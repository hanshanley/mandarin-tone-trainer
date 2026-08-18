#!/usr/bin/env python3
"""Validate all generated artifacts required by the browser and Android app."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    errors = []

    word_recordings = [
        recording
        for recording in recordings
        if recording.get('source') == 'audio_cmn'
    ]
    require(
        len(word_recordings) == snapshots['audio_cmn']['word_recordings'],
        f"expected {snapshots['audio_cmn']['word_recordings']} indexed audio-cmn words, found {len(word_recordings)}",
        errors,
    )
    missing_words = [
        recording['audio_path']
        for recording in word_recordings
        if not (ROOT / recording['audio_path']).is_file()
    ]
    require(not missing_words, f'missing indexed word audio: {missing_words[:5]}', errors)

    syllable_root = ROOT / 'audio' / 'audio_cmn' / 'syllabs'
    syllables = list(syllable_root.glob('cmn-*.mp3')) if syllable_root.is_dir() else []
    require(
        len(syllables) == snapshots['audio_cmn']['syllable_recordings'],
        f"expected {snapshots['audio_cmn']['syllable_recordings']} audio-cmn syllables, found {len(syllables)}",
        errors,
    )

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
            'data/hsk_words.json',
            'data/definitions.json',
            'data/recordings.json',
            'data/pinyin_public_recordings.json',
            'data/correction_audio_quality.json',
        ]:
            require((bundle / relative_path).is_file(), f'missing mobile asset: www/{relative_path}', errors)
        if bundle.is_dir():
            require(
                (bundle / 'app.js').read_bytes() == (ROOT / 'app' / 'app.js').read_bytes(),
                'www/app.js is stale; run npm run build:mobile',
                errors,
            )

    node_script = """
const fs=require('node:fs');
const policy=require('./app/correction_audio.js');
const words=require('./data/hsk_words.json');
const quality=require('./data/correction_audio_quality.json');
const publicRecordings=require('./data/pinyin_public_recordings.json');
const keys=new Set();
for(const word of words){
  for(const pinyin of (word.pinyin_syllables||[])){
    for(const tone of ['1','2','3','4'])keys.add(policy.correctionKey(pinyin,tone));
  }
}
const result={};
for(const mode of ['pinyin_public','audio_cmn']){
  const missing=[];
  const unavailable=[];
  for(const key of keys){
    const selected=policy.correctionSelection(key,quality,publicRecordings,mode);
    if(!selected){unavailable.push(key);continue;}
    if(!fs.existsSync(selected.audio_path))missing.push(selected.audio_path);
  }
  result[mode]={missing,unavailable};
}
process.stdout.write(JSON.stringify(result));
"""
    try:
        output = subprocess.check_output(
            ['node', '-e', node_script],
            cwd=ROOT,
            text=True,
        )
        selections = json.loads(output)
        for mode, result in selections.items():
            require(not result['missing'], f'{mode} has missing selected audio: {result["missing"][:5]}', errors)
            require(
                result['unavailable'] == ['r1', 'r2', 'r3', 'r4'],
                f'{mode} unexpected unavailable keys: {result["unavailable"]}',
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
        + (', mobile bundle ready' if not args.skip_mobile else '')
    )


if __name__ == '__main__':
    main()
