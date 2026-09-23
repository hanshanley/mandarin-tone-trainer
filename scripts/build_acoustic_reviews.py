#!/usr/bin/env python3
"""Compile explicit automated screening decisions; unresolved clips remain unavailable."""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from acoustic_analysis import VERSION, curve_features, decide, decide_neutral, segment, speaker_high_reference
from audit_native_readings import polyphonic_bases
from collect_acoustic_evidence import (
    ALIGNMENT_VERSION, ASR_VERSION, PREPARED_ASR_VERSION, PROFILE_VERSION,
    decoded_bases, identity_encoding, read_jsonl, resolved_recognition,
)
from runtime_data import read_recordings


ROOT = Path(__file__).resolve().parents[1]


def source_group(path):
    if path.startswith('audio/mandarin_native/excerpts/'):
        return 'mandarin_native_excerpts'
    if path.startswith('audio/pinyin_public/'):
        return 'pinyin_public'
    if path.startswith('audio/audio_cmn/syllabs/'):
        return 'audio_cmn_syllables'
    if path.startswith('audio/mandarin_native/'):
        return 'mandarin_native'
    return 'audio_cmn_words'


def label_identity(row):
    if row['kind'] == 'comparison':
        values = [row['kind'], row['audio_path'], row['key']]
    else:
        values = [
            row['kind'], row['audio_path'], row['word_id'], row['word'], row['pinyin'],
            row['pinyin_syllables'], row['lexical_pattern'], row['surface_pattern'],
        ]
        if row.get('source_segment'):
            values.append(row['source_segment'])
    return json.dumps(values, ensure_ascii=False, separators=(',', ':'))


def descriptor(row):
    keys = ['kind', 'audio_path']
    keys += ['key'] if row['kind'] == 'comparison' else [
        'word_id', 'word', 'pinyin', 'pinyin_syllables', 'lexical_pattern', 'surface_pattern',
    ]
    if row.get('source_segment'):
        keys.append('source_segment')
    return {key: row[key] for key in keys}


def evidence_matches(row, profile, recognition, prepared):
    return (
        profile and recognition and prepared
        and profile.get('evidence_version') == PROFILE_VERSION
        and recognition.get('evidence_version') == ASR_VERSION
        and prepared.get('evidence_version') == PREPARED_ASR_VERSION
        and row['sha256'] == profile.get('sha256') == recognition.get('sha256') == prepared.get('sha256')
    )


def syllable_intervals(recognition, count, duration):
    if count == 1:
        return [(0, duration)]
    timestamps = recognition.get('timestamps_ms') or []
    if len(timestamps) != count:
        return None
    boundaries = [0.0]
    previous = 0.0
    for index, timestamp in enumerate(timestamps):
        if not isinstance(timestamp, list) or len(timestamp) != 2:
            return None
        start, end = [value / 1000 for value in timestamp]
        if start < previous - .03 or end <= start or end > duration + .15:
            return None
        previous = end
        if index:
            boundary = (timestamps[index - 1][1] / 1000 + start) / 2
            if boundary <= boundaries[-1] or boundary >= duration:
                return None
            boundaries.append(boundary)
    boundaries.append(duration)
    return list(zip(boundaries, boundaries[1:]))


def compile_reviews(candidates, profiles, recognitions, recordings, alignments, prepared_recognitions):
    measured = {path: segment(profile) for path, profile in profiles.items()}
    levels = defaultdict(list)
    family = defaultdict(list)
    for row in candidates:
        measurement = measured.get(row['audio_path'], {})
        if measurement.get('status') != 'measured':
            continue
        group = source_group(row['audio_path'])
        for curve in measurement['curves'].values():
            features = curve_features(curve)
            if features['range'] <= 2.6 and abs(features['delta']) <= 1.8:
                levels[group].append(features['median_hz'])
            if row['kind'] == 'comparison':
                family[(group, row['key'][:-1])].extend(curve)
    registers = {group: float(np.quantile(values, .8)) for group, values in levels.items() if len(values) >= 5}
    family_registers = {key: float(np.quantile(values, .85)) for key, values in family.items()}
    approvals, findings = [], []
    reference_by_key = defaultdict(list)
    ordered = sorted(candidates, key=lambda row: row['kind'] != 'comparison')
    for row in ordered:
        path = row['audio_path']
        kind = row['kind']
        if kind not in ('native', 'comparison'):
            continue
        reason = None
        profile = profiles.get(path)
        raw_recognition = recognitions.get(path)
        prepared = prepared_recognitions.get(path)
        recognition = None
        recording = recordings.get(path, {})
        group = source_group(path)
        if not evidence_matches(row, profile, raw_recognition, prepared):
            reason = 'missing or stale acoustic/recognition evidence'
        elif profile['clipped_fraction'] > .01:
            reason = 'excessive waveform clipping'
        elif kind == 'comparison' and row.get('blocked_reason'):
            reason = 'known comparison quarantine'
        elif kind == 'native' and (
            recording.get('review_status') == 'rejected'
            or (recording.get('quiz_eligible') is False and not (
                recording.get('source') == 'mandarin_native'
                and recording.get('recording_type') in ('word_candidate', 'aligned_word')
                and recording.get('review_status') == 'pending'
            ))
        ):
            reason = 'known native recording quarantine'
        expected_bases = [row['key'][:-1]] if kind == 'comparison' else [
            base.replace('ü', 'v') for base in row['pinyin_syllables']
        ]
        if not reason:
            recognition, conflict = resolved_recognition(raw_recognition, prepared)
            if conflict:
                reason = conflict
        encoding = identity_encoding(recognition, expected_bases) if recognition else None
        if not reason and not encoding:
            reason = 'independent ASR did not confirm the expected syllables'
        phonetic_confirmation = any(
            identity_encoding(result, expected_bases) == 'literal_pinyin'
            for result in (raw_recognition, prepared) if result
        )
        if not reason and not phonetic_confirmation:
            ambiguous_word = row['word'] if kind == 'native' else recording.get('word', '') if group == 'audio_cmn_words' else ''
            if len(ambiguous_word) == 1 and len(polyphonic_bases(ambiguous_word)) > 1:
                reason = 'polyphonic single-character identity remains ambiguous'
        expected_tones = [row['key'][-1]] if kind == 'comparison' else row['surface_pattern'].split('-')
        if not reason and (expected_tones[0] == 'N' or any(
            tone == 'N' and expected_tones[index - 1] == 'N'
            for index, tone in enumerate(expected_tones) if index
        )):
            reason = 'neutral tone has no full-tone anchor in this word'
        if reason:
            findings.append({'kind': kind, 'audio_path': path, 'label_identity': label_identity(row), 'reason': reason})
            continue
        high = family_registers.get((group, expected_bases[0])) if kind == 'comparison' else registers.get(group)
        parent = row.get('source_segment')
        if parent:
            parent_profile = profiles.get(parent['audio_path'])
            if not parent_profile or parent_profile.get('sha256') != parent['sha256'] or parent_profile.get('evidence_version') != PROFILE_VERSION:
                findings.append({'kind': kind, 'audio_path': path, 'label_identity': label_identity(row),
                                 'reason': 'source sentence has no current pitch evidence'})
                continue
            high = speaker_high_reference(parent_profile)
        timing = recognition
        if len(expected_tones) > 1:
            timing = alignments.get(path, {})
            if timing.get('evidence_version') != ALIGNMENT_VERSION or timing.get('sha256') != row['sha256'] or timing.get('phonetic_bases') != expected_bases:
                timing = {}
        intervals = syllable_intervals(timing, len(expected_tones), profile['duration'])
        if intervals is None or high is None:
            findings.append({'kind': kind, 'audio_path': path, 'label_identity': label_identity(row),
                             'reason': 'no reliable syllable alignment or speaker-register reference'})
            continue
        measurements = [segment(profile, start, end, neutral=tone == 'N')
                        for (start, end), tone in zip(intervals, expected_tones)]
        decisions = []
        for index, (measurement, tone) in enumerate(zip(measurements, expected_tones)):
            if tone == 'N':
                decision = decide_neutral(measurement, measurements[index - 1],
                                          expected_tones[index - 1], high, profile['rms'])
            else:
                decision = decide(measurement, tone, high, connected=len(expected_tones) > 1 or bool(parent))
            decisions.append(decision)
        if any(decision['status'] != 'screened' for decision in decisions):
            findings.append({'kind': kind, 'audio_path': path, 'label_identity': label_identity(row),
                             'reason': 'tone evidence is ambiguous or contradicts the label', 'decisions': decisions})
            continue
        references = []
        if kind == 'native':
            for base, tone in zip(expected_bases, expected_tones):
                if tone == 'N':
                    references.append(None)
                    continue
                key = base + tone
                distinct = [
                    reference for reference in reference_by_key[key]
                    if reference['sha256'] != row['sha256']
                    and (group.startswith('mandarin_native') or reference['distribution_scope'] == 'redistributable')
                ]
                if not distinct:
                    reason = 'no distinct screened comparison for the expected tone'
                    break
                references.append({
                    'key': key,
                    'audio_path': distinct[0]['audio_path'],
                    'sha256': distinct[0]['sha256'],
                })
        if reason:
            findings.append({'kind': kind, 'audio_path': path, 'label_identity': label_identity(row), 'reason': reason})
            continue
        approval = {
            **descriptor(row),
            'status': 'screened',
            'assessment': 'automated',
            'sha256': row['sha256'],
            'source_url': row['source_url'],
            'license': row.get('license'),
            'distribution_scope': 'local_only' if group.startswith('mandarin_native') and recording.get('rights_status') != 'cleared' else 'redistributable',
            'evidence': {
                'method': VERSION,
                'audio_sha256': row['sha256'],
                'label_identity': label_identity(row),
                'identity_method': 'unprompted_paraformer',
                'identity_encoding': encoding,
                'asr_transcript': recognition['text'],
                'recognition_checks': [
                    {
                        'input': input_kind,
                        'audio_sha256': row['sha256'],
                        'evidence_version': result['evidence_version'],
                        'transcript': result['text'],
                        'decoded_bases': decoded_bases(result),
                        **({'preparation': result['preparation']} if input_kind == 'prepared' else {}),
                    }
                    for input_kind, result in [('raw', raw_recognition), ('prepared', prepared)]
                ],
                'recognized_bases': expected_bases,
                'alignment_method': 'whole_clip' if len(expected_tones) == 1 else ALIGNMENT_VERSION,
                'tones': decisions,
                'register_hz': round(high, 3),
                'comparison_support': references,
            },
        }
        approvals.append(approval)
        if kind == 'comparison':
            reference_by_key[row['key']].append(approval)
    return approvals, findings, registers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, default=ROOT / '.audit/acoustic-profiles.jsonl')
    parser.add_argument('--asr', type=Path, default=ROOT / '.audit/acoustic-asr.jsonl')
    parser.add_argument('--prepared-asr', type=Path, default=ROOT / '.audit/acoustic-prepared-asr.jsonl')
    parser.add_argument('--alignment', type=Path, default=ROOT / '.audit/acoustic-alignment.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/acoustic_reviews.json')
    parser.add_argument('--report', type=Path, default=ROOT / '.audit/acoustic-decisions.json')
    parser.add_argument('--allow-partial', action='store_true', help='diagnostic output only; refuses to write the runtime ledger')
    parser.add_argument('--activate-imported', action='store_true', help='enable acoustically screened standalone imports for local practice only')
    args = parser.parse_args()
    if args.allow_partial and args.output.resolve() == (ROOT / 'data/acoustic_reviews.json').resolve():
        parser.error('partial diagnostics must use a separate --output outside the runtime ledger')
    if args.allow_partial and args.activate_imported:
        parser.error('partial diagnostics cannot activate imported recordings')
    candidates = json.loads(args.candidates.read_text(encoding='utf-8'))['candidates']
    profiles, recognition = read_jsonl(args.profiles), read_jsonl(args.asr)
    prepared_recognition = read_jsonl(args.prepared_asr)
    required = [row for row in candidates if row['kind'] in ('native', 'comparison')]
    stale = [row['audio_path'] for row in required if not evidence_matches(
        row, profiles.get(row['audio_path']), recognition.get(row['audio_path']),
        prepared_recognition.get(row['audio_path']),
    )]
    if stale and not args.allow_partial:
        raise SystemExit(f'{len(set(stale))} clips lack current evidence; refusing an incomplete runtime ledger')
    raw_recordings = read_recordings()
    recordings = {row['audio_path']: row for row in raw_recordings}
    for row in required:
        if hashlib.sha256((ROOT / row['audio_path']).read_bytes()).hexdigest() != row['sha256']:
            raise SystemExit(f'Candidate changed since export: {row["audio_path"]}')
    approvals, findings, registers = compile_reviews(
        required, profiles, recognition, recordings, read_jsonl(args.alignment), prepared_recognition,
    )
    code_hash = hashlib.sha256(b''.join(
        (ROOT / 'scripts' / name).read_bytes()
        for name in ['build_acoustic_reviews.py', 'acoustic_analysis.py',
                     'collect_acoustic_evidence.py', 'audit_native_readings.py']
    )).hexdigest()
    ledger = {
        'version': 1,
        'method': VERSION,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'pipeline_sha256': code_hash,
        'certifies_accuracy': False,
        'recognition_policy': 'raw-prepared-no-phonetic-conflict-1',
        'coverage': {
            'scope': 'app_candidate_isolated_recordings',
            'unique_audio_files': len({row['audio_path'] for row in required}),
            'recording_label_candidates': len(required),
            'missing_current_evidence': len(set(stale)),
        },
        'approvals': approvals,
    }
    for entry in approvals:
        entry['evidence']['pipeline_sha256'] = code_hash
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.json.part')
    temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.output)
    if args.activate_imported:
        eligible = {entry['audio_path'] for entry in approvals if entry['kind'] == 'native'}
        eligible.update(entry['audio_path'] for entry in approvals if entry['kind'] == 'comparison')
        for filename in ('mandarin_native_recordings.json', 'context_word_recordings.json'):
            imported_path = ROOT / 'data' / filename
            imported = json.loads(imported_path.read_text(encoding='utf-8'))
            for recording in imported['recordings']:
                if recording['recording_type'] not in ('word_candidate', 'aligned_word') or recording.get('review_status') not in ('pending', 'acoustic_screened'):
                    continue
                recording['quiz_eligible'] = recording['audio_path'] in eligible
                recording['review_status'] = 'acoustic_screened' if recording['quiz_eligible'] else 'pending'
            temporary = imported_path.with_suffix('.json.part')
            temporary.write_text(json.dumps(imported, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(imported_path)
    report = {
        'method': VERSION, 'certifies_accuracy': False, 'registers_hz': registers,
        'pipeline_sha256': code_hash,
        'screened': dict(Counter(row['kind'] for row in approvals)),
        'native_tone_coverage': dict(Counter(
            tone for row in approvals if row['kind'] == 'native'
            for tone in row['surface_pattern'].split('-')
        )),
        'withheld': dict(Counter(row['reason'] for row in findings)), 'findings': findings,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'findings'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
