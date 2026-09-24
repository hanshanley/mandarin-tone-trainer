#!/usr/bin/env python3
"""Native tone-label hypotheses, audio-only predictions, and independent release gates."""
import argparse
import gzip
import hashlib
import itertools
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from scipy.stats import beta

from acoustic_analysis import METHODS, extract
from collect_acoustic_evidence import (
    ALIGNMENT_VERSION, PROFILE_VERSION, identity_encoding, read_jsonl, resolved_recognition,
)
from native_tone_labels import canonical, digest, is_direct, original_inventory, safe_audio, valid_pattern, verify_inventory
from runtime_data import ROOT


CONFIG = ROOT / 'config/native_tone_validation.json'
MODEL = ROOT / 'data/native_tone_validator.json.gz'
REPORT = ROOT / 'data/native_tone_evaluation.json'
FEATURE_VERSION = 'native-joint-f0-time-v1'
PARTITIONS = ('train', 'calibration', 'test', 'external_source')
TONES = ('1', '2', '3', '4', 'N')


def settings():
    return json.loads(CONFIG.read_text())


def pipeline_hash():
    return digest({name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                   for name in ('native_tone_validator.py', 'native_tone_labels.py',
                                'acoustic_analysis.py', 'collect_acoustic_evidence.py', 'build_hsk_data.py')})


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def group_assignments(records, options):
    """Union homophonic families and duplicate payloads before assigning a partition."""
    parent = {}

    def find(key):
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    def union(left, right):
        a, b = find(left), find(right)
        parent[max(a, b)] = min(a, b)

    for row in records:
        union('family:' + '+'.join(row['syllables']), 'audio:' + row['sha256'])
        union('audio:' + row['sha256'], 'pcm:' + row['decoded_audio_sha256'])
    groups = defaultdict(list)
    for row in records:
        groups[find('audio:' + row['sha256'])].append(row)
    assignments = {}
    for group, rows in groups.items():
        # Whole-source evaluation and the family holdout are separate evaluations.
        value = int(hashlib.sha256((options['seed'] + group).encode()).hexdigest()[:12], 16) / 16**12
        fractions = options['split_fractions']
        if set(fractions) != {'train', 'calibration', 'test'} or abs(sum(fractions.values()) - 1) > 1e-9:
            raise ValueError('Invalid reference partition fractions')
        split = 'train' if value < fractions['train'] else 'calibration' if value < fractions['train'] + fractions['calibration'] else 'test'
        external_hashes = {row['decoded_audio_sha256'] for row in rows if row['source'] == options['held_out_source']}
        for row in rows:
            assignments[row['id']] = 'external_source' if row['source'] == options['held_out_source'] or row['decoded_audio_sha256'] in external_hashes else split
    return assignments


def intervals_for(row, profile, alignment):
    count = len(row['syllables'])
    if count == 1:
        return [(0., profile['duration'])]
    if not alignment or alignment.get('sha256') != row['sha256'] or alignment.get('evidence_version') != ALIGNMENT_VERSION:
        raise ValueError('Missing current whole-word alignment')
    if alignment.get('phonetic_bases') != row['syllables']:
        raise ValueError('Aligned syllables differ from the reading')
    stamps = alignment.get('timestamps_ms') or []
    if len(stamps) != count:
        raise ValueError('Alignment does not cover each syllable')
    previous = 0.
    boundaries = [0.]
    for position, pair in enumerate(stamps):
        if len(pair) != 2 or not np.isfinite(pair).all():
            raise ValueError('Invalid alignment timestamp')
        start, end = [float(item) / 1000 for item in pair]
        if start < previous - .03 or end <= start or start < 0 or end > profile['duration'] + .02:
            raise ValueError('Nonmonotonic or out-of-bounds word alignment')
        if position:
            boundary = .5 * (previous + start)
            if boundary <= boundaries[-1] or boundary >= profile['duration']:
                raise ValueError('Invalid syllable boundary')
            boundaries.append(boundary)
        previous = end
    return list(zip(boundaries, boundaries[1:] + [profile['duration']]))


def audio_features(profile, intervals):
    """No tone labels, pinyin, word IDs, corpus IDs, or hypothesized answers enter here."""
    duration = float(profile['duration'])
    if not np.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid audio duration')
    tracks = {method: np.array([np.nan if value is None else value for value in profile['tracks'][method]], float)
              for method in METHODS}
    size = len(tracks['praat'])
    if any(len(track) != size for track in tracks.values()) or size != len(profile['rms']):
        raise ValueError('Pitch and energy time coordinates differ')
    energy = np.asarray(profile['rms'], float)
    if not np.isfinite(energy).all() or max(energy, default=0) <= 0:
        raise ValueError('Silent or invalid audio energy')
    for track in tracks.values():
        if np.any(np.isfinite(track) & (track <= 0)) or np.isinf(track).any():
            raise ValueError('Invalid pitch track')
    times = np.arange(size) / 100.
    voiced_hz = np.concatenate([track[np.isfinite(track)] for track in tracks.values()])
    if len(voiced_hz) < 8:
        raise ValueError('Insufficient pitch measurements')
    register = float(np.median(voiced_hz))
    amplitude = max(float(np.quantile(energy, .9)), 1e-6)
    features = [duration, len(intervals), math.log2(register), float(np.std(12 * np.log2(voiced_hz / register)))]
    diagnostics, segment_medians = [], []
    previous_end = 0.
    for start, end in intervals:
        if not (np.isfinite([start, end]).all() and 0 <= start < end <= duration + .001 and start >= previous_end):
            raise ValueError('Invalid analysis intervals')
        previous_end = end
        selected = (times >= start) & (times < end)
        frame_indices = np.flatnonzero(selected)
        if len(frame_indices) < 6:
            raise ValueError('Syllable interval too short')
        rms = energy[selected]
        features += [end - start, (end - start) / duration, start / duration,
                     float(np.mean(rms) / amplitude), float(np.std(rms) / amplitude)]
        valid_methods, method_curves = 0, {}
        for method, track in tracks.items():
            values = track[selected]
            valid = np.isfinite(values)
            valid_methods += int(valid.sum() >= 8)
            if valid.any():
                pitch = 12 * np.log2(values[valid] / register)
                features += [float(valid.mean()), float(np.mean(pitch)), float(np.std(pitch)),
                             float(np.min(pitch)), float(np.max(pitch))]
            else:
                features += [0.] * 5
            centers, masks, raw_centers = [], [], []
            for block in np.array_split(values, 12):
                block = block[np.isfinite(block)]
                masks.append(float(len(block) > 0))
                raw = float(np.median(block)) if len(block) else None
                raw_centers.append(raw)
                centers.append(12 * math.log2(raw / register) if raw else 0.)
            features += centers + masks
            method_curves[method] = raw_centers
        common = np.stack([track[selected] for track in tracks.values()])
        differences = []
        for i, j in itertools.combinations(range(3), 2):
            valid = np.isfinite(common[i]) & np.isfinite(common[j])
            differences.append(float(np.median(np.abs(12 * np.log2(common[i, valid] / common[j, valid])))) if valid.sum() >= 5 else 24.)
        features += differences
        observed = common[np.isfinite(common)]
        segment_medians.append(float(np.median(observed)) if len(observed) else register)
        diagnostics.append({'usable_pitch_trackers': valid_methods, 'minimum_tracker_difference': min(differences),
                            'curves': method_curves})
    # Explicit transitions and relative durations preserve pair timing.
    for position in range(1, len(intervals)):
        features += [
            12 * math.log2(segment_medians[position] / segment_medians[position - 1]),
            (intervals[position][1] - intervals[position][0]) / (intervals[position - 1][1] - intervals[position - 1][0]),
        ]
    if not np.isfinite(features).all():
        raise ValueError('Non-finite tone features')
    return np.asarray(features, np.float32), diagnostics


def perturbations(intervals, delta):
    variants = [intervals]
    for index in range(len(intervals) - 1):
        for shift in (-delta, delta):
            boundary = intervals[index][1] + shift
            if boundary - intervals[index][0] < .06 or intervals[index + 1][1] - boundary < .06:
                continue
            changed = list(intervals)
            changed[index] = (changed[index][0], boundary)
            changed[index + 1] = (boundary, changed[index + 1][1])
            variants.append(changed)
    return variants


def export_forest(model):
    trees = []
    for estimator in model.estimators_:
        tree = estimator.tree_
        values = tree.value[:, 0, :]
        probabilities = values / np.maximum(values.sum(axis=1, keepdims=True), 1e-12)
        trees.append({'left': tree.children_left.tolist(), 'right': tree.children_right.tolist(),
                      'feature': tree.feature.tolist(), 'threshold': tree.threshold.tolist(),
                      'probabilities': probabilities.tolist()})
    return trees


def forest_predict(trees, matrix):
    matrix = np.asarray(matrix, np.float32)
    total = None
    for tree in trees:
        nodes = np.zeros(len(matrix), dtype=int)
        left, right = np.asarray(tree['left']), np.asarray(tree['right'])
        feature, threshold = np.asarray(tree['feature']), np.asarray(tree['threshold'])
        active = left[nodes] >= 0
        while active.any():
            indices = np.flatnonzero(active)
            old = nodes[indices]
            nodes[indices] = np.where(matrix[indices, feature[old]] <= threshold[old], left[old], right[old])
            active = left[nodes] >= 0
        values = np.asarray(tree['probabilities'])[nodes]
        total = values if total is None else total + values
    return total / len(trees)


def temperature_scale(probabilities, temperature):
    if not np.isfinite(temperature) or temperature <= 0:
        raise ValueError('Invalid calibration temperature')
    return softmax(np.log(np.clip(probabilities, 1e-9, 1)) / temperature, axis=1)


def fit_temperature(probabilities, labels):
    if not len(labels):
        raise ValueError('No source-label calibration examples')
    def objective(log_temperature):
        scaled = temperature_scale(probabilities, math.exp(float(log_temperature)))
        return float(-np.mean(np.log(np.clip(scaled[np.arange(len(labels)), labels], 1e-9, 1))))
    result = minimize_scalar(objective, bounds=(-2, 3), method='bounded')
    if not result.success:
        raise ValueError('Source-agreement calibration did not converge')
    return math.exp(float(result.x))


def metrics(rows, probabilities, classes):
    if not rows:
        return {'n': 0, 'label_basis': 'unverified_source_annotations'}
    truth = [row['weak_training_label'] for row in rows]
    predicted = [classes[index] for index in np.argmax(probabilities, axis=1)]
    supported = [label in classes for label in truth]
    confusion = Counter(zip(truth, predicted))
    per_pattern = {}
    for label in sorted(set(truth)):
        indices = [index for index, value in enumerate(truth) if value == label]
        per_pattern[label] = {
            'n': len(indices), 'exact_source_label_agreement': sum(predicted[i] == label for i in indices) / len(indices),
        }
    token_matches = sum(sum(left == right for left, right in zip(a.split('-'), b.split('-')))
                        for a, b in zip(truth, predicted))
    token_count = sum(len(value.split('-')) for value in truth)
    bins = []
    confidence = np.max(probabilities, axis=1)
    correct = np.asarray([a == b for a, b in zip(truth, predicted)], float)
    ece = 0.
    for lower in np.arange(0, 1, .1):
        indices = (confidence >= lower) & (confidence <= lower + .1 if lower >= .9 else confidence < lower + .1)
        if indices.any():
            mean, agree = float(confidence[indices].mean()), float(correct[indices].mean())
            ece += float(indices.mean()) * abs(mean - agree)
            bins.append({'n': int(indices.sum()), 'confidence': mean, 'source_agreement': agree})
    return {
        'n': len(rows), 'label_basis': 'unverified_source_annotations',
        'exact_source_label_agreement': float(correct.mean()), 'token_source_label_agreement': token_matches / token_count,
        'unsupported_source_patterns': sum(not value for value in supported),
        'source_agreement_ece': ece, 'calibration_bins': bins, 'per_pattern': per_pattern,
        'confusion': [{'supplied': a, 'predicted': b, 'count': count} for (a, b), count in sorted(confusion.items())],
    }


def load_evidence():
    return (read_jsonl(ROOT / '.audit/acoustic-profiles.jsonl'),
            read_jsonl(ROOT / '.audit/acoustic-alignment.jsonl'))


def prepare_inventory(inventory_path):
    inventory = verify_inventory(json.loads(inventory_path.read_text()))
    candidates = []
    for row in inventory['records']:
        if len(row['syllables']) > max(settings()['supported_lengths']):
            continue
        candidates.append({
            'kind': 'native' if row['kind'] == 'word' else 'comparison',
            'audio_path': row['audio_path'], 'sha256': row['sha256'],
            'word': row.get('word') or '', 'pinyin_syllables': row['syllables'],
            'surface_pattern': row['original_labels']['surface'],
        })
    path = ROOT / '.audit/native-tone-feature-candidates.json'
    atomic_json(path, {'candidates': candidates})
    for phase in ('profiles', 'asr', 'prepared-asr', 'alignment'):
        subprocess.run(
            [sys.executable, str(ROOT / 'scripts/collect_acoustic_evidence.py'),
             '--candidates', str(path), '--phase', phase, '--workers', '2'],
            cwd=ROOT, check=True,
        )


def training_examples(inventory, profiles, alignments):
    options = settings()
    labels_by_hash = defaultdict(set)
    for row in inventory['records']:
        labels_by_hash[row['decoded_audio_sha256']].add(row['weak_training_label'])
    seen = set()
    examples, failures = [], []
    for row in inventory['records']:
        reason = None
        count = len(row['syllables'])
        if row['decoded_audio_sha256'] in seen:
            continue
        seen.add(row['decoded_audio_sha256'])
        if count not in options['supported_lengths'] or count == 1 and row['weak_training_label'] == 'N':
            reason = 'unsupported length or standalone neutral'
        elif row['quarantined']:
            reason = 'known source quarantine'
        elif len(labels_by_hash[row['decoded_audio_sha256']]) != 1:
            reason = 'same audio has conflicting supplied tone labels'
        elif any(item['ambiguous'] for item in row['hypotheses'] if item['role'] == 'spoken'):
            reason = 'ambiguous source prosodic grouping'
        profile = profiles.get(row['audio_path'])
        if not reason and (not profile or profile['sha256'] != row['sha256'] or profile.get('evidence_version') != PROFILE_VERSION):
            reason = 'missing current audio profile'
        if reason:
            failures.append({'id': row['id'], 'reason': reason})
            continue
        try:
            intervals = intervals_for(row, profile, alignments.get(row['audio_path']))
            vector, quality = audio_features(profile, intervals)
        except ValueError as error:
            failures.append({'id': row['id'], 'reason': str(error)})
            continue
        examples.append({'row': row, 'features': vector, 'quality': quality})
    return examples, failures


def train(inventory_path):
    from sklearn.ensemble import ExtraTreesClassifier
    inventory = verify_inventory(json.loads(inventory_path.read_text()))
    options = settings()
    profiles, alignments = load_evidence()
    examples, failures = training_examples(inventory, profiles, alignments)
    splits = group_assignments(inventory['records'], options)
    profile_bindings = sorted((item['row']['audio_path'], item['row']['sha256']) for item in examples)
    evidence_files = [ROOT / '.audit/acoustic-profiles.jsonl', ROOT / '.audit/acoustic-alignment.jsonl']
    evidence_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in evidence_files}
    artifact = {
        'version': 1, 'feature_version': FEATURE_VERSION, 'pipeline_sha256': pipeline_hash(),
        'config_sha256': hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        'inventory_sha256': inventory['inventory_sha256'], 'heads': {},
        'source_labels_are_gold': False, 'production_admission': False,
        'source_reliability_prior': None,
        'reference_evidence_sha256': evidence_hashes,
        'reference_audio_sha256': digest(profile_bindings),
    }
    report = {
        'version': 1, 'inventory_sha256': inventory['inventory_sha256'], 'heads': {},
        'reference_labels': options['reference_labels'], 'speaker_identity': options['speaker_identity'],
        'source_prior': 'not_estimated_without_independent_gold',
        'gold_release_status': 'blocked_no_independent_native_gold',
        'production_admission': False, 'unscorable': dict(Counter(item['reason'] for item in failures)),
        'reference_evidence_sha256': evidence_hashes,
        'calibration_interpretation': 'temperature fitted to uncertain source-label agreement; no independent correctness calibration yet',
    }
    for length in options['supported_lengths']:
        rows = [item for item in examples if len(item['row']['syllables']) == length]
        groups = {split: [item for item in rows if splits[item['row']['id']] == split] for split in PARTITIONS}
        counts = Counter(item['row']['weak_training_label'] for item in groups['train'])
        classes = sorted(label for label, count in counts.items() if count >= options['minimum_training_per_pattern'])
        if length == 1 and set(classes) != {'1', '2', '3', '4'}:
            raise ValueError('Training references do not cover all four isolated tones')
        usable = [item for item in groups['train'] if item['row']['weak_training_label'] in classes]
        calibration = [item for item in groups['calibration'] if item['row']['weak_training_label'] in classes]
        if len(classes) < 2 or len(usable) < 30 or len(calibration) < 10:
            report['heads'][str(length)] = {'status': 'insufficient_reference_support', 'patterns': counts}
            continue
        model = ExtraTreesClassifier(**options['forest'], max_features=.8, class_weight='balanced', n_jobs=2)
        model.fit(np.asarray([item['features'] for item in usable]), [item['row']['weak_training_label'] for item in usable])
        classes = model.classes_.tolist()
        raw = model.predict_proba(np.asarray([item['features'] for item in calibration]))
        temperature = fit_temperature(raw, np.array([classes.index(item['row']['weak_training_label']) for item in calibration]))
        trees = export_forest(model)
        if not np.allclose(forest_predict(trees, np.asarray([item['features'] for item in calibration])), raw, atol=1e-12):
            raise ValueError('Portable tone model differs from fitted model')
        head = {
            'classes': classes, 'trees': trees, 'temperature': temperature, 'input_features': len(usable[0]['features']),
            'training_sha256s': sorted({item['row']['sha256'] for item in usable}),
            'training_decoded_sha256s': sorted({item['row']['decoded_audio_sha256'] for item in usable}),
            'training_families': sorted({'+'.join(item['row']['syllables']) for item in usable}),
            'training_sources': sorted({item['row']['source'] for item in usable}),
        }
        artifact['heads'][str(length)] = head
        result = {'status': 'fitted_to_weak_source_labels', 'patterns': counts, 'partitions': {}}
        for split in PARTITIONS:
            selected = groups[split]
            probabilities = temperature_scale(forest_predict(trees, np.asarray([item['features'] for item in selected])), temperature) if selected else None
            result['partitions'][split] = metrics([item['row'] for item in selected], probabilities, classes)
        test_rows = groups['test']
        if test_rows:
            p = temperature_scale(forest_predict(trees, [item['features'] for item in test_rows]), temperature)
            predictions = np.argmax(p, axis=1)
            high = np.max(p, axis=1) >= options['reference_agreement_threshold']
            original_support, swapped_support, checks = 0, 0, 0
            for index, item in enumerate(test_rows):
                actual = item['row']['weak_training_label']
                original_support += int(high[index] and classes[predictions[index]] == actual)
                for alternative in classes:
                    if alternative == actual:
                        continue
                    checks += 1
                    swapped_support += int(high[index] and classes[predictions[index]] == alternative)
            result['label_swap_controls'] = {
                'test_recordings': len(test_rows), 'original_labels_supported': original_support,
                'swapped_labels_evaluated': checks, 'swapped_labels_supported': swapped_support,
                'audio_predictions_do_not_take_the_supplied_label_as_input': True,
                'not_an_independent_error_rate': True,
            }
        report['heads'][str(length)] = result
        print(length, 'syllables:', {split: result['partitions'][split].get('exact_source_label_agreement') for split in PARTITIONS}, flush=True)
    report['model_content_sha256'] = digest(artifact)
    report['splits'] = {row['id']: splits[row['id']] for row in inventory['records']}
    report['split_manifest'] = [
        {'id': row['id'], 'source': row['source'], 'family': '+'.join(row['syllables']),
         'sha256': row['sha256'], 'decoded_audio_sha256': row['decoded_audio_sha256'], 'partition': splits[row['id']]}
        for row in inventory['records']
    ]
    atomic_json(REPORT, report)
    artifact['evaluation_sha256'] = digest(report)
    MODEL.write_bytes(gzip.compress(canonical(artifact).encode(), mtime=0))
    print('Independent release gate remains closed: supplied source labels are not test gold.', flush=True)
    print('Native label-blind models fitted. These metrics are source-label agreement, not independent accuracy.')


def load_model():
    model = json.loads(gzip.decompress(MODEL.read_bytes()))
    if model['feature_version'] != FEATURE_VERSION or model['pipeline_sha256'] != pipeline_hash():
        raise ValueError('Native validation implementation changed; retrain explicitly')
    if model['config_sha256'] != hashlib.sha256(CONFIG.read_bytes()).hexdigest():
        raise ValueError('Native validator configuration changed')
    report = json.loads(REPORT.read_text())
    if model['evaluation_sha256'] != digest(report) or report['model_content_sha256'] != digest(
        {key: value for key, value in model.items() if key != 'evaluation_sha256'}
    ):
        raise ValueError('Native model and evaluation fingerprints disagree')
    return model


def blind_prediction(head, profile, intervals):
    options = settings()
    variants = perturbations(intervals, options['boundary_perturbation_seconds'])
    vectors, qualities = zip(*(audio_features(profile, variant) for variant in variants))
    if any(len(vector) != head['input_features'] for vector in vectors):
        raise ValueError('Tone feature dimension changed')
    probabilities = temperature_scale(forest_predict(head['trees'], np.asarray(vectors)), head['temperature'])
    return summarize_prediction(head, probabilities, qualities)


def summarize_prediction(head, probabilities, qualities):
    options = settings()
    predictions = [head['classes'][index] for index in np.argmax(probabilities, axis=1)]
    sorted_probabilities = np.sort(probabilities, axis=1)
    quality = all(
        item['usable_pitch_trackers'] >= 2 and item['minimum_tracker_difference'] <= options['maximum_tracker_difference_semitones']
        for group in qualities for item in group
    )
    return {
        'pattern': predictions[0],
        'probabilities': dict(zip(head['classes'], probabilities[0].tolist())),
        'boundary_variants': len(probabilities), 'boundary_stable': len(set(predictions)) == 1,
        'minimum_top_probability': float(np.min(np.max(probabilities, axis=1))),
        'minimum_margin': float(np.min(sorted_probabilities[:, -1] - sorted_probabilities[:, -2])),
        'quality_ok': quality,
        'score_interpretation': 'probability_calibrated_to_weak_source_labels_not_gold',
    }


def compatibility(prediction, original_label, hypotheses, source_prior=None):
    """The label may explain evidence, but cannot alter or rescue the blind prediction."""
    options = settings()
    label_informed = None
    if source_prior is not None:
        if (source_prior.get('sampling') != 'uniform_source_holdout'
                or source_prior.get('role') != 'calibration'
                or source_prior.get('source_label_accuracy') is None
                or source_prior.get('n', 0) < options['gold']['minimum_calibration_per_source']):
            raise ValueError('A source prior cannot be used before representative independent calibration')
        reliability = source_prior['source_label_accuracy']
        if not 0 <= reliability <= 1 or source_prior.get('correct', -1) / source_prior['n'] != reliability:
            raise ValueError('Source prior does not match its independent audit counts')
        classes = list(prediction['probabilities'])
        if original_label in classes and len(classes) > 1:
            weight = options['gold']['maximum_label_prior_weight']
            label_informed = {
                label: (1 - weight) * probability + weight * (
                    reliability if label == original_label else (1 - reliability) / (len(classes) - 1)
                ) for label, probability in prediction['probabilities'].items()
            }
    supplied_score = prediction['probabilities'].get(original_label, 0.)
    allowed = {item['pattern'] for item in hypotheses if item['role'] == 'spoken' and not item['ambiguous']}
    alternate = prediction['pattern'] != original_label and prediction['pattern'] in allowed
    reliable = (
        prediction['quality_ok'] and prediction['boundary_stable']
        and prediction['minimum_top_probability'] >= options['reference_agreement_threshold']
        and prediction['minimum_margin'] >= options['minimum_margin']
    )
    if original_label not in prediction['probabilities']:
        status = 'possible_spoken_variant' if reliable and alternate else 'unresolved'
    elif not reliable:
        status = 'unresolved'
    elif prediction['pattern'] == original_label:
        status = 'reference_supported'
    elif alternate:
        status = 'possible_spoken_variant'
    else:
        status = 'likely_mismatch'
    return {
        'assessment': status, 'supplied_pattern': original_label, 'supplied_acoustic_score': supplied_score,
        'blind_pattern': prediction['pattern'], 'plausible_spoken_patterns': sorted(allowed),
        'source_prior_used': source_prior is not None,
        'original_pattern_represented_in_model': original_label in prediction['probabilities'],
        'label_informed_probabilities': label_informed,
        'label_prior_cannot_change_blind_support_or_contradiction': True,
        'independent_accuracy_verified': False, 'production_admission': False,
    }


def score_inventory(inventory_path, output, prior_path=None):
    inventory = verify_inventory(json.loads(inventory_path.read_text()))
    model = load_model()
    if model['inventory_sha256'] != inventory['inventory_sha256']:
        raise ValueError('Scoring inventory differs from the model reference inventory')
    priors = {}
    if prior_path:
        prior_data = json.loads(prior_path.read_text())
        if (prior_data.get('inventory_sha256') != inventory['inventory_sha256']
                or prior_data.get('model_sha256') != hashlib.sha256(MODEL.read_bytes()).hexdigest()
                or prior_data.get('sampling') != 'uniform_source_holdout'):
            raise ValueError('Independent calibration priors do not match this inventory/model')
        priors = prior_data.get('source_priors', {})
    profiles, alignments = load_evidence()
    raw = read_jsonl(ROOT / '.audit/acoustic-asr.jsonl')
    prepared = read_jsonl(ROOT / '.audit/acoustic-prepared-asr.jsonl')
    results, cache, failed = [], {}, {}
    groups = defaultdict(dict)
    for row in inventory['records']:
        key = (row['sha256'], tuple(row['syllables']))
        if key in groups[len(row['syllables'])] or key in failed or row['quarantined']:
            continue
        head = model['heads'].get(str(len(row['syllables'])))
        if not head:
            failed[key] = 'No trained model for this word length'
            continue
        profile = profiles.get(row['audio_path'])
        try:
            if not profile or profile['sha256'] != row['sha256'] or profile.get('evidence_version') != PROFILE_VERSION:
                raise ValueError('Missing current pitch evidence')
            intervals = intervals_for(row, profile, alignments.get(row['audio_path']))
            variants = perturbations(intervals, settings()['boundary_perturbation_seconds'])
            vectors, quality = zip(*(audio_features(profile, variant) for variant in variants))
            groups[len(row['syllables'])][key] = (vectors, quality)
        except ValueError as error:
            failed[key] = str(error)
    for length, items in groups.items():
        if not items:
            continue
        head = model['heads'][str(length)]
        ordered = list(items.items())
        matrix = np.asarray([vector for _, (vectors, _) in ordered for vector in vectors], dtype=np.float32)
        if matrix.shape[1] != head['input_features']:
            raise ValueError('Tone feature dimension changed')
        probabilities = temperature_scale(forest_predict(head['trees'], matrix), head['temperature'])
        offset = 0
        for key, (vectors, quality) in ordered:
            cache[key] = summarize_prediction(head, probabilities[offset:offset + len(vectors)], quality)
            offset += len(vectors)
        print(f'Label-blind {length}-syllable scoring: {len(items)} recordings, {len(matrix)} boundary variants', flush=True)
    for row in inventory['records']:
        length = str(len(row['syllables']))
        head = model['heads'].get(length)
        result = {'id': row['id'], 'label_identity': row['label_identity'], 'sha256': row['sha256'],
                  'source': row['source'], 'syllables': row['syllables'], 'supplied_pattern': row['weak_training_label'],
                  'production_admission': False}
        try:
            if row['quarantined']:
                raise ValueError('Known source quarantine')
            if not head:
                raise ValueError('No trained model for this word length')
            key = (row['sha256'], tuple(row['syllables']))
            if key in failed:
                raise ValueError(failed[key])
            prediction = cache[key]
            result['blind'] = prediction
            result.update(compatibility(prediction, row['weak_training_label'], row['hypotheses'], priors.get(row['source'])))
            result['reference_overlap'] = row['decoded_audio_sha256'] in head['training_decoded_sha256s']
            a, b = raw.get(row['audio_path']), prepared.get(row['audio_path'])
            if a and b and a.get('sha256') == b.get('sha256') == row['sha256']:
                recognized, _ = resolved_recognition(a, b)
                identity_ok = bool(recognized and identity_encoding(recognized, row['syllables']))
            else:
                identity_ok = False
            result['identity_supported'] = identity_ok
            if not identity_ok:
                result['assessment'] = 'unresolved'
                result['reason'] = 'Unresolved syllable identity; tone scores do not verify consonants/finals'
            if result['reference_overlap']:
                result['assessment'] = 'unresolved'
                result['reason'] = 'Training-reference overlap cannot validate the same recording'
        except ValueError as error:
            result.update(assessment='unresolved', reason=str(error))
        results.append(result)
    out = {
        'version': 1, 'inventory_sha256': inventory['inventory_sha256'],
        'model_sha256': hashlib.sha256(MODEL.read_bytes()).hexdigest(),
        'independent_gold_available': False, 'production_admission': False,
        'counts': dict(Counter(row['assessment'] for row in results)), 'records': results,
    }
    atomic_json(output, out)
    print(out['counts'])


def score_file(audio, syllables, original_pattern, word, output):
    from audit_native_readings import recognized_pinyin
    from native_tone_labels import decoded_fingerprints, hypotheses
    from pronunciation_features import load_engines

    path = safe_audio(audio)
    syllables = [value.lower().replace('ü', 'v') for value in syllables]
    if any(not re.fullmatch(r'[a-zv]+', base) for base in syllables) or not valid_pattern(original_pattern, len(syllables)):
        raise ValueError('Provide exact base syllables and an aligned original tone pattern')
    model = load_model()
    head = model['heads'].get(str(len(syllables)))
    result = {'version': 1, 'audio_path': audio, 'supplied_pattern': original_pattern,
              'model_sha256': hashlib.sha256(MODEL.read_bytes()).hexdigest(),
              'production_admission': False, 'independent_accuracy_verified': False}
    if not head:
        atomic_json(output, {**result, 'assessment': 'unresolved', 'reason': 'This length has no independently supported model'})
        return
    if len(syllables) > 1 and (not word or not re.fullmatch(r'[\u3400-\u9fff]+', word) or len(word) != len(syllables)):
        raise ValueError('Multi-syllable analysis requires matching Hanzi for alignment')
    payload_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    decoded_hash = decoded_fingerprints({audio: payload_hash})[audio]
    profile = extract(path)
    recognizer, aligner = load_engines()
    recognized = recognizer.generate(input=str(path), disable_pbar=True)[0]
    recognition = {'text': recognized.get('text', ''), 'recognized_pinyin': recognized_pinyin(recognized.get('text', ''))}
    identity_ok = bool(identity_encoding(recognition, syllables))
    alignment = None
    if len(syllables) > 1:
        from opencc import OpenCC
        aligned_word = OpenCC('t2s').convert(word)
        if len(aligned_word) != len(word):
            raise ValueError('Alignment script conversion changed the number of characters')
        if not identity_ok:
            atomic_json(output, {**result, 'assessment': 'unresolved', 'reason': 'Unprompted recognition did not confirm the syllables',
                                 'recognition': recognition, 'sha256': payload_hash})
            return
        aligned = aligner.generate(input=(str(path), ' '.join(aligned_word)), data_type=('sound', 'text'), disable_pbar=True)[0]
        alignment = {'sha256': payload_hash, 'phonetic_bases': syllables, 'evidence_version': ALIGNMENT_VERSION,
                     'timestamps_ms': aligned.get('timestamp', [])}
    if hashlib.sha256(path.read_bytes()).hexdigest() != payload_hash or profile['sha256'] != payload_hash:
        raise ValueError('Audio changed during native tone analysis')
    row = {'sha256': payload_hash, 'syllables': syllables}
    try:
        intervals = intervals_for(row, profile, alignment)
        prediction = blind_prediction(head, profile, intervals)
    except ValueError as error:
        atomic_json(output, {**result, 'assessment': 'unresolved', 'reason': str(error), 'sha256': payload_hash})
        return
    evidence = hypotheses({'word': word or '', 'pinyin_syllables': syllables, 'lexical_pattern': original_pattern,
                           'default_surface_pattern': original_pattern}, {}, {})
    assessment = compatibility(prediction, original_pattern, evidence)
    result.update(assessment)
    result.update(blind=prediction, identity_supported=identity_ok, sha256=payload_hash,
                  recognition=recognition, reference_overlap=decoded_hash in head['training_decoded_sha256s'])
    if result['reference_overlap'] or not identity_ok:
        result.update(assessment='unresolved', reason='Training overlap or unresolved syllable identity')
    atomic_json(output, result)


def audit_plan(inventory_path, output, per_stratum, sampling='challenge', per_source=120):
    inventory = verify_inventory(json.loads(inventory_path.read_text()))
    model = load_model()
    if inventory['inventory_sha256'] != model['inventory_sha256']:
        raise ValueError('Audit inventory does not match the frozen model')
    trained = {value for head in model['heads'].values() for value in head['training_decoded_sha256s']}
    strata = defaultdict(list)
    for row in inventory['records']:
        if row['quarantined'] or len(row['syllables']) > 4 or row['decoded_audio_sha256'] in trained:
            continue
        stratum = (row['source'],) if sampling == 'source' else (row['source'], len(row['syllables']), row['weak_training_label'])
        strata[stratum].append(row)
    chosen, seen = [], set()
    for stratum, rows in sorted(strata.items()):
        ordered = sorted(rows, key=lambda row: digest([settings()['seed'], 'independent-audit', row['sha256']]))
        count = 0
        for row in ordered:
            if row['decoded_audio_sha256'] in seen:
                continue
            seen.add(row['decoded_audio_sha256'])
            role = 'calibration' if int(digest(['role', row['sha256']])[:8], 16) % 2 == 0 else 'test'
            chosen.append({'id': row['id'], 'sha256': row['sha256'], 'label_identity': row['label_identity'],
                           'decoded_audio_sha256': row['decoded_audio_sha256'],
                           'role': role, 'stratum': [row['source'], len(row['syllables']), row['weak_training_label']],
                           'audio_path': row['audio_path'],
                           'annotated_pattern': None, 'reviews': []})
            count += 1
            if count >= (per_source if sampling == 'source' else per_stratum):
                break
    plan = {'version': 1, 'inventory_sha256': inventory['inventory_sha256'],
            'model_sha256': hashlib.sha256(MODEL.read_bytes()).hexdigest(),
            'sampling': 'uniform_source_holdout' if sampling == 'source' else 'source_length_pattern_challenge',
            'population_priors_permitted': sampling == 'source',
            'items': [{key: value for key, value in item.items() if key not in ('annotated_pattern', 'reviews')} for item in chosen]}
    plan['plan_sha256'] = digest(plan)
    frozen = output.with_name(output.stem + '-plan.json')
    for path in (frozen, output):
        if path.exists():
            raise ValueError('Independent audit plans and templates are never overwritten')
    atomic_json(frozen, plan)
    atomic_json(output, {'version': 1, 'plan_sha256': plan['plan_sha256'], 'items': chosen})
    print(f'Created {len(chosen)} pending independent audit items; no reviews or ground truth were fabricated.')


def validated_gold(plan, labels, predictions, roles):
    if plan['plan_sha256'] != digest({key: value for key, value in plan.items() if key != 'plan_sha256'}):
        raise ValueError('Frozen gold split plan changed')
    if labels['plan_sha256'] != plan['plan_sha256'] or predictions['inventory_sha256'] != plan['inventory_sha256'] or predictions['model_sha256'] != plan['model_sha256']:
        raise ValueError('Gold labels, predictions and frozen model plan do not match')
    annotated = {row['id']: row for row in labels['items']}
    proposed = {row['id']: row for row in predictions['records']}
    if len(proposed) != len(predictions['records']) or len({row['id'] for row in plan['items']}) != len(plan['items']):
        raise ValueError('Duplicate predictions or frozen audit identities')
    if len(annotated) != len(labels['items']) or set(annotated) != {row['id'] for row in plan['items']}:
        raise ValueError('Gold audit items were added, dropped or duplicated')
    tested, calibration, pending = [], [], []
    seen = set()
    for item in plan['items']:
        row, prediction = annotated[item['id']], proposed.get(item['id'])
        if item['decoded_audio_sha256'] in seen:
            raise ValueError('Duplicate audio crosses independent audit items')
        seen.add(item['decoded_audio_sha256'])
        for field in ('sha256', 'decoded_audio_sha256', 'label_identity', 'role', 'stratum', 'audio_path'):
            if row.get(field) != item.get(field):
                raise ValueError('Gold review changed the locked audio, split, or label identity')
        if item['role'] not in ('calibration', 'test') or len(item['stratum']) != 3 or not is_direct(item['audio_path']):
            raise ValueError('Invalid locked audit role or direct recording path')
        if item['role'] not in roles:
            continue
        reviews = row.get('reviews', [])
        if row.get('annotated_pattern') is None and not reviews:
            pending.append(item['id'])
            continue
        if (row.get('annotated_pattern') is not None and not valid_pattern(row['annotated_pattern'], item['stratum'][1])) or len(reviews) < 2:
            raise ValueError('Gold requires a complete pattern and two independent reviews')
        reviewer_ids = set()
        judgments = []
        for review in reviews:
            reviewer = str(review.get('reviewer', '')).strip().lower()
            if not reviewer or reviewer in reviewer_ids or review.get('method') != 'independent_listening':
                raise ValueError('Independent gold reviewer attestations are missing or duplicated')
            reviewer_ids.add(reviewer)
            if (review.get('sha256') != item['sha256'] or review.get('label_identity') != item['label_identity']
                    or review.get('heard_pattern') != row['annotated_pattern']
                    or not isinstance(review.get('identity_correct'), bool)
                    or not isinstance(review.get('clear_for_practice'), bool)):
                raise ValueError('Independent reviewers must agree on the exact audio and heard pattern')
            judgments.append((review['identity_correct'], review['clear_for_practice']))
        if len(set(judgments)) != 1:
            raise ValueError('Independent listeners disagree on identity or clarity')
        identity_correct, clear = judgments[0]
        if row['annotated_pattern'] is None and clear:
            raise ValueError('A clear-for-practice review requires a complete heard tone pattern')
        if not prediction or prediction['sha256'] != item['sha256'] or prediction['label_identity'] != item['label_identity']:
            raise ValueError('Missing or stale model prediction for the gold audio')
        if prediction.get('reference_overlap'):
            raise ValueError('Training audio cannot serve as independent calibration or test gold')
        result = {'source': item['stratum'][0], 'length': item['stratum'][1], 'supplied_stratum': item['stratum'][2],
                  'gold': row['annotated_pattern'], 'supplied': prediction['supplied_pattern'],
                  'blind': prediction.get('blind', {}).get('pattern'),
                  'usable_gold': identity_correct and clear,
                  'accepted': prediction['assessment'] == 'reference_supported'}
        (calibration if item['role'] == 'calibration' else tested).append(result)
    return tested, calibration, pending


def calibrate_gold(plan_path, labels_path, predictions_path, output):
    plan, labels, predictions = (json.loads(path.read_text()) for path in (plan_path, labels_path, predictions_path))
    _, calibration, pending = validated_gold(plan, labels, predictions, {'calibration'})
    sources = {}
    if plan['sampling'] == 'uniform_source_holdout' and not pending:
        for source in sorted({row['source'] for row in calibration}):
            selected = [row for row in calibration if row['source'] == source]
            if len(selected) < settings()['gold']['minimum_calibration_per_source']:
                continue
            correct = sum(row['usable_gold'] and row['supplied'] == row['gold'] for row in selected)
            sources[source] = {
                'source_label_accuracy': correct / len(selected), 'n': len(selected), 'correct': correct,
                'role': 'calibration', 'sampling': 'uniform_source_holdout',
                'scope': 'frozen unseen-audio candidate pool, not the entire world',
            }
    atomic_json(output, {
        'version': 1, 'model_sha256': plan['model_sha256'], 'inventory_sha256': plan['inventory_sha256'],
        'plan_sha256': plan['plan_sha256'], 'sampling': plan['sampling'],
        'pending_calibration_items': len(pending), 'calibration_items': len(calibration),
        'source_priors': sources, 'test_labels_used': False, 'production_admission': False,
    })


def evaluate_gold(plan_path, labels_path, predictions_path, output):
    plan, labels, predictions = (json.loads(path.read_text()) for path in (plan_path, labels_path, predictions_path))
    tested, calibration, pending = validated_gold(plan, labels, predictions, {'calibration', 'test'})
    per_stratum = {}
    for stratum in sorted({(row['source'], row['length']) for row in tested}):
        rows = [row for row in tested if (row['source'], row['length']) == stratum]
        accepted = [row for row in rows if row['accepted']]
        errors = sum(not row['usable_gold'] or row['supplied'] != row['gold'] for row in accepted)
        upper = 1. if not accepted or errors == len(accepted) else float(beta.ppf(
            settings()['gold']['confidence'], errors + 1, len(accepted) - errors))
        per_stratum[f'{stratum[0]}:{stratum[1]}'] = {
            'n': len(rows), 'accepted': len(accepted), 'wrong_accepted': errors,
            'exact_blind_accuracy': sum(row['usable_gold'] and row['blind'] == row['gold'] for row in rows) / len(rows),
            'accepted_error_upper_bound': upper,
        }
    ready = bool(per_stratum) and not pending and all(
        value['wrong_accepted'] == 0 and value['accepted'] >= settings()['gold']['minimum_test_per_stratum']
        and value['accepted_error_upper_bound'] <= settings()['gold']['maximum_error_upper_bound']
        for value in per_stratum.values()
    )
    per_pattern = {}
    required_patterns = {(item['stratum'][0], item['stratum'][1], item['stratum'][2])
                         for item in plan['items'] if item['role'] == 'test'}
    for source, length, pattern_label in sorted(required_patterns):
        rows = [row for row in tested if row['source'] == source and row['length'] == length and row['supplied_stratum'] == pattern_label]
        accepted = [row for row in rows if row['accepted']]
        errors = sum(not row['usable_gold'] or row['supplied'] != row['gold'] for row in accepted)
        upper = 1. if not accepted or errors == len(accepted) else float(beta.ppf(
            settings()['gold']['confidence'], errors + 1, len(accepted) - errors))
        per_pattern[f'{source}:{length}:{pattern_label}'] = {
            'n': len(rows), 'accepted': len(accepted), 'wrong_accepted': errors,
            'accepted_error_upper_bound': upper,
        }
        ready = ready and len(accepted) >= settings()['gold']['minimum_test_per_stratum'] and errors == 0 and upper <= settings()['gold']['maximum_error_upper_bound']
    atomic_json(output, {
        'version': 1, 'pending_reviews': len(pending), 'calibration_items': len(calibration), 'test_items': len(tested),
        'per_source_length': per_stratum, 'zero_observed_error_gate_passed': ready,
        'per_supplied_tone_pattern': per_pattern,
        'population_source_priors': None,
        'source_prior_reason': 'source priors require the separate calibration-only command and representative sampling',
        'production_admission': False,
        'gate_status': 'evidence_ready_for_separate_release_review' if ready else 'blocked_insufficient_or_failing_independent_gold',
    })


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    manifest = commands.add_parser('inventory')
    manifest.add_argument('--output', type=Path, required=True)
    train_command = commands.add_parser('train')
    train_command.add_argument('--inventory', type=Path, required=True)
    prepare_command = commands.add_parser('prepare')
    prepare_command.add_argument('--inventory', type=Path, required=True)
    score = commands.add_parser('score')
    score.add_argument('--inventory', type=Path, required=True)
    score.add_argument('--output', type=Path, required=True)
    score.add_argument('--source-priors', type=Path)
    single = commands.add_parser('score-file')
    single.add_argument('--audio', required=True, help='repository-relative direct audio path')
    single.add_argument('--syllables', nargs='+', required=True)
    single.add_argument('--original-pattern', required=True)
    single.add_argument('--word')
    single.add_argument('--output', type=Path, required=True)
    audit = commands.add_parser('audit-plan')
    audit.add_argument('--inventory', type=Path, required=True)
    audit.add_argument('--output', type=Path, required=True)
    audit.add_argument('--per-stratum', type=int, default=2)
    audit.add_argument('--sampling', choices=['challenge', 'source'], default='challenge')
    audit.add_argument('--per-source', type=int, default=120)
    evaluate = commands.add_parser('evaluate-gold')
    for name in ('plan', 'labels', 'predictions', 'output'):
        evaluate.add_argument('--' + name, type=Path, required=True)
    calibrate = commands.add_parser('calibrate-gold')
    for name in ('plan', 'labels', 'predictions', 'output'):
        calibrate.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        inventory = original_inventory()
        atomic_json(args.output, inventory)
        print(f'Preserved original labels and alternate hypotheses for {len(inventory["records"])} direct recording/readings.')
    elif args.command == 'train':
        train(args.inventory)
    elif args.command == 'prepare':
        prepare_inventory(args.inventory)
    elif args.command == 'score':
        score_inventory(args.inventory, args.output, args.source_priors)
    elif args.command == 'score-file':
        score_file(args.audio, args.syllables, args.original_pattern, args.word, args.output)
    elif args.command == 'audit-plan':
        if not 1 <= args.per_stratum <= 20:
            parser.error('--per-stratum must be between 1 and 20')
        if not 2 <= args.per_source <= 1000:
            parser.error('--per-source must be 2-1000')
        audit_plan(args.inventory, args.output, args.per_stratum, args.sampling, args.per_source)
    elif args.command == 'calibrate-gold':
        calibrate_gold(args.plan, args.labels, args.predictions, args.output)
    else:
        evaluate_gold(args.plan, args.labels, args.predictions, args.output)


if __name__ == '__main__':
    main()
