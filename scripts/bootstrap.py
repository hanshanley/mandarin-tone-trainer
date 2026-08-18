#!/usr/bin/env python3
"""Recreate the ignored runtime assets from pinned upstream snapshots."""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
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
    try:
        output = subprocess.check_output(['node', '--version'], text=True).strip()
        return int(output.lstrip('v').split('.', 1)[0])
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_node():
    if node_major()>=22 and shutil.which('npm'):
        return
    config=json.loads((ROOT/'config'/'toolchain.json').read_text(encoding='utf-8'))['node']
    system={'darwin':'darwin','linux':'linux'}.get(sys.platform)
    machine=platform.machine().lower()
    architecture='arm64' if machine in {'arm64','aarch64'} else 'x64' if machine in {'x86_64','amd64'} else None
    key=f'{system}-{architecture}' if system and architecture else None
    if key not in config['sha256']:
        raise SystemExit(
            'Node.js 22+ is missing and automatic installation is unavailable '
            f'for {sys.platform}/{platform.machine()}. Install Node.js 22 and rerun.'
        )
    version=config['version']
    directory=ROOT/'.tools'/f'node-v{version}-{key}'
    node=directory/'bin'/'node'
    npm=directory/'bin'/'npm'
    if not node.is_file() or not npm.is_file():
        tools=ROOT/'.tools'; tools.mkdir(exist_ok=True)
        archive=tools/f'node-v{version}-{key}.tar.xz'
        filename=archive.name
        if not archive.is_file() or sha256(archive)!=config['sha256'][key]:
            print(f'Downloading local Node.js {version} for {key}...',flush=True)
            request=urllib.request.Request(
                f"{config['base_url']}/{filename}",
                headers={'User-Agent':'MandarinToneTrainer/1.0'},
            )
            temporary=archive.with_name(f'{archive.name}.part')
            with urllib.request.urlopen(request,timeout=180) as response:
                temporary.write_bytes(response.read())
            if sha256(temporary)!=config['sha256'][key]:
                temporary.unlink(missing_ok=True)
                raise SystemExit('Downloaded Node.js archive failed SHA-256 verification.')
            temporary.replace(archive)
        with tarfile.open(archive,'r:xz') as source:
            source.extractall(ROOT/'.tools')
    os.environ['PATH']=f"{directory/'bin'}{os.pathsep}{os.environ.get('PATH','')}"
    if node_major()<22 or not shutil.which('npm'):
        raise SystemExit('The local Node.js installation did not initialize correctly.')
    print(f"Using local Node.js {subprocess.check_output(['node','--version'],text=True).strip()}.")


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

    ensure_node()
    require_command('ffmpeg', 'Install FFmpeg and ensure it is on PATH.')

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
