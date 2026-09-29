#!/usr/bin/env python3
"""Review every active quiz file and prefer clear citation tones across all sources."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from acoustic_analysis import curve_features, decide, decide_neutral, segment
from build_acoustic_reviews import syllable_intervals
from collect_acoustic_evidence import ALIGNMENT_VERSION, PROFILE_VERSION, read_jsonl
from native_tone_labels import verify_inventory
from review_imported_third_clarity import clarity as third_clarity
from runtime_data import ROOT, read_recordings

METHOD = 'all-source-isolated-clarity-1'


def isolated_clarity(profile, tone):
    if tone == '3':
        review = third_clarity(profile)
        review['method'] = METHOD
        review['status'] = 'clear_citation_tone' if review['status'] == 'clear_citation_third' else 'needs_clearer_citation'
        return review
    measured = segment(profile)
    features = {method: curve_features(curve) for method, curve in measured.get('curves', {}).items()}

    def agrees(feature):
        if tone == '1':
            return feature['range'] <= 2.6 and abs(feature['delta']) <= 1.8
        if tone == '2':
            return feature['delta'] >= 2.2 and feature['dip'] <= 2 and feature['trough'] <= .4
        if tone == '4':
            return feature['delta'] <= -3.2 and feature['rebound'] <= 1.8 and feature['trough'] >= .6
        raise ValueError('Only isolated full tones can be reviewed here')

    clear = measured['status'] == 'measured' and len(features) >= 2 and all(agrees(value) for value in features.values())
    return {
        'method': METHOD, 'status': 'clear_citation_tone' if clear else 'needs_clearer_citation',
        'reason': 'Continuous citation contour agrees across usable trackers.' if clear else
            measured.get('reason', 'The expected isolated citation contour is not established.'),
        'measurement': measured, 'features': features,
        'source_label_changed': False, 'linguistic_error_established': False,
    }


def current_quiz(quality=None):
    script = """
import fs from 'node:fs';
import {loadReviewData,validateLedger,quizInventory,practiceInventory} from './scripts/review_audio.mjs';
import R from './app/audio_review.js';
import C from './app/correction_audio.js';
const data=loadReviewData();
if(process.argv[1])data.quality=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const index=validateLedger(data),quiz=quizInventory(data,index),library=practiceInventory(data,index);
const selected=new Map(),words=new Map(data.words.map(word=>[word.id,word])),records=new Map(data.recordings.map(r=>[r.audio_path,r]));
for(const pair of quiz.recordingLabelPairs){
  const approval=R.nativeApproval(index,words.get(pair.word_id),records.get(pair.audio_path));
  selected.set(R.identity(approval),approval);
}
for(const id of quiz.eligibleWords)for(const base of words.get(id).pinyin_syllables){
  for(const tone of ['1','2','3','4'])for(const mode of R.COMPARISON_SOURCES){
    const clip=R.correctionSelection(C,C.correctionKey(base,tone),data.quality,data.publicRecordings,index,mode);
    if(!clip)throw new Error('Incomplete quiz reference family');
    selected.set(R.identity(clip.approval),clip.approval);
  }
  const neutral=R.neutralSelection(index,base);
  if(neutral)selected.set(R.identity(neutral),neutral);
}
process.stdout.write(JSON.stringify({entries:quiz.eligibleWords,initial_examples:quiz.recordingLabelPairs,
  library_entries:library.eligibleWords.length,assessments:[...selected.values()],tone_coverage:quiz.toneCoverage}));
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', encoding='utf-8') as staged:
        if quality is not None:
            json.dump(quality, staged, ensure_ascii=False)
            staged.flush()
        return json.loads(subprocess.check_output(
            ['node', '--input-type=module', '-e', script, *([staged.name] if quality is not None else [])],
            cwd=ROOT, text=True,
        ))


def check_word(assessment, profile, alignment):
    tones = assessment['surface_pattern'].split('-')
    if not alignment or alignment.get('sha256') != assessment['sha256'] or alignment.get('evidence_version') != ALIGNMENT_VERSION:
        raise ValueError(f"Stale whole-word alignment: {assessment['audio_path']}")
    if alignment.get('phonetic_bases') != [base.replace('ü', 'v') for base in assessment['pinyin_syllables']]:
        raise ValueError('Whole-word alignment does not match the expected syllables')
    intervals = syllable_intervals(alignment, len(tones), profile['duration'])
    if intervals is None:
        raise ValueError('Unusable whole-word alignment')
    measurements = [segment(profile, start, end, neutral=tone == 'N')
                    for (start, end), tone in zip(intervals, tones)]
    evidence = assessment['evidence']
    if evidence['method'] == 'spectral-consensus-1':
        high = evidence['register_hz']
        decisions = [
            decide_neutral(measurement, measurements[index - 1], tones[index - 1], high, profile['rms'])
            if tone == 'N' else decide(measurement, tone, high, connected=True)
            for index, (measurement, tone) in enumerate(zip(measurements, tones))
        ]
        supported = all(decision['status'] == 'screened' for decision in decisions)
    else:
        decisions = measurements
        supported = all(measurement['status'] == 'measured' for measurement in measurements)
    return {
        'pattern': assessment['surface_pattern'], 'word_id': assessment['word_id'],
        'status': 'existing_evidence_reproduced' if supported else 'needs_further_review',
        'route': evidence['method'], 'syllable_checks': decisions,
        'independent_linguistic_certification': False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', required=True, type=Path)
    args = parser.parse_args()
    inventory = verify_inventory(json.loads(args.inventory.read_text()))
    profiles = read_jsonl(ROOT / '.audit/acoustic-profiles.jsonl')
    alignments = read_jsonl(ROOT / '.audit/acoustic-alignment.jsonl')
    quality_path = ROOT / 'data/correction_audio_quality.json'
    quality = json.loads(quality_path.read_text())
    before = current_quiz()
    candidates = defaultdict(list)
    for row in inventory['records']:
        if len(row['syllables']) == 1 and row['weak_training_label'] in ('1', '2', '3', '4'):
            candidates[row['audio_path']].append(row)
    for recording in read_recordings():
        key = recording.get('comparison_key')
        if recording['source'] == 'sinosplice' and key and key[-1] in ('1', '2', '3', '4'):
            candidates[recording['audio_path']].append({
                'audio_path': recording['audio_path'], 'sha256': recording['sha256'],
                'syllables': [key[:-1]], 'weak_training_label': key[-1],
            })
    reviews = {}
    for path, rows in candidates.items():
        row = rows[0]
        profile = profiles.get(path)
        if not profile or profile.get('sha256') != row['sha256'] or profile.get('evidence_version') != PROFILE_VERSION:
            raise ValueError(f'Missing current isolated pitch evidence: {path}')
        keys = {item['syllables'][0] + item['weak_training_label'] for item in rows}
        review = isolated_clarity(profile, row['weak_training_label'])
        if len(keys) != 1:
            review.update(status='needs_clearer_citation', reason='The same file has conflicting isolated labels')
        reviews[path] = {**review, 'key': sorted(keys)[0], 'sha256': row['sha256']}
    quality['isolated_clarity'] = reviews
    after = current_quiz(quality)
    if not after['entries'] or set(after['tone_coverage']) != {'1', '2', '3', '4', 'N'} or min(after['tone_coverage'].values()) == 0:
        raise ValueError('All-source review would remove a required practice tone category')
    all_assessments = {}
    for assessment in before['assessments'] + after['assessments']:
        key = json.dumps([assessment.get(field) for field in (
            'kind', 'audio_path', 'word_id', 'key', 'pinyin', 'surface_pattern',
        )], ensure_ascii=False)
        all_assessments[key] = assessment
    grouped = defaultdict(list)
    for assessment in all_assessments.values():
        grouped[assessment['audio_path']].append(assessment)
    before_paths = {item['audio_path'] for item in before['assessments']}
    after_paths = {item['audio_path'] for item in after['assessments']}
    files = []
    for path, assessments in sorted(grouped.items()):
        profile = profiles[path]
        if profile.get('evidence_version') != PROFILE_VERSION or profile['sha256'] != assessments[0]['sha256']:
            raise ValueError(f'Stale active-file profile: {path}')
        checks = []
        for assessment in assessments:
            isolated = assessment['kind'] == 'comparison' or len(assessment['pinyin_syllables']) == 1
            if isolated:
                review = reviews[path]
                checks.append({'key': review['key'], 'status': review['status'], 'reason': review['reason'],
                               'route': assessment['evidence']['method']})
            else:
                checks.append(check_word(assessment, profile, alignments.get(path)))
        if path in after_paths and any(check['status'] not in ('clear_citation_tone', 'existing_evidence_reproduced') for check in checks):
            raise ValueError(f'Active pronunciation evidence remains unresolved: {path}')
        files.append({
            'audio_path': path, 'sha256': profile['sha256'], 'used_before': path in before_paths,
            'used_after': path in after_paths, 'roles': sorted({entry['kind'] for entry in assessments}),
            'checks': checks,
        })
    report = {
        'version': 1, 'method': 'all-active-quiz-pronunciation-review-1',
        'pipeline_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'inventory_sha256': inventory['inventory_sha256'],
        'sinosplice_index_sha256': hashlib.sha256((ROOT / 'data/sinosplice_recordings.json').read_bytes()).hexdigest(),
        'scope': 'Every initial, comparison, and neutral-context file used before or after all-source citation clarity',
        'before': {'entries': len(before['entries']), 'initial_examples': len(before['initial_examples']), 'files': len(before_paths)},
        'after': {'entries': len(after['entries']), 'initial_examples': len(after['initial_examples']), 'files': len(after_paths)},
        'retained_library_entries': after['library_entries'],
        'isolated_candidate_files_reviewed': len(reviews),
        'active_file_source_counts': dict(Counter(path.split('/')[1] for path in after_paths)),
        'all_active_files_reviewed': True, 'missing_files': [],
        'independent_linguistic_accuracy_certified': False,
        'limitations': 'Automated file, phonetic, contour and evidence checks are not independent human tone judgments. Unclear citation contours do not establish source-label errors.',
        'files': files,
    }
    for path, value in [(quality_path, quality), (ROOT / 'data/quiz_pronunciation_review.json', report)]:
        temporary = path.with_suffix('.json.part')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        temporary.replace(path)
    print(json.dumps({key: value for key, value in report.items() if key != 'files'}, indent=2))


if __name__ == '__main__':
    main()
