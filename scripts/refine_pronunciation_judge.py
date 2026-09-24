#!/usr/bin/env python3
"""Development-only acoustic representation experiments with explicit test reuse."""
import argparse
import hashlib
import json
import gzip
import importlib.metadata
from pathlib import Path

import librosa
import numpy as np

from calibrated_pronunciation_judge import (
    CACHE, CONFIG_PATH, ENGINES, MODEL, REFERENCE, REPORT as FINAL_REPORT, ROOT, TARGETS,
    acceptance_gate, calibrated, choose_threshold, config, dataset, engine_provenance,
    feature_fingerprint, fit_calibration, forest_export, forest_predict, probability_metrics,
    sha256, speaker_bootstrap, stable_json, threshold_feasibility, threshold_metrics, training_data,
)
from pronunciation_features import FEATURE_NAMES, VERSION, load_engines


CONFIG = ROOT / 'config/pronunciation_judge_refinement.json'
OUTPUT = ROOT / '.audit/pronunciation-judge-representations'
REPORT = ROOT / '.audit/pronunciation-judge-refinement-development.json'


class UnscorableRepresentation(ValueError):
    pass


def settings():
    return json.loads(CONFIG.read_text())


def fingerprint():
    return hashlib.sha256(CONFIG.read_bytes() + ENGINES.read_bytes()).hexdigest()


def pooled_representation(matrix, units, frame_seconds, duration):
    if matrix.ndim != 2 or not np.isfinite(matrix).all() or frame_seconds <= 0:
        raise ValueError('Invalid acoustic encoder representation')
    if abs(matrix.shape[0] * frame_seconds - duration) > .15:
        raise ValueError('Encoder frame rate does not match audio duration')
    times = np.arange(matrix.shape[0]) * frame_seconds
    features = []
    for unit in units:
        if not 0 <= unit['start'] < unit['end'] <= duration + .02:
            raise ValueError('Unit interval lies outside its source audio')
        selected = matrix[(times >= unit['start']) & (times < unit['end'])]
        if not len(selected):
            raise UnscorableRepresentation('Annotated pronunciation unit has no encoder frame')
        features.append(np.concatenate([np.mean(selected, axis=0), np.std(selected, axis=0)]))
    result = np.asarray(features, dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError('Non-finite pooled acoustic representation')
    return result


def fit_projection(matrix, components):
    from scipy.linalg import eigh
    from scipy.linalg.blas import dsyrk

    matrix = np.asarray(matrix, dtype=np.float64)
    mean = np.mean(matrix, axis=0)
    scale = np.std(matrix, axis=0)
    scale[scale < 1e-8] = 1
    standardized = (matrix - mean) / scale
    # scipy's explicit BLAS entry avoids NumPy/Accelerate's spurious matmul flags.
    covariance = dsyrk(1 / (len(matrix) - 1), np.asfortranarray(standardized.T), lower=1)
    covariance = np.tril(covariance) + np.tril(covariance, -1).T
    if not np.isfinite(covariance).all():
        raise ValueError('Invalid acoustic covariance')
    values, vectors = eigh(covariance, subset_by_index=(matrix.shape[1] - components, matrix.shape[1] - 1))
    projection = vectors[:, ::-1].T
    return {'mean': mean.tolist(), 'scale': scale.tolist(), 'components': projection.tolist()}


def project(matrix, projection):
    standardized = (np.asarray(matrix, dtype=np.float64) - np.asarray(projection['mean'])) / np.asarray(projection['scale'])
    result = np.einsum('ij,kj->ik', standardized, np.asarray(projection['components']), optimize=False)
    if not np.isfinite(result).all():
        raise ValueError('Invalid projected acoustic representation')
    return result


def reference_arrays(samples, source_rows):
    hashes = {row['utterance']: row['sha256'] for row in source_rows}
    arrays, excluded = {}, {}
    for key in sorted({row['utterance'] for row in samples}):
        with np.load(OUTPUT / (key + '.npz'), allow_pickle=False) as saved:
            if str(saved['fingerprint']) != fingerprint() or str(saved['sha256']) != hashes[key]:
                raise ValueError('Stale acoustic representation')
            if str(saved.get('status', 'measured')) != 'measured':
                excluded[key] = str(saved['reason'])
            else:
                arrays[key] = saved['features']
    return arrays, excluded


def make_forest(candidate):
    from sklearn.ensemble import ExtraTreesClassifier

    if candidate['kind'] == 'baseline_forest':
        parameters = config()['models'][1]
        return ExtraTreesClassifier(**parameters, max_features=.8, class_weight='balanced', random_state=42, n_jobs=2)
    if candidate['kind'] != 'forest':
        raise ValueError('This portable refinement release supports only preselected forest candidates')
    return ExtraTreesClassifier(n_estimators=candidate['trees'], max_depth=candidate['depth'],
                                min_samples_leaf=candidate['minimum_leaf'], random_state=451,
                                class_weight='balanced', n_jobs=2)


def representation(path, units, recognizer, options):
    frames = []
    block = options['encoder_block']
    if len(recognizer.model.encoder.encoders) <= block:
        raise ValueError('Acoustic encoder architecture differs from the configured checkpoint')

    def capture(module, inputs, result):
        states = result[0]
        if not hasattr(states, 'shape') or states.ndim != 3 or states.shape[0] != 1:
            raise ValueError('Unexpected intermediate acoustic states')
        frames.append(states.detach().cpu().numpy()[0])

    from acoustic_analysis import prepare_signal

    payload = path.read_bytes()
    samples, rate = librosa.load(path, sr=16000, mono=True)
    signal = prepare_signal(samples)
    rms = float(np.sqrt(np.mean(signal ** 2)))
    signal = signal * min(.1 / max(rms, 1e-8), .9 / max(float(np.max(np.abs(signal))), 1e-8))
    handle = recognizer.model.encoder.encoders[block].register_forward_hook(capture)
    try:
        recognizer.generate(input=np.ascontiguousarray(signal, dtype=np.float32), fs=rate, disable_pbar=True)
    finally:
        handle.remove()
    if len(frames) != 1 or path.read_bytes() != payload:
        raise ValueError('Acoustic capture is incomplete or source audio changed')
    matrix = frames[0]
    return pooled_representation(matrix, units, options['frame_seconds'], len(samples) / rate)


def prepare(limit=None):
    options = settings()
    rows, _ = dataset()
    cache = {row['utterance']: row for row in map(json.loads, CACHE.read_text().splitlines())}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    recognizer, aligner = load_engines()
    if engine_provenance((recognizer, aligner)) != json.loads(ENGINES.read_text()):
        raise ValueError('Reference engine checkpoint changed')
    candidates = [row for row in rows if cache[row['utterance']]['status'] == 'measured']
    completed = 0
    for row in candidates:
        path = OUTPUT / (row['utterance'] + '.npz')
        if path.is_file():
            with np.load(path, allow_pickle=False) as saved:
                if str(saved['fingerprint']) == fingerprint() and str(saved['sha256']) == row['sha256']:
                    continue
        source = REFERENCE / row['path']
        if hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Reference recording changed')
        try:
            values = representation(source, cache[row['utterance']]['units'], recognizer, options)
            status, reason = 'measured', ''
        except UnscorableRepresentation as error:
            values = np.empty((0, 1024), dtype=np.float32)
            status, reason = 'unscorable', str(error)
            print(f'{row["utterance"]}: unscorable ({reason})', flush=True)
        temporary = path.with_suffix('.npz.part')
        with temporary.open('wb') as output:
            np.savez_compressed(output, features=values, fingerprint=fingerprint(), sha256=row['sha256'],
                                status=status, reason=reason)
        temporary.replace(path)
        completed += 1
        if completed % 100 == 0:
            print(f'Acoustic representations: {completed} newly extracted', flush=True)
        if limit and completed >= limit:
            break
    print(f'Extracted {completed} new reference representations; no test metrics examined.')


def development():
    from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier

    samples, source_rows, _, _ = training_data()
    hashes = {row['utterance']: row['sha256'] for row in source_rows}
    options = settings()
    # This command cannot inspect test, calibration, or threshold outcomes.
    selected = [sample for sample in samples if sample['split'] in ('train', 'select')]
    arrays = {}
    excluded = {}
    for row in selected:
        key = row['utterance']
        if key not in arrays:
            with np.load(OUTPUT / (key + '.npz'), allow_pickle=False) as saved:
                if str(saved['fingerprint']) != fingerprint() or str(saved['sha256']) != hashes[key]:
                    raise ValueError('Stale acoustic representation')
                if str(saved.get('status', 'measured')) != 'measured':
                    excluded[key] = str(saved['reason'])
                    continue
                arrays[key] = saved['features']
    rows = {name: [row for row in selected if row['split'] == name and row['utterance'] not in excluded]
            for name in ('train', 'select')}
    encoded = {name: np.asarray([arrays[row['utterance']][row['unit']] for row in values]) for name, values in rows.items()}
    projection = fit_projection(encoded['train'], options['pca_components'])
    matrix = {
        name: np.concatenate([np.asarray([row['features'] for row in values]),
                              project(encoded[name], projection)], axis=1)
        for name, values in rows.items()
    }
    results = []
    for target in TARGETS:
        gold = {name: np.array([row['labels'][target] for row in values]) for name, values in rows.items()}
        for candidate in options['candidates']:
            if candidate['kind'] == 'boost':
                model = HistGradientBoostingClassifier(
                    max_depth=candidate['depth'], max_iter=candidate['iterations'],
                    learning_rate=candidate['learning_rate'], max_leaf_nodes=31, min_samples_leaf=25,
                    l2_regularization=3, early_stopping=False, random_state=451,
                )
            else:
                model = ExtraTreesClassifier(n_estimators=candidate['trees'], max_depth=candidate['depth'],
                                             min_samples_leaf=candidate['minimum_leaf'], random_state=451,
                                             class_weight='balanced', n_jobs=2)
            model.fit(matrix['train'], gold['train'])
            probability = model.predict_proba(matrix['select'])[:, 1]
            metrics = probability_metrics(gold['select'], probability)
            result = {'target': target, 'candidate': candidate, 'development': metrics}
            results.append(result)
            print(json.dumps({'target': target, 'model': candidate,
                              'development_auc': metrics['auroc_correctness'], 'development_brier': metrics['brier']}), flush=True)
    # Existing-feature controls distinguish a representation improvement from model choice.
    for target in TARGETS:
        y = np.array([row['labels'][target] for row in rows['train']])
        labels = np.array([row['labels'][target] for row in rows['select']])
        raw_train = np.asarray([row['features'] for row in rows['train']])
        raw_select = np.asarray([row['features'] for row in rows['select']])
        control = HistGradientBoostingClassifier(max_depth=6, max_iter=250, learning_rate=.05,
                                                 max_leaf_nodes=31, min_samples_leaf=25,
                                                 l2_regularization=3, early_stopping=False, random_state=451)
        control.fit(raw_train, y)
        metrics = probability_metrics(labels, control.predict_proba(raw_select)[:, 1])
        results.append({'target': target, 'candidate': {'kind': 'existing_feature_control'}, 'development': metrics})
    for target in TARGETS:
        baseline = make_forest({'kind': 'baseline_forest'})
        baseline.fit(np.asarray([row['features'] for row in rows['train']], dtype=np.float32),
                     np.asarray([row['labels'][target] for row in rows['train']]))
        scores = baseline.predict_proba(np.asarray([row['features'] for row in rows['select']], dtype=np.float32))[:, 1]
        metrics = probability_metrics([row['labels'][target] for row in rows['select']], scores)
        results.append({'target': target, 'candidate': {'kind': 'baseline_forest'}, 'development': metrics})
    REPORT.write_text(json.dumps({'feature_fingerprint': fingerprint(), 'results': results,
                                 'unscorable_utterances': excluded,
                                 'test_examined': False}, indent=2) + '\n')


def train():
    options, policy = settings(), config()
    development_report = json.loads(REPORT.read_text())
    if development_report['test_examined'] is not False or development_report['feature_fingerprint'] != fingerprint():
        raise ValueError('Development selection evidence is missing or stale')
    development_payload = (json.dumps(development_report, ensure_ascii=False, indent=2) + '\n').encode()
    samples, utterances, coverage, manifest = training_data()
    arrays, excluded = reference_arrays(samples, utterances)
    partitions = {name: [row for row in samples if row['split'] == name and row['utterance'] not in excluded]
                  for name in ('train', 'select', 'calibrate', 'threshold', 'test')}
    for name, rows in partitions.items():
        omitted = coverage[name]['measured_units'] - len(rows)
        coverage[name]['unscorable_units'] += omitted
        coverage[name]['unscorable_utterances'] += sum(
            row['split'] == name and row['utterance'] in excluded for row in utterances
        )
        coverage[name]['measured_units'] = len(rows)
    encoded = {name: np.asarray([arrays[row['utterance']][row['unit']] for row in rows])
               for name, rows in partitions.items()}
    projection = fit_projection(encoded['train'], options['pca_components'])
    raw = {name: np.asarray([row['features'] for row in rows], dtype=np.float32) for name, rows in partitions.items()}
    matrices = {name: np.concatenate([raw[name], project(encoded[name], projection)], axis=1).astype(np.float32)
                for name in partitions}
    engine_manifest = json.loads(ENGINES.read_text())
    artifact = {
        'version': 1, 'model_variant': 'acoustic-encoder-refinement-v2',
        'feature_version': VERSION, 'feature_names': FEATURE_NAMES,
        'feature_fingerprint': feature_fingerprint(),
        'trainer_sha256': sha256((ROOT / 'scripts/calibrated_pronunciation_judge.py').read_bytes()),
        'refinement_sha256': sha256(Path(__file__).read_bytes()),
        'config_sha256': sha256(CONFIG_PATH.read_bytes()),
        'reference_manifest_sha256': sha256((ROOT / 'data/pronunciation_judge_reference.json').read_bytes()),
        'reference_revision': manifest['revision'], 'engine_provenance': engine_manifest,
        'scope': policy['production_scope'], 'certifies_accuracy': False,
        'supported_protocol': 'connected read Mandarin speech with a supplied transcript',
        'reference_sentences': sorted({row['text'] for row in utterances}),
        'representation': {'settings': options, 'fingerprint': fingerprint(), 'projection': projection},
        'development_report_sha256': sha256(development_payload),
        'heads': {},
    }
    lower, upper = np.quantile(matrices['train'], [.001, .999], axis=0)
    width = np.maximum(upper - lower, 1e-5)
    artifact['feature_support'] = {'lower': (lower - .5 * width).tolist(), 'upper': (upper + .5 * width).tolist()}
    report = {
        'version': 2, 'reference': policy['reference'], 'config': policy, 'refinement_config': options,
        'trainer_sha256': artifact['trainer_sha256'], 'refinement_sha256': artifact['refinement_sha256'],
        'measurement_cache_sha256': sha256(CACHE.read_bytes()), 'engine_provenance': engine_manifest,
        'training_packages': {'scikit-learn': importlib.metadata.version('scikit-learn'), 'scipy': importlib.metadata.version('scipy')},
        'split_unit': 'speaker', 'coverage': coverage, 'results': {},
        'speaker_splits': {name: sorted({row['speaker'] for row in utterances if row['split'] == name}) for name in partitions},
        'test_status': options['test_status'],
        'selection_policy': 'Feature/model choices used only train/select; calibration and threshold speakers remained separate. The previously reported test set is reused for transparency, not represented as a new external test.',
        'text_overlap_caveat': 'Sentences recur across speakers. Native isolated recordings and other accents remain unvalidated.',
        'human_label_policy': policy['label_policy'], 'certifies_accuracy': False,
        'domain_transfer_verified': False, 'automatic_practice_admission_enabled': False,
        'representation_failures': excluded,
    }
    measurements = {row['utterance']: row for row in map(json.loads, CACHE.read_text().splitlines())}
    report['unscorable_utterances'] = [
        {'utterance': row['utterance'], 'speaker': row['speaker'], 'split': row['split'],
         'annotated_units': len(row['units']),
         'reason': excluded.get(row['utterance']) or measurements[row['utterance']].get('reason')}
        for row in utterances
        if row['utterance'] in excluded or measurements[row['utterance']]['status'] != 'measured'
    ]
    for target in TARGETS:
        choices = [row for row in development_report['results'] if row['target'] == target]
        selected = max(choices, key=lambda row: row['development']['auroc_correctness'])
        candidate = selected['candidate']
        classifier = make_forest(candidate)
        inputs = raw if candidate['kind'] == 'baseline_forest' else matrices
        labels = {name: np.asarray([row['labels'][target] for row in rows]) for name, rows in partitions.items()}
        classifier.fit(inputs['train'], labels['train'])
        probabilities = {name: classifier.predict_proba(value)[:, 1] for name, value in inputs.items()}
        calibrator = fit_calibration(probabilities['calibrate'], labels['calibrate'])
        adjusted = {name: calibrated(value, calibrator) for name, value in probabilities.items()}
        accept = choose_threshold(labels['threshold'], adjusted['threshold'], policy['decision'], 'accept')
        reject = choose_threshold(labels['threshold'], adjusted['threshold'], policy['decision'], 'reject')
        quality = [row['voiced_frames'] >= 8 for row in partitions['test']]
        selective = threshold_metrics(labels['test'], adjusted['test'], accept, reject, quality)
        bootstrap = speaker_bootstrap(partitions['test'], adjusted['test'], target, accept)
        gate = acceptance_gate(selective, bootstrap, int(np.sum(labels['test'] == 0)), policy['decision'])
        forest = forest_export(classifier)
        if not np.allclose(forest_predict(forest, inputs['test']), probabilities['test'], atol=1e-12):
            raise ValueError('Portable refinement prediction mismatch')
        artifact['heads'][target] = {
            'forest': forest, 'input_features': inputs['train'].shape[1],
            'calibration': calibrator, 'accept_threshold': accept, 'reject_threshold': reject,
            'held_out_acceptance_gate_passed': gate,
        }
        per_tone = {}
        for tone in range(1, 6):
            indices = np.array([index for index, row in enumerate(partitions['test']) if row['expected_tones'] == [tone]], dtype=int)
            per_tone[str(tone)] = probability_metrics(labels['test'][indices], adjusted['test'][indices])
        unanimous = [index for index, row in enumerate(partitions['test']) if row['votes'] and len(set(row['votes'][target])) == 1]
        report['results'][target] = {
            'selected_model': candidate, 'development_auroc': selected['development']['auroc_correctness'],
            'calibrator': calibrator, 'test_uncalibrated': probability_metrics(labels['test'], probabilities['test']),
            'test_calibrated': probability_metrics(labels['test'], adjusted['test']),
            'test_constant_prior_baseline': probability_metrics(labels['test'], np.full(len(labels['test']), np.mean(labels['train']))),
            'test_selective_decisions': selective, 'threshold_selection': {'accept': accept, 'reject': reject},
            'threshold_data_support': threshold_feasibility(labels['threshold'], policy['decision']),
            'test_end_to_end_coverage': {
                'all_annotated_units': coverage['test']['annotated_units'],
                'unscorable_units': coverage['test']['unscorable_units'],
                'decided_units': selective['accepted'] + selective['rejected'],
                'coverage': (selective['accepted'] + selective['rejected']) / coverage['test']['annotated_units'],
                'unscorable_policy': 'abstain, not pass',
            },
            'test_per_expected_tone': per_tone, 'speaker_bootstrap': bootstrap,
            'test_unanimous_raters': probability_metrics(labels['test'][unanimous], adjusted['test'][unanimous]),
            'held_out_acceptance_gate_passed': gate,
        }
        print(target, json.dumps({'auc': report['results'][target]['test_calibrated']['auroc_correctness'],
                                  'brier': report['results'][target]['test_calibrated']['brier'],
                                  'ece': report['results'][target]['test_calibrated']['ece_10_bins'],
                                  'acceptance_gate': gate, 'selective': selective}), flush=True)
    split_manifest = json.loads((ROOT / 'data/pronunciation_judge_splits.json').read_text())
    artifact['split_manifest_sha256'] = sha256(stable_json(split_manifest).encode())
    artifact['evaluation_sha256'] = sha256(stable_json(report).encode())
    (ROOT / 'data/pronunciation_judge_development.json').write_bytes(development_payload)
    MODEL.write_bytes(gzip.compress(stable_json(artifact).encode(), mtime=0))
    report['model_sha256'] = sha256(MODEL.read_bytes())
    FINAL_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Published the development-selected diagnostic refinement; automatic practice admission remains disabled.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'development', 'train'])
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.limit)
    elif args.command == 'development':
        development()
    else:
        train()


if __name__ == '__main__':
    main()
