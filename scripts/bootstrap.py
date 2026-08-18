#!/usr/bin/env python3
"""Recreate the ignored runtime assets from pinned upstream snapshots."""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command):
    print(f"\n$ {' '.join(map(str, command))}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)

def run_retry(command, attempts=4):
    for attempt in range(1,attempts+1):
        try:
            run(command)
            return
        except subprocess.CalledProcessError:
            if attempt==attempts:
                raise
            delay=15*attempt
            print(f'Download batch failed; retrying resumably in {delay} seconds.',flush=True)
            time.sleep(delay)


def require_command(name, guidance):
    if not shutil.which(name):
        raise SystemExit(f'Missing required command: {name}. {guidance}')


def node_major():
    output = subprocess.check_output(['node', '--version'], text=True).strip()
    return int(output.lstrip('v').split('.', 1)[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--verify-only',
        action='store_true',
        help='validate an existing setup without downloading or installing',
    )
    parser.add_argument(
        '--skip-npm-ci',
        action='store_true',
        help='reuse the current node_modules directory',
    )
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()

    require_command('node', 'Install Node.js 22 or newer.')
    require_command('npm', 'Install npm with Node.js 22 or newer.')
    require_command('ffmpeg', 'Install FFmpeg and ensure it is on PATH.')
    if node_major() < 22:
        raise SystemExit('Node.js 22 or newer is required.')

    if args.verify_only:
        run([sys.executable, 'scripts/validate_setup.py'])
        run(['npm', 'test'])
        return

    snapshots = json.loads(
        (ROOT / 'config' / 'source_snapshots.json').read_text(encoding='utf-8')
    )
    audio_cmn = snapshots['audio_cmn']
    pinyin_public = snapshots['pinyin_public']

    if not args.skip_npm_ci:
        run(['npm', 'ci'])

    run_retry([
        sys.executable,
        'scripts/download_audio_cmn.py',
        '--all-source',
        '--revision',
        audio_cmn['revision'],
        '--quality',
        audio_cmn['word_quality'],
        '--workers',
        str(args.workers),
    ])
    run_retry([
        sys.executable,
        'scripts/download_audio_cmn_syllables.py',
        '--revision',
        audio_cmn['revision'],
        '--quality',
        audio_cmn['syllable_quality'],
        '--workers',
        str(args.workers),
    ])
    run([
        sys.executable,
        'scripts/download_public_pinyin_syllables.py',
        '--revision',
        pinyin_public['revision'],
    ])
    run([sys.executable, 'scripts/import_local_audio.py'])
    run(['npm', 'run', 'build:mobile'])
    run([sys.executable, 'scripts/validate_setup.py'])
    run(['npm', 'test'])

    print('\nReady.')
    print('Browser: python3 scripts/serve.py, then open http://localhost:8000/app/')
    print('Android debug APK: npm run android:debug')


if __name__ == '__main__':
    main()
