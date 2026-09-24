#!/usr/bin/env python3
"""Expand direct-source tone references additively, with held-family-out evidence."""
import argparse
import gzip
import hashlib
import json
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from acoustic_analysis import METHODS
from audit_correction_similarity import normalized_waveform, similarity
from collect_acoustic_evidence import (
    ASR_VERSION, PREPARED_ASR_VERSION, PROFILE_VERSION, decoded_bases,
    identity_encoding, read_jsonl, resolved_recognition,
)
from native_tone_labels import canonical, digest, safe_audio, verify_inventory
from native_tone_validator import audio_features, export_forest, fit_temperature, forest_predict, temperature_scale
from runtime_data import ROOT
from verify_comparison_identity import VERSION as IDENTITY_VERSION, no_primary_contradiction


CONFIG = ROOT / 'config/mixed_audio_bank.json'
REPORT = ROOT / 'data/mixed_audio_coverage.json'
MODEL = ROOT / 'data/mixed_audio_reference_models.json.gz'
METHOD = 'cross-source-native-reference-v1'
WORD_METHOD = 'cross-source-whole-word-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_family(source):
    return 'audio_cmn' if source in ('audio_cmn', 'audio_cmn_syllables') else source


def identity(entry):
    if entry['kind'] == 'comparison':
        value = ['comparison', entry['audio_path'], entry['key']]
    else:
        value = ['native', entry['audio_path'], entry['word_id'], entry['word'], entry['pinyin'],
                 entry['pinyin_syllables'], entry['lexical_pattern'], entry['surface_pattern']]
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def candidate_groups(rows):
    parent = {}

    def root(key):
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = root(parent[key])
        return parent[key]

    def union(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)

    for row in rows:
        union('base:' + row['syllables'][0], 'audio:' + row['decoded_audio_sha256'])
    return {row['id']: root('base:' + row['syllables'][0]) for row in rows}


def features_and_quality(profile):
    features, diagnostics = audio_features(profile, [(0, profile['duration'])])
    tracks = [profile['tracks'][method] for method in METHODS]
    jointly_voiced = sum(sum(value is not None for value in frame) >= 2 for frame in zip(*tracks))
    details = diagnostics[0]
    return features, {
        'jointly_voiced_frames': jointly_voiced,
        'usable_trackers': details['usable_pitch_trackers'],
        'tracker_difference': details['minimum_tracker_difference'],
        'clipped_fraction': profile['clipped_fraction'],
    }


def accepted_prediction(row, result, options):
    return (
        result['predicted_tone'] == row['weak_training_label']
        and result['expected_probability'] >= options['minimum_probability']
        and result['margin'] >= options['minimum_margin']
        and result['quality']['jointly_voiced_frames'] >= options['minimum_voiced_frames']
        and result['quality']['usable_trackers'] >= 2
        and result['quality']['tracker_difference'] <= options['maximum_tracker_difference_semitones']
        and result['quality']['clipped_fraction'] <= .01
        and result['identity_supported'] is True
        and result['training_overlap'] is False
        and row['quarantined'] is False
    )


def fingerprint():
    return digest({
        'config': sha(CONFIG),
        'code': {name: sha(ROOT / 'scripts' / name) for name in (
            'build_mixed_audio_bank.py', 'native_tone_validator.py',
            'native_tone_labels.py', 'collect_acoustic_evidence.py', 'audit_correction_similarity.py',
            'verify_comparison_identity.py',
        )},
    })


def whole_word_supported(row, diagnostic):
    blind = diagnostic.get('blind', {})
    return (
        row['kind'] == 'word' and row['source'] == 'mandarin_native'
        and len(row['syllables']) == 2 and not row['quarantined'] and 'N' not in row['weak_training_label']
        and diagnostic.get('assessment') == 'reference_supported' and diagnostic.get('reference_overlap') is False
        and diagnostic.get('identity_supported') is True and diagnostic.get('sha256') == row['sha256']
        and diagnostic.get('label_identity') == row['label_identity']
        and blind.get('pattern') == row['weak_training_label'] and blind.get('boundary_stable') is True
        and blind.get('quality_ok') is True and .95 <= blind.get('minimum_top_probability', 0) <= 1
        and .25 <= blind.get('minimum_margin', 0) <= 1
        and isinstance(blind.get('boundary_variants'), int) and blind['boundary_variants'] >= 3
    )


def fit_cross_source_models(inventory):
    from sklearn.ensemble import ExtraTreesClassifier

    options = json.loads(CONFIG.read_text())
    profiles = read_jsonl(ROOT / '.audit/acoustic-profiles.jsonl')
    raw = read_jsonl(ROOT / '.audit/acoustic-asr.jsonl')
    prepared = read_jsonl(ROOT / '.audit/acoustic-prepared-asr.jsonl')
    secondary = read_jsonl(ROOT / '.audit/comparison-independent-identity.jsonl')
    rows = [row for row in inventory['records'] if len(row['syllables']) == 1 and row['weak_training_label'] in ('1', '2', '3', '4')]
    groups = candidate_groups(rows)
    fold_by_group = {group: int(digest([options['seed'], group])[:12], 16) % options['folds'] for group in set(groups.values())}
    labels = defaultdict(set)
    for row in rows:
        labels[row['decoded_audio_sha256']].add(row['weak_training_label'])
    examples, outcomes, seen = [], {}, set()
    for row in rows:
        if row['audio_path'] not in profiles:
            outcomes[row['id']] = {'status': 'unresolved', 'reason': 'missing_pitch_profile'}
            continue
        profile = profiles[row['audio_path']]
        if profile.get('evidence_version') != PROFILE_VERSION or profile['sha256'] != row['sha256']:
            outcomes[row['id']] = {'status': 'unresolved', 'reason': 'stale_pitch_profile'}
            continue
        if len(labels[row['decoded_audio_sha256']]) != 1 or row['quarantined']:
            outcomes[row['id']] = {'status': 'unresolved', 'reason': 'conflicting_labels_or_known_quarantine'}
            continue
        try:
            features, quality = features_and_quality(profile)
        except ValueError as error:
            outcomes[row['id']] = {'status': 'unresolved', 'reason': str(error)}
            continue
        row = {**row, 'features': features, 'quality': quality, 'group': groups[row['id']],
               'fold': fold_by_group[groups[row['id']]]}
        examples.append(row)
    unique = []
    for row in examples:
        if row['decoded_audio_sha256'] not in seen:
            unique.append(row)
            seen.add(row['decoded_audio_sha256'])
    models, metrics = [], []
    for fold in range(options['folds']):
        calibration_fold = (fold + 1) % options['folds']
        training = [row for row in unique if row['fold'] not in (fold, calibration_fold)]
        calibration = [row for row in unique if row['fold'] == calibration_fold]
        testing = [row for row in examples if row['fold'] == fold]
        if any(set(row['weak_training_label'] for row in partition) != {'1', '2', '3', '4'}
               for partition in (training, calibration)):
            raise ValueError('A cross-fit partition lacks one of the four reference tones')
        training_groups = {row['group'] for row in training}
        calibration_groups = {row['group'] for row in calibration}
        test_groups = {row['group'] for row in testing}
        if training_groups & calibration_groups or training_groups & test_groups or calibration_groups & test_groups:
            raise ValueError('Base-family/duplicate leakage in mixed-source cross-fitting')
        model = ExtraTreesClassifier(**options['forest'], max_features=.8, class_weight='balanced', n_jobs=2)
        model.fit(np.asarray([row['features'] for row in training]), [row['weak_training_label'] for row in training])
        classes = model.classes_.tolist()
        calibration_raw = model.predict_proba(np.asarray([row['features'] for row in calibration]))
        temperature = fit_temperature(calibration_raw, np.array([classes.index(row['weak_training_label']) for row in calibration]))
        test_matrix = np.asarray([row['features'] for row in testing])
        trees = export_forest(model)
        raw_probabilities = forest_predict(trees, test_matrix)
        if not np.allclose(raw_probabilities, model.predict_proba(test_matrix), atol=1e-12):
            raise ValueError('Portable cross-fit predictions differ')
        probabilities = temperature_scale(raw_probabilities, temperature)
        model_record = {
            'fold': fold, 'calibration_fold': calibration_fold, 'classes': classes,
            'temperature': temperature, 'trees': trees,
            'training_groups': sorted(training_groups), 'calibration_groups': sorted(calibration_groups),
            'test_groups': sorted(test_groups),
            'training_audio_sha256s': sorted({row['sha256'] for row in training}),
            'training_pcm_sha256s': sorted({row['decoded_audio_sha256'] for row in training}),
        }
        model_sha = digest(model_record)
        models.append(model_record)
        correct = 0
        for row, probability in zip(testing, probabilities):
            expected = classes.index(row['weak_training_label'])
            predicted = classes[int(np.argmax(probability))]
            correct += predicted == row['weak_training_label']
            ordered = np.sort(probability)
            a, b = raw.get(row['audio_path']), prepared.get(row['audio_path'])
            recognized, conflict = (None, 'missing recognition evidence')
            if (a and b and a.get('sha256') == b.get('sha256') == row['sha256']
                    and a.get('evidence_version') == ASR_VERSION and b.get('evidence_version') == PREPARED_ASR_VERSION):
                recognized, conflict = resolved_recognition(a, b)
            identity_ok = bool(recognized and identity_encoding(recognized, row['syllables']))
            recognition_checks = [
                {'input': kind, 'audio_sha256': row['sha256'], 'transcript': evidence['text'],
                 'decoded_bases': decoded_bases(evidence)}
                for kind, evidence in [('raw', a), ('prepared', b)] if evidence
            ]
            primary_checks = recognition_checks
            identity_method = 'paraformer-raw-prepared'
            identity_model_sha = None
            alternative = secondary.get(row['audio_path'])
            if (not identity_ok and a and b and a.get('sha256') == b.get('sha256') == row['sha256']
                    and a.get('evidence_version') == ASR_VERSION and b.get('evidence_version') == PREPARED_ASR_VERSION
                    and no_primary_contradiction(a, b, row['syllables'][0])
                    and alternative and alternative.get('sha256') == row['sha256']
                    and alternative.get('version') == IDENTITY_VERSION and alternative.get('identity_supported') is True
                    and len(alternative.get('checks', [])) == 2
                    and all(check['decoded_bases'] == row['syllables'] and check['minimum_log_probability'] >= -1.
                            for check in alternative['checks'])):
                identity_ok = True
                recognition_checks = alternative['checks']
                identity_method = IDENTITY_VERSION
                identity_model_sha = alternative['model_sha256']
            result = {
                'audio_path': row['audio_path'], 'sha256': row['sha256'],
                'decoded_sha256': row['decoded_audio_sha256'], 'key': row['syllables'][0] + row['weak_training_label'],
                'source': source_family(row['source']), 'fold': fold, 'family': row['group'],
                'model_sha256': model_sha, 'expected_tone': row['weak_training_label'],
                'predicted_tone': predicted, 'expected_probability': float(probability[expected]),
                'margin': float(ordered[-1] - ordered[-2]), 'training_overlap': False,
                'identity_supported': identity_ok, 'identity_reason': conflict, 'quality': row['quality'],
                'identity_method': identity_method, 'identity_model_sha256': identity_model_sha,
                'recognition_checks': recognition_checks,
                'primary_recognition_checks': primary_checks,
            }
            result['status'] = 'candidate_supported' if accepted_prediction(row, result, options) else 'unresolved'
            if not identity_ok:
                result['reason'] = 'identity_unresolved'
            elif predicted != row['weak_training_label']:
                result['reason'] = 'source_label_and_blind_tone_disagree'
            elif result['status'] == 'unresolved':
                result['reason'] = 'tone_confidence_or_signal_quality_unresolved'
            outcomes[row['id']] = result
        metrics.append({'fold': fold, 'training': len(training), 'calibration': len(calibration),
                        'test': len(testing), 'source_label_agreement': correct / len(testing)})
        print(f'Cross-fit fold {fold}: {len(testing)} held-family-out references; source-label agreement={correct / len(testing):.4f}', flush=True)
    model_bundle = {
        'version': 1, 'method': METHOD, 'pipeline_sha256': fingerprint(),
        'inventory_sha256': inventory['inventory_sha256'], 'models': models,
        'independent_accuracy_verified': False, 'metrics': metrics,
    }
    return rows, outcomes, model_bundle


def supported_groups(rows, outcomes):
    grouped = defaultdict(dict)
    for row in rows:
        result = outcomes.get(row['id'], {})
        if result.get('status') == 'candidate_supported':
            grouped[result['key']][row['audio_path']] = (row, result)
    return grouped


def cross_source_pairs(candidates, options):
    waves, output = {}, {}
    for key, paths in candidates.items():
        items = list(paths.values())
        for row, result in items:
            for other, evidence in items:
                if source_family(row['source']) == source_family(other['source']) or row['decoded_audio_sha256'] == other['decoded_audio_sha256']:
                    continue
                for item in (row, other):
                    if item['audio_path'] not in waves:
                        waves[item['audio_path']] = normalized_waveform(safe_audio(item['audio_path']))
                score = similarity(waves[row['audio_path']], waves[other['audio_path']])
                if score >= options['maximum_duplicate_similarity']:
                    continue
                output[(key, row['audio_path'])] = {
                    'audio_path': other['audio_path'], 'sha256': other['sha256'],
                    'decoded_sha256': other['decoded_audio_sha256'], 'key': key,
                    'source': source_family(other['source']),
                    'waveform_similarity': float(score),
                    'cross_fit': evidence,
                }
                break
    return output


def build(inventory_path, predictions_output):
    inventory = verify_inventory(json.loads(inventory_path.read_text()))
    options = json.loads(CONFIG.read_text())
    ledger_path = ROOT / 'data/acoustic_reviews.json'
    ledger = json.loads(ledger_path.read_text())
    baseline_ids = {identity(entry) for entry in ledger['approvals']}
    baseline_words = {entry['word_id'] for entry in ledger['approvals'] if entry['kind'] == 'native'}
    original_word_references = defaultdict(list)
    for entry in ledger['approvals']:
        if (entry['kind'] == 'native' and entry['evidence']['method'] == 'spectral-consensus-1'
                and entry['audio_path'].startswith('audio/audio_cmn/')):
            original_word_references[(entry['word_id'], entry['surface_pattern'])].append(entry)
    rows, outcomes, model_bundle = fit_cross_source_models(inventory)
    grouped = supported_groups(rows, outcomes)
    pairs = cross_source_pairs(grouped, options)
    existing = {identity(entry): entry for entry in ledger['approvals']}
    model_payload = gzip.compress(canonical(model_bundle).encode(), mtime=0)
    model_digest = hashlib.sha256(model_payload).hexdigest()
    added = []
    for key, candidates in grouped.items():
        for row, result in candidates.values():
            peer = pairs.get((key, row['audio_path']))
            if not peer:
                continue
            descriptor = {'kind': 'comparison', 'audio_path': row['audio_path'], 'key': key}
            entry_identity = identity(descriptor)
            if entry_identity in existing and existing[entry_identity]['evidence']['method'] != METHOD:
                continue
            entry = {
                **descriptor, 'status': 'screened', 'assessment': 'automated',
                'sha256': row['sha256'], 'source_url': row['source_url'], 'license': row['license'],
                'distribution_scope': 'local_only' if source_family(row['source']) == 'mandarin_native' else 'redistributable',
                'evidence': {
                    'method': METHOD, 'pipeline_sha256': model_bundle['pipeline_sha256'],
                    'audio_sha256': row['sha256'], 'label_identity': entry_identity,
                    'original_key': key, 'source': source_family(row['source']),
                    'model_bundle_sha256': model_digest, 'cross_fit': result,
                    'corroboration': peer, 'independent_gold_accuracy_claimed': False,
                },
            }
            existing[entry_identity] = entry
            if entry_identity not in baseline_ids:
                added.append(entry)
    added_native = []
    for row in rows:
        if row['kind'] != 'word' or row['quarantined']:
            continue
        key = row['syllables'][0] + row['weak_training_label']
        comparison = existing.get(identity({'kind': 'comparison', 'audio_path': row['audio_path'], 'key': key}))
        peer = pairs.get((key, row['audio_path']))
        result = outcomes.get(row['id'])
        if not comparison or not peer or not result or result.get('status') != 'candidate_supported':
            continue
        descriptor = {
            'kind': 'native', 'audio_path': row['audio_path'], 'word_id': row['word_id'],
            'word': row['word'], 'pinyin': row['pinyin'], 'pinyin_syllables': row['syllables'],
            'lexical_pattern': row['original_labels']['lexical'], 'surface_pattern': row['weak_training_label'],
        }
        entry_identity = identity(descriptor)
        if entry_identity in existing and existing[entry_identity]['evidence']['method'] != METHOD:
            continue
        evidence = {
            'method': METHOD, 'pipeline_sha256': model_bundle['pipeline_sha256'],
            'audio_sha256': row['sha256'], 'label_identity': entry_identity,
            'original_key': key, 'source': source_family(row['source']),
            'model_bundle_sha256': model_digest, 'cross_fit': result,
            'corroboration': peer, 'independent_gold_accuracy_claimed': False,
            'comparison_support': [{'audio_path': peer['audio_path'], 'sha256': peer['sha256'], 'key': key}],
        }
        entry = {
            **descriptor, 'status': 'screened', 'assessment': 'automated', 'sha256': row['sha256'],
            'source_url': row['source_url'], 'license': row['license'],
            'distribution_scope': 'local_only' if source_family(row['source']) == 'mandarin_native'
                                  or peer['source'] == 'mandarin_native' else 'redistributable',
            'evidence': evidence,
        }
        existing[entry_identity] = entry
        if entry_identity not in baseline_ids:
            added_native.append(entry)
    native_predictions_path = ROOT / '.audit/native-tone-delivery-predictions.json'
    native_model_path = ROOT / 'data/native_tone_validator.json.gz'
    if native_predictions_path.is_file():
        word_predictions = json.loads(native_predictions_path.read_text())
        if word_predictions['inventory_sha256'] != inventory['inventory_sha256'] or word_predictions['model_sha256'] != sha(native_model_path):
            raise ValueError('Whole-word source-agreement predictions are stale')
        predicted = {item['id']: item for item in word_predictions['records']}
        by_path = {row['audio_path']: row for row in inventory['records']}
        raw = read_jsonl(ROOT / '.audit/acoustic-asr.jsonl')
        prepared = read_jsonl(ROOT / '.audit/acoustic-prepared-asr.jsonl')
        waves = {}
        for row in inventory['records']:
            diagnostic = predicted.get(row['id'], {})
            blind = diagnostic.get('blind', {})
            if not whole_word_supported(row, diagnostic):
                continue
            descriptor = {
                'kind': 'native', 'audio_path': row['audio_path'], 'word_id': row['word_id'],
                'word': row['word'], 'pinyin': row['pinyin'], 'pinyin_syllables': row['syllables'],
                'lexical_pattern': row['original_labels']['lexical'], 'surface_pattern': row['weak_training_label'],
            }
            entry_identity = identity(descriptor)
            if entry_identity in existing and existing[entry_identity]['evidence']['method'] != WORD_METHOD:
                continue
            for reference in original_word_references[(row['word_id'], row['weak_training_label'])]:
                peer = by_path.get(reference['audio_path'])
                if not peer or peer['sha256'] != reference['sha256'] or peer['decoded_audio_sha256'] == row['decoded_audio_sha256']:
                    continue
                if (reference['pinyin_syllables'] != row['syllables'] or reference['word'] != row['word']
                        or reference['lexical_pattern'] != row['original_labels']['lexical']):
                    continue
                for item in (row, peer):
                    if item['audio_path'] not in waves:
                        waves[item['audio_path']] = normalized_waveform(safe_audio(item['audio_path']))
                score = similarity(waves[row['audio_path']], waves[peer['audio_path']])
                if score >= options['maximum_duplicate_similarity']:
                    continue
                a, b = raw.get(row['audio_path']), prepared.get(row['audio_path'])
                if (not a or not b or a.get('sha256') != b.get('sha256') or a.get('sha256') != row['sha256']
                        or a.get('evidence_version') != ASR_VERSION or b.get('evidence_version') != PREPARED_ASR_VERSION):
                    continue
                recognized, conflict = resolved_recognition(a, b)
                if conflict or not recognized or not identity_encoding(recognized, row['syllables']):
                    continue
                checks = [{'input': kind, 'audio_sha256': row['sha256'], 'transcript': item['text'],
                           'decoded_bases': decoded_bases(item)} for kind, item in [('raw', a), ('prepared', b)]]
                entry = {
                    **descriptor, 'status': 'screened', 'assessment': 'automated', 'sha256': row['sha256'],
                    'source_url': row['source_url'], 'license': row['license'], 'distribution_scope': 'local_only',
                    'evidence': {
                        'method': WORD_METHOD, 'pipeline_sha256': model_bundle['pipeline_sha256'],
                        'audio_sha256': row['sha256'], 'label_identity': entry_identity,
                        'native_model_sha256': word_predictions['model_sha256'],
                        'supplied_pattern': row['weak_training_label'], 'blind_pattern': blind['pattern'],
                        'minimum_probability': blind['minimum_top_probability'], 'minimum_margin': blind['minimum_margin'],
                        'boundary_variants': blind['boundary_variants'], 'boundary_stable': True, 'quality_ok': True,
                        'training_overlap': False, 'decoded_sha256': row['decoded_audio_sha256'],
                        'recognition_checks': checks,
                        'whole_word_reference': {'identity': identity(reference), 'audio_path': reference['audio_path'],
                                                 'sha256': reference['sha256'], 'decoded_sha256': peer['decoded_audio_sha256'],
                                                 'waveform_similarity': score},
                        'comparison_support': reference['evidence']['comparison_support'],
                        'independent_gold_accuracy_claimed': False,
                    },
                }
                existing[entry_identity] = entry
                if entry_identity not in baseline_ids:
                    added_native.append(entry)
                break
        ledger.setdefault('supplemental_pipelines', {})[WORD_METHOD] = {
            'pipeline_sha256': model_bundle['pipeline_sha256'],
            'native_model_sha256': word_predictions['model_sha256'],
            'minimum_probability': .95, 'minimum_margin': .25, 'independent_gold_accuracy_claimed': False,
        }
    ledger.setdefault('supplemental_pipelines', {})[METHOD] = {
        'pipeline_sha256': model_bundle['pipeline_sha256'], 'model_bundle_sha256': model_digest,
        'minimum_probability': options['minimum_probability'], 'minimum_margin': options['minimum_margin'],
        'minimum_distinct_sources': options['minimum_distinct_sources'],
        'independent_gold_accuracy_claimed': False,
    }
    ledger['approvals'] = list(existing.values())
    if not baseline_ids <= set(existing):
        raise ValueError('Mixed-source expansion may not remove existing assessments')
    # All imported files are accounted for even when they have no eligible role.
    imported = json.loads((ROOT / 'data/mandarin_native_recordings.json').read_text())
    coverage = []
    for record in imported['recordings']:
        if record['recording_type'] != 'word_candidate':
            continue
        approvals = [entry for entry in ledger['approvals'] if entry['audio_path'] == record['audio_path']]
        roles = sorted({entry['kind'] for entry in approvals})
        if record['review_status'] in ('pending', 'acoustic_screened', 'source_corroborated') and approvals:
            record['quiz_eligible'] = True
            if any(entry['evidence']['method'] in (METHOD, WORD_METHOD) for entry in approvals):
                record['review_status'] = 'source_corroborated'
        related = [result for result in outcomes.values() if result.get('audio_path') == record['audio_path']]
        coverage.append({
            'audio_path': record['audio_path'], 'sha256': record['sha256'],
            'source_audio_key': record['source_audio_key'], 'candidate_word_ids': record['candidate_hsk_ids'],
            'assessed_roles': roles,
            'comparison_results': [{'key': result.get('key'), 'status': result.get('status'), 'reason': result.get('reason')}
                                   for result in related],
            'unresolved_reason': None if roles else 'no_supported_direct_recording_role',
        })
    # Validate the proposed data through the actual runtime policy before publishing it.
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=ROOT / '.audit', encoding='utf-8') as staged:
        json.dump({'ledger': ledger, 'imported': imported}, staged, ensure_ascii=False)
        staged.flush()
        script = """
import fs from 'node:fs';
import {validateAudioUpdate} from './scripts/review_audio.mjs';
const staged=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
process.stdout.write(JSON.stringify(validateAudioUpdate(staged)));
"""
        impact = json.loads(subprocess.check_output(
            ['node', '--input-type=module', '-e', script, staged.name], cwd=ROOT, text=True,
        ))
    report = {
        'version': 1, 'method': METHOD, 'pipeline_sha256': model_bundle['pipeline_sha256'],
        'baseline_native_assessment_word_ids': sorted(baseline_words),
        'new_comparison_assessments': len(added),
        'new_initial_recording_assessments': len(added_native),
        'runtime_impact': impact,
        'total_mixed_comparison_assessments': sum(entry['kind'] == 'comparison' and entry['evidence']['method'] == METHOD for entry in ledger['approvals']),
        'new_comparison_keys': sorted({entry['key'] for entry in added}),
        'new_imported_comparison_files': sorted({entry['audio_path'] for entry in added if entry['audio_path'].startswith('audio/mandarin_native/')}),
        'imported_standalone_files': coverage, 'independent_accuracy_verified': False,
    }
    temporary_model = MODEL.with_suffix(MODEL.suffix + '.part')
    temporary_model.write_bytes(model_payload)
    temporary_model.replace(MODEL)
    for path, value in ((ROOT / 'data/mandarin_native_recordings.json', imported), (ledger_path, ledger), (REPORT, report),
                        (predictions_output, {'inventory_sha256': inventory['inventory_sha256'], 'outcomes': outcomes})):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.part')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        temporary.replace(path)
    print(f'Added {len(added)} comparison and {len(added_native)} initial assessments with held-family-out evidence and distinct-source corroboration.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--predictions-output', type=Path, default=ROOT / '.audit/mixed-audio-cross-fit.json')
    args = parser.parse_args()
    build(args.inventory, args.predictions_output)


if __name__ == '__main__':
    main()
