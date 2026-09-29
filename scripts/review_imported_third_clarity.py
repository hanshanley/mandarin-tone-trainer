#!/usr/bin/env python3
"""Review isolated third-tone teaching clarity without relabeling source audio."""
import argparse
import hashlib
import json

from acoustic_analysis import curve_features, segment
from collect_acoustic_evidence import PROFILE_VERSION, read_jsonl
from native_tone_labels import safe_audio, verify_inventory
from runtime_data import ROOT


def clarity(profile):
    measured = segment(profile)
    features = {method: curve_features(curve) for method, curve in measured.get('curves', {}).items()}
    clear = measured['status'] == 'measured' and len(features) >= 2 and all(
        feature['dip'] >= 2.5 and feature['rebound'] >= 2.5 and .25 <= feature['trough'] <= .8
        for feature in features.values()
    )
    return {
        'method': 'isolated-third-clarity-1',
        'status': 'clear_citation_third' if clear else 'needs_clearer_citation',
        'reason': 'Continuous fall-rise agrees across usable trackers.' if clear else
            measured.get('reason', 'A clear isolated fall-rise is not established; low third tones are not automatically wrong.'),
        'measurement': measured, 'features': features,
        'source_label_changed': False, 'linguistic_error_established': False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', required=True)
    args = parser.parse_args()
    inventory = verify_inventory(json.loads((ROOT / args.inventory).read_text()))
    profiles = read_jsonl(ROOT / '.audit/acoustic-profiles.jsonl')
    path = ROOT / 'data/correction_audio_quality.json'
    quality = json.loads(path.read_text())
    results = {}
    for row in inventory['records']:
        if row['source'] != 'mandarin_native' or row['syllables'] is None or len(row['syllables']) != 1 or row['weak_training_label'] != '3':
            continue
        relative = row['audio_path']
        if relative in results:
            continue
        profile = profiles.get(relative)
        if not profile or profile.get('evidence_version') != PROFILE_VERSION or profile['sha256'] != row['sha256']:
            raise ValueError(f'Missing current clarity evidence: {relative}')
        if hashlib.sha256(safe_audio(relative).read_bytes()).hexdigest() != row['sha256']:
            raise ValueError(f'Changed source recording: {relative}')
        results[relative] = {**clarity(profile), 'key': row['syllables'][0] + '3', 'sha256': row['sha256']}
    quality['native_clarity'] = results
    temporary = path.with_suffix('.json.part')
    temporary.write_text(json.dumps(quality, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)
    print(json.dumps({'reviewed_files': len(results), 'clear_citation_thirds': sum(
        item['status'] == 'clear_citation_third' for item in results.values()
    ), 'labels_changed': 0, 'linguistic_errors_claimed': 0}))


if __name__ == '__main__':
    main()
