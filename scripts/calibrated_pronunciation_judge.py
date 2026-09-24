#!/usr/bin/env python3
"""Fit, calibrate, evaluate, and run a human-label-trained pronunciation judge."""
import argparse
import gzip
import hashlib
import json
import math
import importlib.metadata
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.special import expit, logit
from scipy.stats import norm
from scipy.stats import rankdata

from pronunciation_features import FEATURE_NAMES, VERSION, analyze, load_engines, transcript_characters


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'config/pronunciation_judge.json'
REFERENCE = ROOT / 'imports/pronunciation_judge/ompal'
CACHE = ROOT / '.audit/pronunciation-judge-features.jsonl'
MODEL = ROOT / 'data/pronunciation_judge_model.json.gz'
REPORT = ROOT / 'data/pronunciation_judge_evaluation.json'
TARGETS = ('tone', 'phoneme_consonant', 'phoneme_vowel')
ENGINES = ROOT / '.audit/pronunciation-judge-engines.json'


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def config():
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


def feature_fingerprint():
    return sha256(b''.join((ROOT / 'scripts' / name).read_bytes()
                          for name in ('pronunciation_features.py', 'acoustic_analysis.py')))


def engine_provenance(engines):
    result = {}
    for alias, engine in zip(('paraformer-zh', 'fa-zh'), engines):
        checkpoint = engine.kwargs.get('init_param')
        if not isinstance(checkpoint, str) or not Path(checkpoint).is_file():
            raise ValueError(f'Cannot identify checkpoint bytes for {alias}')
        directory = Path(checkpoint).parent
        names = [Path(checkpoint).name, 'config.yaml', 'configuration.json', 'tokens.json', 'seg_dict', 'am.mvn']
        result[alias] = {name: sha256((directory / name).read_bytes()) for name in names if (directory / name).is_file()}
    result['packages'] = {
        name: importlib.metadata.version(name)
        for name in ('funasr', 'torch', 'numpy', 'librosa', 'praat-parselmouth', 'pypinyin', 'opencc-python-reimplemented')
    }
    return result


def split_speakers(speakers, settings):
    """Split complete speakers before feature inspection; no gold-label balancing."""
    assignments = {}
    for native, counts in ((True, settings['native_counts']), (False, settings['learner_counts'])):
        group = [speaker for speaker in speakers if speaker.startswith('SPEAKER01') == native]
        if len(group) != sum(counts.values()):
            raise ValueError(f'Unexpected number of {"native" if native else "learner"} speakers')
        ordered = sorted(group, key=lambda speaker: sha256((settings['seed'] + speaker).encode()))
        offset = 0
        for partition, count in counts.items():
            for speaker in ordered[offset:offset + count]:
                assignments[speaker] = partition
            offset += count
    return assignments


def dataset():
    settings = config()
    manifest = json.loads((ROOT / 'data/pronunciation_judge_reference.json').read_text())
    reference = settings['reference']
    if manifest['revision'] != reference['revision'] or manifest['repository'] != reference['repository']:
        raise ValueError('Reference manifest does not match the configured source')
    files = {item['path']: item for item in manifest['files']}
    scores = {}
    for filename in ('native_scores.json', 'non-native_scores.json'):
        payload = (REFERENCE / filename).read_bytes()
        if sha256(payload) != files[filename]['sha256']:
            raise ValueError('Human annotation snapshot changed')
        entries = json.loads(payload)
        if set(entries) & set(scores):
            raise ValueError('Duplicate reference utterance IDs')
        scores.update(entries)
    detail_payload = (REFERENCE / 'non-native_scores-detail.json').read_bytes()
    if sha256(detail_payload) != files['non-native_scores-detail.json']['sha256']:
        raise ValueError('Individual-rater snapshot changed')
    details = json.loads(detail_payload)
    speakers = {Path(item['path']).parts[1] for item in files.values() if item['path'].endswith('.wav')}
    if len(speakers) != reference['expected_speakers']:
        raise ValueError('Reference speaker inventory changed')
    assignments = split_speakers(speakers, settings['split'])
    rows = []
    for relative, item in sorted(files.items()):
        if not relative.endswith('.wav'):
            continue
        utterance = Path(relative).stem
        speaker = Path(relative).parts[1]
        label = scores.get(utterance)
        if label is None:
            raise ValueError(f'Reference recording has no expert annotation: {utterance}')
        units = []
        offset = 0
        for position, word in enumerate(label['words']):
            text = ''.join(word['text'])
            if any(word[target] not in (0, 1) for target in TARGETS):
                raise ValueError('Expected published binary expert judgments')
            votes = details.get(utterance, {}).get('words', [])
            raw_votes = votes[position] if votes else None
            if raw_votes and raw_votes['text'] != word['text']:
                raise ValueError('Aggregate and individual-rater labels do not align')
            units.append({
                'text': text, 'span': [offset, offset + len(text)],
                'labels': {target: int(word[target]) for target in TARGETS},
                'votes': {target: [int(value) for value in raw_votes[target]] for target in TARGETS} if raw_votes else None,
            })
            offset += len(text)
        annotation_issue = (
            'expert annotation units do not reconstruct the reference transcript'
            if ''.join(unit['text'] for unit in units) != label['text'] else None
        )
        rows.append({
            'utterance': utterance, 'speaker': speaker, 'split': assignments[speaker],
            'path': relative, 'sha256': item['sha256'], 'text': label['text'], 'units': units,
            'annotation_issue': annotation_issue,
        })
    # Exact recording reuse cannot cross split boundaries.
    seen = {}
    for row in rows:
        previous = seen.setdefault(row['sha256'], row['split'])
        if previous != row['split']:
            raise ValueError('Identical audio crosses evaluation partitions')
    return rows, manifest


def cache_rows():
    if not CACHE.exists():
        return {}
    rows = {}
    for number, line in enumerate(CACHE.read_text(encoding='utf-8').splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f'Incomplete feature cache at line {number}; repair before resuming') from error
        rows[row['utterance']] = row
    return rows


def prepare(limit=None):
    rows, manifest = dataset()
    fingerprint = feature_fingerprint()
    completed = cache_rows()
    pending = [row for row in rows if (
        completed.get(row['utterance'], {}).get('sha256') != row['sha256']
        or completed.get(row['utterance'], {}).get('feature_fingerprint') != fingerprint
        or completed.get(row['utterance'], {}).get('reference_text') != row['text']
    )]
    if limit is not None:
        pending = pending[:limit]
    engines = load_engines()
    provenance = engine_provenance(engines)
    if ENGINES.is_file() and json.loads(ENGINES.read_text()) != provenance:
        raise ValueError('ASR/alignment checkpoints changed; use a new feature cache and evaluation')
    ENGINES.parent.mkdir(parents=True, exist_ok=True)
    ENGINES.write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open('a', encoding='utf-8') as output:
        for number, row in enumerate(pending, 1):
            path = REFERENCE / row['path']
            if sha256(path.read_bytes()) != row['sha256']:
                raise ValueError(f'Reference audio changed: {row["path"]}')
            try:
                if row['annotation_issue']:
                    raise ValueError(row['annotation_issue'])
                result = analyze(path, row['text'], *engines, units=[unit['span'] for unit in row['units']])
                value = {'status': 'measured', **result}
            except ValueError as error:
                # Data/measurement failures are explicitly recorded and included in coverage.
                value = {'status': 'unscorable', 'reason': str(error)}
            value.update({
                'utterance': row['utterance'], 'reference_text': row['text'],
                'sha256': row['sha256'], 'feature_fingerprint': fingerprint,
            })
            output.write(stable_json(value) + '\n')
            output.flush()
            if number % 100 == 0 or number == len(pending):
                print(f'Judge reference features: {number}/{len(pending)}', flush=True)
    print('No existing practice decisions or heuristic labels were used as targets.')


def training_data():
    rows, manifest = dataset()
    features = cache_rows()
    fingerprint = feature_fingerprint()
    samples, coverage = [], defaultdict(Counter)
    for row in rows:
        measured = features.get(row['utterance'])
        if not measured or measured.get('sha256') != row['sha256'] or measured.get('feature_fingerprint') != fingerprint:
            raise ValueError('Missing or stale reference features; complete the prepare phase')
        coverage[row['split']]['utterances'] += 1
        coverage[row['split']]['annotated_units'] += len(row['units'])
        if measured['status'] != 'measured':
            coverage[row['split']]['unscorable_utterances'] += 1
            coverage[row['split']]['unscorable_units'] += len(row['units'])
            continue
        if len(measured['units']) != len(row['units']):
            raise ValueError('Features and human annotations have different unit counts')
        for position, (unit, feature) in enumerate(zip(row['units'], measured['units'])):
            if [feature['character_start'], feature['character_end']] != unit['span']:
                raise ValueError('Feature unit is not the expert-scored text span')
            samples.append({
                **feature, 'utterance': row['utterance'], 'speaker': row['speaker'],
                'split': row['split'], 'unit': position, 'labels': unit['labels'], 'votes': unit['votes'],
            })
            coverage[row['split']]['measured_units'] += 1
    return samples, rows, dict(coverage), manifest


def probability_metrics(labels, probabilities):
    labels, probabilities = np.asarray(labels), np.asarray(probabilities)
    if labels.shape != probabilities.shape or labels.ndim != 1:
        raise ValueError('Labels and probabilities must be aligned one-dimensional arrays')
    if not np.isin(labels, [0, 1]).all() or not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise ValueError('Invalid binary labels or probabilities')
    if not len(labels):
        return {'n': 0}
    clipped = np.clip(probabilities, 1e-7, 1 - 1e-7)
    bins, ece = [], 0.0
    for lower, upper in zip(np.linspace(0, 1, 11)[:-1], np.linspace(0, 1, 11)[1:]):
        included = (probabilities >= lower) & ((probabilities < upper) if upper < 1 else (probabilities <= upper))
        if not included.any():
            continue
        confidence, accuracy = float(np.mean(probabilities[included])), float(np.mean(labels[included]))
        count = int(included.sum())
        bins.append({'lower': float(lower), 'upper': float(upper), 'n': count,
                     'predicted_correct': confidence, 'human_correct_fraction': accuracy})
        ece += count / len(labels) * abs(confidence - accuracy)
    predicted = probabilities >= .5
    tn = int(np.sum((labels == 0) & ~predicted))
    fp = int(np.sum((labels == 0) & predicted))
    fn = int(np.sum((labels == 1) & ~predicted))
    tp = int(np.sum((labels == 1) & predicted))
    positives, negatives = int(np.sum(labels == 1)), int(np.sum(labels == 0))
    ranks = rankdata(probabilities)
    auc = (float(np.sum(ranks[labels == 1])) - positives * (positives + 1) / 2) / (positives * negatives) if positives and negatives else None
    return {
        'n': len(labels), 'human_incorrect': int(np.sum(labels == 0)), 'human_correct': int(np.sum(labels == 1)),
        'brier': float(np.mean((probabilities - labels) ** 2)),
        'log_loss': float(-np.mean(labels * np.log(clipped) + (1 - labels) * np.log(1 - clipped))),
        'ece_10_bins': float(ece), 'accuracy_at_0_5': float(np.mean(predicted == labels)),
        'balanced_accuracy_at_0_5': .5 * (tp / max(tp + fn, 1) + tn / max(tn + fp, 1)),
        'auroc_correctness': auc,
        'confusion_at_0_5': {'true_negative': tn, 'false_accept': fp, 'false_reject': fn, 'true_positive': tp},
        'reliability_bins': bins,
    }


def binomial_upper(errors, total, confidence=.95):
    if not total:
        return 1.0
    z = float(norm.ppf(confidence))
    rate = errors / total
    return (rate + z*z/(2*total) + z*math.sqrt(rate*(1-rate)/total + z*z/(4*total*total))) / (1 + z*z/total)


def choose_threshold(labels, probabilities, confidence, decision):
    """Choose on a dedicated partition, with simultaneous error-bound correction."""
    labels, probabilities = np.asarray(labels), np.asarray(probabilities)
    if decision not in ('accept', 'reject'):
        raise ValueError('Threshold decision must be accept or reject')
    error_label = 0 if decision == 'accept' else 1
    alpha = 1 - confidence['confidence']
    corrected = 1 - alpha / (4 * len(confidence['threshold_grid']))
    options = []
    for threshold in confidence['threshold_grid']:
        selected = probabilities >= threshold if decision == 'accept' else probabilities <= 1 - threshold
        count = int(selected.sum())
        errors = int(np.sum(labels[selected] == error_label))
        upper = binomial_upper(errors, count, corrected)
        error_support = int(np.sum(labels == error_label))
        conditional_upper = binomial_upper(errors, error_support, corrected)
        if (count >= confidence['minimum_decisions'] and upper <= confidence['maximum_error_rate']
                and conditional_upper <= confidence['maximum_error_rate']):
            options.append({'confidence': threshold, 'selected': count, 'errors': errors,
                            'simultaneous_error_upper_bound': upper,
                            'error_class_support': error_support,
                            'simultaneous_error_class_rate_upper_bound': conditional_upper})
    return max(options, key=lambda option: option['selected']) if options else None


def threshold_metrics(labels, probabilities, accept, reject, quality=None):
    labels, probabilities = np.asarray(labels), np.asarray(probabilities)
    quality = np.ones(len(labels), dtype=bool) if quality is None else np.asarray(quality, dtype=bool)
    yes = quality & (probabilities >= accept['confidence']) if accept else np.zeros(len(labels), dtype=bool)
    no = quality & (probabilities <= 1 - reject['confidence']) if reject else np.zeros(len(labels), dtype=bool)
    if (yes & no).any():
        raise ValueError('Accept and reject thresholds overlap')
    fa = int(np.sum(yes & (labels == 0)))
    fr = int(np.sum(no & (labels == 1)))
    return {
        'n': len(labels), 'accepted': int(yes.sum()), 'rejected': int(no.sum()),
        'uncertain': int((~yes & ~no).sum()), 'coverage': float(np.mean(yes | no)),
        'false_accepts': fa, 'false_rejects': fr,
        'false_accept_rate_among_incorrect': fa / max(1, int(np.sum(labels == 0))),
        'false_reject_rate_among_correct': fr / max(1, int(np.sum(labels == 1))),
        'accepted_error_rate': fa / int(yes.sum()) if yes.any() else None,
        'rejected_error_rate': fr / int(no.sum()) if no.any() else None,
        'accepted_error_upper_95': binomial_upper(fa, int(yes.sum())),
        'rejected_error_upper_95': binomial_upper(fr, int(no.sum())),
    }


def forest_export(model):
    trees = []
    for estimator in model.estimators_:
        tree = estimator.tree_
        values = tree.value[:, 0, :]
        positive = values[:, 1] / np.maximum(values.sum(axis=1), 1e-12)
        trees.append({
            'left': tree.children_left.tolist(), 'right': tree.children_right.tolist(),
            'feature': tree.feature.tolist(), 'threshold': tree.threshold.tolist(),
            'probability': positive.tolist(),
        })
    return trees


def forest_predict(trees, matrix):
    matrix = np.asarray(matrix, dtype=np.float32)
    probabilities = np.zeros(len(matrix))
    for tree in trees:
        nodes = np.zeros(len(matrix), dtype=int)
        left, right = np.array(tree['left']), np.array(tree['right'])
        feature, threshold = np.array(tree['feature']), np.array(tree['threshold'])
        active = left[nodes] >= 0
        while active.any():
            positions = np.flatnonzero(active)
            current = nodes[positions]
            go_left = matrix[positions, feature[current]] <= threshold[current]
            nodes[positions] = np.where(go_left, left[current], right[current])
            active = left[nodes] >= 0
        probabilities += np.array(tree['probability'])[nodes]
    return probabilities / len(trees)


def calibrated(raw, parameters):
    return expit(parameters['coefficient'] * logit(np.clip(raw, 1e-6, 1 - 1e-6)) + parameters['intercept'])


def fit_calibration(raw, labels):
    """Stable two-parameter Platt fit; avoid platform-dependent BLAS matmul warnings."""
    scores = logit(np.clip(np.asarray(raw, dtype=float), 1e-6, 1 - 1e-6))
    labels = np.asarray(labels, dtype=float)
    if scores.shape != labels.shape or not np.isfinite(scores).all() or set(labels) != {0.0, 1.0}:
        raise ValueError('Calibration needs finite scores and both human-label classes')

    def objective(parameters):
        slope, intercept = parameters
        logits = slope * scores + intercept
        probability = expit(logits)
        residual = probability - labels
        loss = float(np.sum(np.logaddexp(0, logits) - labels * logits) + .5 * slope * slope)
        gradient = (float(np.sum(residual * scores) + slope), float(np.sum(residual)))
        weight = probability * (1 - probability)
        hessian = (float(np.sum(weight * scores * scores) + 1),
                   float(np.sum(weight * scores)), float(np.sum(weight)))
        return loss, gradient, hessian

    parameters = (1.0, float(logit(np.mean(labels))))
    for _ in range(200):
        loss, gradient, (a, b, c) = objective(parameters)
        if max(abs(gradient[0]), abs(gradient[1])) < 1e-7:
            return {'coefficient': parameters[0], 'intercept': parameters[1]}
        determinant = a * c - b * b
        if determinant <= 0:
            raise ValueError('Singular probability-calibration Hessian')
        step = ((c * gradient[0] - b * gradient[1]) / determinant,
                (a * gradient[1] - b * gradient[0]) / determinant)
        factor = 1.0
        for _ in range(60):
            candidate = (max(0.0, parameters[0] - factor * step[0]),
                         parameters[1] - factor * step[1])
            candidate_loss = objective(candidate)[0]
            if candidate_loss <= loss + 1e-10:
                break
            factor *= .5
        if max(abs(candidate[i] - parameters[i]) for i in range(2)) < 1e-10:
            if max(abs(gradient[0]), abs(gradient[1])) < 1e-4:
                return {'coefficient': candidate[0], 'intercept': candidate[1]}
            raise ValueError('Probability-calibration optimizer stalled before convergence')
        parameters = candidate
    raise ValueError('Probability calibration exceeded its iteration limit')


def speaker_bootstrap(samples, probabilities, target, acceptance=None):
    speakers = sorted({sample['speaker'] for sample in samples})
    by_speaker = {speaker: np.array([i for i, sample in enumerate(samples) if sample['speaker'] == speaker]) for speaker in speakers}
    labels = np.array([sample['labels'][target] for sample in samples])
    random = np.random.default_rng(90210)
    briers = []
    acceptance_errors = []
    false_accept_rates = []
    for _ in range(500):
        indices = np.concatenate([by_speaker[speaker] for speaker in random.choice(speakers, len(speakers), replace=True)])
        briers.append(float(np.mean((probabilities[indices] - labels[indices]) ** 2)))
        if acceptance:
            chosen = probabilities[indices] >= acceptance['confidence']
            if chosen.any():
                acceptance_errors.append(float(np.mean(labels[indices][chosen] == 0)))
            incorrect = labels[indices] == 0
            if incorrect.any():
                false_accept_rates.append(float(np.mean(chosen[incorrect])))
    return {'unit': 'speaker', 'resamples': 500, 'speakers': len(speakers),
            'brier_95_interval': np.quantile(briers, [.025, .975]).tolist(),
            'accepted_error_upper_95': float(np.quantile(acceptance_errors, .95)) if acceptance_errors else None,
            'false_accept_rate_upper_95': float(np.quantile(false_accept_rates, .95)) if false_accept_rates else None}


def acceptance_gate(selective, bootstrap, incorrect_support, policy):
    return bool(
        selective['accepted'] >= policy['minimum_test_decisions']
        and selective['accepted_error_upper_95'] <= policy['maximum_error_rate']
        and binomial_upper(selective['false_accepts'], incorrect_support) <= policy['maximum_error_rate']
        and bootstrap['accepted_error_upper_95'] is not None
        and bootstrap['accepted_error_upper_95'] <= policy['maximum_error_rate']
        and bootstrap['false_accept_rate_upper_95'] is not None
        and bootstrap['false_accept_rate_upper_95'] <= policy['maximum_error_rate']
    )


def train():
    from sklearn.ensemble import ExtraTreesClassifier

    settings = config()
    samples, utterances, coverage, manifest = training_data()
    partitions = {name: [row for row in samples if row['split'] == name]
                  for name in ('train', 'select', 'calibrate', 'threshold', 'test')}
    arrays = {name: np.asarray([row['features'] for row in rows], dtype=np.float32) for name, rows in partitions.items()}
    if any(not len(rows) for rows in partitions.values()):
        raise ValueError('Every independent partition must contain usable measurements')
    artifact = {
        'version': 1, 'feature_version': VERSION, 'feature_fingerprint': feature_fingerprint(),
        'trainer_sha256': sha256(Path(__file__).read_bytes()),
        'feature_names': FEATURE_NAMES, 'config_sha256': sha256(CONFIG_PATH.read_bytes()),
        'reference_manifest_sha256': sha256((ROOT / 'data/pronunciation_judge_reference.json').read_bytes()),
        'reference_revision': manifest['revision'], 'heads': {},
        'engine_provenance': json.loads(ENGINES.read_text(encoding='utf-8')),
        'scope': settings['production_scope'], 'certifies_accuracy': False,
        'supported_protocol': 'connected read Mandarin speech with a supplied transcript',
        'unvalidated_domains': ['isolated syllables', 'other accents', 'app corpora', 'background noise', 'spontaneous speech'],
    }
    report = {
        'version': 1, 'reference': settings['reference'], 'config': settings,
        'trainer_sha256': artifact['trainer_sha256'],
        'measurement_cache_sha256': sha256(CACHE.read_bytes()),
        'split_unit': 'speaker', 'coverage': coverage, 'results': {},
        'speaker_splits': {name: sorted({row['speaker'] for row in utterances if row['split'] == name}) for name in partitions},
        'text_overlap_caveat': 'Reference sentences can recur across speakers. This measures unseen-speaker performance on the corpus protocol, not unseen text or native isolated-word transfer.',
        'human_label_policy': settings['label_policy'], 'certifies_accuracy': False,
        'engine_provenance': artifact['engine_provenance'],
        'training_packages': {'scikit-learn': importlib.metadata.version('scikit-learn'), 'scipy': importlib.metadata.version('scipy')},
    }
    lower = np.quantile(arrays['train'], .001, axis=0)
    upper = np.quantile(arrays['train'], .999, axis=0)
    width = np.maximum(upper - lower, 1e-5)
    artifact['feature_support'] = {'lower': (lower - width * .5).tolist(), 'upper': (upper + width * .5).tolist()}
    for target in TARGETS:
        labels = {name: np.asarray([row['labels'][target] for row in rows], dtype=int) for name, rows in partitions.items()}
        if any(len(np.unique(labels[name])) < 2 for name in ('train', 'select', 'calibrate', 'threshold', 'test')):
            raise ValueError(f'{target} lacks both human-label classes in a split')
        options = []
        for parameters in settings['models']:
            model = ExtraTreesClassifier(
                **parameters, max_features=.8, class_weight='balanced', random_state=42, n_jobs=2,
            )
            model.fit(arrays['train'], labels['train'])
            development = model.predict_proba(arrays['select'])[:, 1]
            options.append((probability_metrics(labels['select'], development)['log_loss'], parameters, model))
        loss, parameters, model = min(options, key=lambda item: item[0])
        raw = {name: model.predict_proba(matrix)[:, 1] for name, matrix in arrays.items()}
        calibrator = fit_calibration(raw['calibrate'], labels['calibrate'])
        if calibrator['coefficient'] <= 0:
            raise ValueError(f'Non-monotonic calibration for {target}; the learned score is not reliable')
        probabilities = {name: calibrated(value, calibrator) for name, value in raw.items()}
        acceptance = choose_threshold(labels['threshold'], probabilities['threshold'], settings['decision'], 'accept')
        rejection = choose_threshold(labels['threshold'], probabilities['threshold'], settings['decision'], 'reject')
        trees = forest_export(model)
        portable = forest_predict(trees, arrays['test'])
        if not np.allclose(portable, raw['test'], atol=1e-12):
            raise ValueError('Portable model predictions disagree with training implementation')
        quality = [sample['voiced_frames'] >= 8 for sample in partitions['test']]
        selective = threshold_metrics(labels['test'], probabilities['test'], acceptance, rejection, quality)
        bootstrap = speaker_bootstrap(partitions['test'], probabilities['test'], target, acceptance)
        validation_pass = acceptance_gate(
            selective, bootstrap, int(np.sum(labels['test'] == 0)), settings['decision'],
        )
        artifact['heads'][target] = {
            'forest': trees, 'calibration': calibrator,
            'accept_threshold': acceptance, 'reject_threshold': rejection,
            'held_out_acceptance_gate_passed': bool(validation_pass),
        }
        baseline = np.full(len(labels['test']), np.mean(labels['train']))
        per_tone = {}
        for tone in range(1, 6):
            indices = np.array([i for i, row in enumerate(partitions['test']) if row['expected_tones'] == [tone]], dtype=int)
            per_tone[str(tone)] = probability_metrics(labels['test'][indices], probabilities['test'][indices])
        unanimous = [i for i, row in enumerate(partitions['test'])
                     if row['votes'] and len(set(row['votes'][target])) == 1]
        report['results'][target] = {
            'selected_model': parameters, 'selection_log_loss': loss, 'calibrator': calibrator,
            'test_uncalibrated': probability_metrics(labels['test'], raw['test']),
            'test_calibrated': probability_metrics(labels['test'], probabilities['test']),
            'test_constant_prior_baseline': probability_metrics(labels['test'], baseline),
            'test_selective_decisions': selective,
            'test_end_to_end_coverage': {
                'all_annotated_units': coverage['test']['annotated_units'],
                'unscorable_units': coverage['test'].get('unscorable_units', 0),
                'decided_units': selective['accepted'] + selective['rejected'],
                'coverage': (selective['accepted'] + selective['rejected']) / coverage['test']['annotated_units'],
                'unscorable_policy': 'abstain, not pass',
            },
            'threshold_selection': {'accept': acceptance, 'reject': rejection},
            'test_per_expected_tone': per_tone,
            'test_unanimous_raters': probability_metrics(labels['test'][unanimous], probabilities['test'][unanimous]),
            'speaker_bootstrap': bootstrap,
            'held_out_acceptance_gate_passed': bool(validation_pass),
        }
        print(target, json.dumps({
            'test_brier': report['results'][target]['test_calibrated']['brier'],
            'baseline_brier': report['results'][target]['test_constant_prior_baseline']['brier'],
            'ece': report['results'][target]['test_calibrated']['ece_10_bins'],
            'selective': selective, 'acceptance_gate': bool(validation_pass),
        }), flush=True)
    artifact['reference_sentences'] = sorted({row['text'] for row in utterances})
    failure_rows = cache_rows()
    report['unscorable_utterances'] = [
        {'utterance': row['utterance'], 'speaker': row['speaker'], 'split': row['split'],
         'reason': failure_rows[row['utterance']].get('reason', 'unknown measurement failure'),
         'annotated_units': len(row['units'])}
        for row in utterances if failure_rows[row['utterance']]['status'] != 'measured'
    ]
    report['domain_transfer_verified'] = False
    report['automatic_practice_admission_enabled'] = False
    split_manifest = {
        'reference_revision': manifest['revision'], 'seed': settings['split']['seed'],
        'assignments': [{'utterance': row['utterance'], 'speaker': row['speaker'], 'split': row['split'],
                         'audio_sha256': row['sha256']} for row in utterances],
    }
    artifact['split_manifest_sha256'] = sha256(stable_json(split_manifest).encode())
    artifact['evaluation_sha256'] = sha256(stable_json(report).encode())
    MODEL.write_bytes(gzip.compress(stable_json(artifact).encode(), mtime=0))
    report['model_sha256'] = sha256(MODEL.read_bytes())
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (ROOT / 'data/pronunciation_judge_splits.json').write_text(
        json.dumps(split_manifest, indent=2) + '\n', encoding='utf-8',
    )
    print('Saved a non-executable JSON model and sealed evaluation. Practice admission is unchanged.')


def load_model():
    payload = MODEL.read_bytes()
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    if sha256(payload) != report['model_sha256']:
        raise ValueError('Judge model does not match its held-out evaluation')
    artifact = json.loads(gzip.decompress(payload))
    unsigned_report = {key: value for key, value in report.items() if key != 'model_sha256'}
    if sha256(stable_json(unsigned_report).encode()) != artifact['evaluation_sha256']:
        raise ValueError('Judge evaluation content differs from its model fingerprint')
    if artifact['feature_version'] != VERSION or artifact['feature_names'] != FEATURE_NAMES:
        raise ValueError('Judge feature schema changed; rebuild and reevaluate')
    if artifact['feature_fingerprint'] != feature_fingerprint():
        raise ValueError('Judge feature implementation changed; rebuild and reevaluate')
    if artifact['config_sha256'] != sha256(CONFIG_PATH.read_bytes()):
        raise ValueError('Judge configuration changed; rebuild and reevaluate')
    if artifact['reference_manifest_sha256'] != sha256((ROOT / 'data/pronunciation_judge_reference.json').read_bytes()):
        raise ValueError('Judge reference corpus manifest changed')
    if artifact['trainer_sha256'] != sha256(Path(__file__).read_bytes()):
        raise ValueError('Judge training/scoring implementation changed; rebuild and reevaluate')
    split_manifest = json.loads((ROOT / 'data/pronunciation_judge_splits.json').read_text(encoding='utf-8'))
    if artifact['split_manifest_sha256'] != sha256(stable_json(split_manifest).encode()):
        raise ValueError('Judge speaker split manifest changed')
    return artifact


def judge(audio_path, text, expected_pinyin=None):
    artifact = load_model()
    engines = load_engines()
    if engine_provenance(engines) != artifact['engine_provenance']:
        raise ValueError('Inference engines differ from those used for calibration')
    evidence = analyze(audio_path, text, *engines, expected_pinyin=expected_pinyin)
    matrix = np.asarray([row['features'] for row in evidence['units']], dtype=np.float32)
    lower, upper = np.array(artifact['feature_support']['lower']), np.array(artifact['feature_support']['upper'])
    outliers = np.mean((matrix < lower) | (matrix > upper), axis=1)
    known_text = evidence['reference_text'] in artifact['reference_sentences']
    predictions = {}
    for target, model in artifact['heads'].items():
        raw = forest_predict(model['forest'], matrix)
        predictions[target] = calibrated(raw, model['calibration'])
    units = []
    for index, unit in enumerate(evidence['units']):
        scores = {}
        for target, model in artifact['heads'].items():
            probability = float(predictions[target][index])
            # Text overlap alone cannot establish accent, recording or speaker-domain transfer.
            reasons = ['target-domain calibration has not been independently verified']
            if unit['voiced_frames'] < 8:
                reasons.append('insufficient voiced frames')
            if outliers[index] > .05:
                reasons.append('acoustics outside the supported training range')
            if not model['held_out_acceptance_gate_passed']:
                reasons.append('held-out false-acceptance gate was not met')
            scores[target] = {
                'reference_calibrated_correctness_estimate': probability,
                'status': 'uncertain',
                'calibration_transfer_verified': False,
                'reasons': reasons,
            }
        units.append({
            'text': evidence['reference_text'][unit['character_start']:unit['character_end']],
            'expected_pinyin': unit['expected_pinyin'],
            'start_seconds': unit['start'], 'end_seconds': unit['end'],
            'out_of_training_range_fraction': float(outliers[index]),
            'scores': scores,
        })
    return {
        'judge': 'expert-trained-calibrated-pronunciation-v1',
        'audio_sha256': evidence['audio_sha256'], 'reference_text': evidence['reference_text'],
        'recognition_text': evidence['recognition_text'], 'units': units,
        'domain': 'unvalidated_target_domain',
        'reference_text_seen_in_corpus': known_text,
        'production_admission': False,
        'limitation': 'Calibration was measured on held-out OMPAL speakers, not this app corpus or arbitrary learner populations. This report does not certify pronunciation or authorize practice audio.',
        'model_sha256': sha256(MODEL.read_bytes()),
    }


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_command = commands.add_parser('prepare')
    prepare_command.add_argument('--limit', type=int)
    commands.add_parser('train')
    score = commands.add_parser('score')
    score.add_argument('--audio', type=Path, required=True)
    score.add_argument('--text', required=True)
    score.add_argument('--pinyin', nargs='+', help='explicit numbered Mandarin syllables; 5 is neutral')
    score.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.limit)
    elif args.command == 'train':
        train()
    else:
        result = judge(args.audio, args.text, args.pinyin)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        print(f'Pronunciation report written to {args.output}. Not a practice-approval certificate.')


if __name__ == '__main__':
    main()
