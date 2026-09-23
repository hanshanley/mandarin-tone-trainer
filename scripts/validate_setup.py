#!/usr/bin/env python3
"""Validate all generated artifacts required by the browser and Android app."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from download_mandarin_native import valid_audio_payload


ROOT = Path(__file__).resolve().parents[1]
MP3_MAGIC = (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')


def read_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding='utf-8'))


def require(condition, message, errors):
    if not condition:
        errors.append(message)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def valid_mp3(path):
    try:
        with path.open('rb') as stream:
            return stream.read(3).startswith(MP3_MAGIC) and path.stat().st_size > 3
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--skip-mobile',
        action='store_true',
        help='do not require the generated www/ mobile bundle',
    )
    args = parser.parse_args()

    snapshots = read_json('config/source_snapshots.json')
    recordings = read_json('data/recordings.json')
    public = read_json('data/pinyin_public_recordings.json')
    mandarin_native = read_json('data/mandarin_native_recordings.json')
    errors = []
    require(
        mandarin_native.get('version') == 1 and isinstance(mandarin_native.get('recordings'), list),
        'invalid Mandarin Native recording index',
        errors,
    )
    imported_recordings = [
        *(mandarin_native.get('recordings') or []),
        *read_json('data/context_word_recordings.json')['recordings'],
    ]
    for recording in imported_recordings:
        path = ROOT / recording['audio_path']
        valid_import = False
        if path.is_file():
            with path.open('rb') as stream:
                valid_import = valid_audio_payload(stream.read(129), path.suffix)
        require(valid_import, f"missing or invalid imported audio: {recording['audio_path']}", errors)
        if path.is_file():
            require(
                file_hash(path) == recording.get('sha256'),
                f"imported audio hash mismatch: {recording['audio_path']}",
                errors,
            )

    word_recordings = [
        recording
        for recording in recordings
        if recording.get('source') == 'audio_cmn'
        and recording.get('recording_type') == 'isolated_word'
    ]
    require(
        len(word_recordings) == snapshots['audio_cmn']['word_recordings'],
        f"expected {snapshots['audio_cmn']['word_recordings']} indexed audio-cmn words, found {len(word_recordings)}",
        errors,
    )
    runtime_word_recordings = [
        recording
        for recording in recordings
        if recording.get('source') == 'audio_cmn'
    ]
    missing_words = [
        recording['audio_path']
        for recording in runtime_word_recordings
        if not (ROOT / recording['audio_path']).is_file()
    ]
    require(not missing_words, f'missing indexed word audio: {missing_words[:5]}', errors)
    invalid_words = [
        recording['audio_path']
        for recording in runtime_word_recordings
        if not valid_mp3(ROOT / recording['audio_path'])
    ]
    require(not invalid_words, f'invalid indexed word audio: {invalid_words[:5]}', errors)

    syllable_root = ROOT / 'audio' / 'audio_cmn' / 'syllabs'
    syllables = list(syllable_root.glob('cmn-*.mp3')) if syllable_root.is_dir() else []
    require(
        len(syllables) == snapshots['audio_cmn']['syllable_recordings'],
        f"expected {snapshots['audio_cmn']['syllable_recordings']} audio-cmn syllables, found {len(syllables)}",
        errors,
    )
    invalid_syllables = [str(path.relative_to(ROOT)) for path in syllables if not valid_mp3(path)]
    require(not invalid_syllables, f'invalid audio-cmn syllables: {invalid_syllables[:5]}', errors)

    require(
        len(public) == snapshots['pinyin_public']['recordings'],
        f"expected {snapshots['pinyin_public']['recordings']} public pinyin entries, found {len(public)}",
        errors,
    )
    missing_public = [
        recording['audio_path']
        for recording in public.values()
        if not (ROOT / recording['audio_path']).is_file()
    ]
    require(not missing_public, f'missing public pinyin audio: {missing_public[:5]}', errors)
    invalid_public = [
        recording['audio_path']
        for recording in public.values()
        if not valid_mp3(ROOT / recording['audio_path'])
    ]
    require(not invalid_public, f'invalid public pinyin audio: {invalid_public[:5]}', errors)

    for source in snapshots.values():
        for relative_path, expected_hash in source.get('samples', {}).items():
            path = ROOT / relative_path
            require(path.is_file(), f'missing snapshot sample: {relative_path}', errors)
            if path.is_file():
                require(
                    file_hash(path) == expected_hash,
                    f'snapshot hash mismatch: {relative_path}',
                    errors,
                )

    lockfile = (ROOT / 'package-lock.json').read_text(encoding='utf-8')
    require(
        'pkgs.visualstudio.com' not in lockfile and 'ms-feed-' not in lockfile,
        'package-lock.json still references a private package registry',
        errors,
    )

    if not args.skip_mobile:
        bundle = ROOT / 'www'
        for relative_path in [
            'index.html',
            'style.css',
            'app.js',
            'correction_audio.js',
            'audio_review.js',
            'data/hsk_words.json',
            'data/definitions.json',
            'data/recordings.json',
            'data/pinyin_public_recordings.json',
            'data/correction_audio_quality.json',
            'data/audio_reviews.json',
            'data/acoustic_reviews.json',
            'data/mandarin_native_recordings.json',
            'data/mandarin_native_words.json',
            'data/context_word_recordings.json',
        ]:
            require((bundle / relative_path).is_file(), f'missing mobile asset: www/{relative_path}', errors)
        if bundle.is_dir():
            for relative in ['app.js', 'audio_review.js', 'correction_audio.js']:
                target = bundle / relative
                if target.is_file():
                    require(
                        target.read_bytes() == (ROOT / 'app' / relative).read_bytes(),
                        f'www/{relative} is stale; run npm run build:mobile',
                        errors,
                    )
            for relative in ['audio_reviews.json', 'correction_audio_quality.json', 'mandarin_native_recordings.json',
                             'mandarin_native_words.json', 'context_word_recordings.json']:
                target = bundle / 'data' / relative
                if target.is_file():
                    require(
                        target.read_bytes() == (ROOT / 'data' / relative).read_bytes(),
                        f'www/data/{relative} is stale; run npm run build:mobile',
                        errors,
                    )
            acoustic_path = bundle / 'data/acoustic_reviews.json'
            if acoustic_path.is_file():
                expected = read_json('data/acoustic_reviews.json')
                expected['approvals'] = [
                    entry for entry in expected['approvals']
                    if entry['distribution_scope'] != 'local_only'
                ]
                require(
                    json.loads(acoustic_path.read_text(encoding='utf-8')) == expected,
                    'www/data/acoustic_reviews.json is stale or includes local-only audio',
                    errors,
                )

    node_script = """
import {loadReviewData,validateLedger,practiceInventory,requireToneCoverage} from './scripts/review_audio.mjs';
const data=loadReviewData();
const index=validateLedger(data);
const inventory=practiceInventory(data,index);
const packaged=practiceInventory(data,validateLedger(data,undefined,{allowLocalOnly:false}));
requireToneCoverage(inventory);
requireToneCoverage(packaged);
process.stdout.write(JSON.stringify({
  approvals:index.size,eligible:inventory.eligibleWords.length,audio:[...inventory.audio],
  packagedEligible:packaged.eligibleWords.length,packagedAudio:[...packaged.audio],
  toneCoverage:inventory.toneCoverage,packagedToneCoverage:packaged.toneCoverage,
}));
"""
    try:
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', node_script],
            cwd=ROOT,
            text=True,
        )
        selections = json.loads(output)
        print(f"Audio assessment coverage: {selections['eligible']} local / {selections['packagedEligible']} packaged practice entries; {selections['approvals']} checks (not an accuracy certificate)")
        if not args.skip_mobile:
            bundled_audio = {
                path.relative_to(bundle).as_posix()
                for path in (bundle / 'audio').rglob('*')
                if path.is_file()
            }
            require(
                bundled_audio == set(selections['packagedAudio']),
                'mobile audio does not match the distributable screened inventory',
                errors,
            )
            for relative in selections['packagedAudio']:
                target = bundle / relative
                if target.is_file():
                    require(
                        file_hash(target) == file_hash(ROOT / relative),
                        f'mobile audio changed since listening review: {relative}',
                        errors,
                    )
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        errors.append(f'could not validate correction selection with Node.js: {error}')

    if errors:
        print('Setup validation failed:')
        for error in errors:
            print(f'  - {error}')
        raise SystemExit(1)

    print(
        'Setup valid: '
        f'{len(word_recordings)} word recordings, '
        f'{len(syllables)} human syllables, '
        f'{len(public)} public syllables'
        f', {len(imported_recordings)} imported Mandarin Native candidates'
        + (', mobile bundle ready' if not args.skip_mobile else '')
    )


if __name__ == '__main__':
    main()
