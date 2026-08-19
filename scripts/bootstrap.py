#!/usr/bin/env python3
"""Recreate the ignored runtime assets from pinned upstream snapshots."""
import argparse
import hashlib
import importlib
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


def java_major():
    try:
        output = subprocess.check_output(
            ['java', '-version'],
            stderr=subprocess.STDOUT,
            text=True,
        )
        version = output.split('"', 2)[1]
        return int(version.split('.', 1)[0])
    except (OSError, subprocess.CalledProcessError, IndexError, ValueError):
        return 0


def platform_key():
    system = {'darwin': 'darwin', 'linux': 'linux'}.get(sys.platform)
    machine = platform.machine().lower()
    architecture = (
        'arm64' if machine in {'arm64', 'aarch64'}
        else 'x64' if machine in {'x86_64', 'amd64'}
        else None
    )
    return f'{system}-{architecture}' if system and architecture else None


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def configure_user_bin(home=None, shell=None):
    home = Path(home) if home else Path.home()
    user_bin = home / '.local' / 'bin'
    user_bin.mkdir(parents=True, exist_ok=True)
    shell_name = Path(shell or os.environ.get('SHELL', '')).name
    profile_name = '.zprofile' if shell_name == 'zsh' else '.profile'
    profile = home / profile_name
    path_line = 'export PATH="$HOME/.local/bin:$PATH"'
    existing = profile.read_text(encoding='utf-8') if profile.exists() else ''
    if path_line not in existing.splitlines():
        separator = '' if not existing or existing.endswith('\n') else '\n'
        with profile.open('a', encoding='utf-8') as output:
            output.write(f'{separator}\n# Added by Mandarin Tone Trainer setup\n{path_line}\n')

    current_path = os.environ.get('PATH', '').split(os.pathsep)
    if str(user_bin) not in current_path:
        os.environ['PATH'] = f'{user_bin}{os.pathsep}{os.environ.get("PATH", "")}'
    return user_bin, profile, profile_name


def install_user_shim(name, source, user_bin):
    destination = user_bin / name
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        print(
            f'Keeping existing {destination}; the project-local {name} '
            'remains available during setup.',
            flush=True,
        )
        return False
    destination.symlink_to(source)
    return True


def install_user_node_shims(directory, home=None, shell=None):
    user_bin, profile, profile_name = configure_user_bin(home, shell)
    installed = [
        name
        for name in ('node', 'npm', 'npx')
        if install_user_shim(name, directory / 'bin' / name, user_bin)
    ]
    if installed:
        print(
            f'Installed user commands in {user_bin}: {", ".join(installed)}.',
            flush=True,
        )
    print(
        f'New terminals will load npm from {profile}. '
        f'For this terminal, run: source ~/{profile_name}',
        flush=True,
    )


def ensure_ffmpeg():
    if shutil.which('ffmpeg'):
        return
    package_root = ROOT / '.tools' / 'python'
    try:
        import imageio_ffmpeg
    except ImportError:
        run([
            sys.executable,
            '-m',
            'pip',
            'install',
            '--target',
            package_root,
            '--requirement',
            ROOT / 'requirements.txt',
        ])
        sys.path.insert(0, str(package_root))
        importlib.invalidate_caches()
        import imageio_ffmpeg
    source = Path(imageio_ffmpeg.get_ffmpeg_exe())
    if not source.is_file():
        raise SystemExit('The local FFmpeg package did not provide an executable.')
    user_bin, profile, profile_name = configure_user_bin()
    install_user_shim('ffmpeg', source, user_bin)
    if not shutil.which('ffmpeg'):
        os.environ['PATH'] = f'{source.parent}{os.pathsep}{os.environ.get("PATH", "")}'
    if not shutil.which('ffmpeg'):
        raise SystemExit('The local FFmpeg installation did not initialize correctly.')
    print(
        f'Using local FFmpeg {imageio_ffmpeg.get_ffmpeg_version()}. '
        f'New terminals load it through {profile}; for this terminal, '
        f'run: source ~/{profile_name}',
        flush=True,
    )


def ensure_node():
    if node_major()>=22 and shutil.which('npm'):
        return
    config=json.loads((ROOT/'config'/'toolchain.json').read_text(encoding='utf-8'))['node']
    key=platform_key()
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
    install_user_node_shims(directory)
    print(f"Using local Node.js {subprocess.check_output(['node','--version'],text=True).strip()}.")


def ensure_jdk():
    if java_major() >= 21 and shutil.which('keytool'):
        return
    config = json.loads(
        (ROOT / 'config' / 'toolchain.json').read_text(encoding='utf-8')
    )['jdk']
    key = platform_key()
    if key not in config['sha256']:
        raise SystemExit(
            'JDK 21 is missing and automatic installation is unavailable '
            f'for {sys.platform}/{platform.machine()}. Install JDK 21 and rerun.'
        )
    legacy_home = ROOT / '.tools' / 'jdk-21'
    if sys.platform == 'darwin':
        legacy_home = legacy_home / 'Contents' / 'Home'
    directory = ROOT / '.tools' / f"jdk-{config['version'].replace('+', '_')}-{key}"
    java_home = legacy_home if (legacy_home / 'bin' / 'java').is_file() else None
    if java_home is None:
        matches = list(directory.glob('**/bin/java')) if directory.exists() else []
        java_home = matches[0].parent.parent if matches else None
    if java_home is None:
        tools = ROOT / '.tools'
        tools.mkdir(exist_ok=True)
        filename = config['filenames'][key]
        archive = tools / filename
        if not archive.is_file() or sha256(archive) != config['sha256'][key]:
            print(f"Downloading local JDK {config['version']} for {key}...", flush=True)
            request = urllib.request.Request(
                f"{config['base_url']}/{filename}",
                headers={'User-Agent': 'MandarinToneTrainer/1.0'},
            )
            temporary = archive.with_name(f'{archive.name}.part')
            with urllib.request.urlopen(request, timeout=300) as response:
                temporary.write_bytes(response.read())
            if sha256(temporary) != config['sha256'][key]:
                temporary.unlink(missing_ok=True)
                raise SystemExit('Downloaded JDK archive failed SHA-256 verification.')
            temporary.replace(archive)
        directory.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, 'r:gz') as source:
            source.extractall(directory)
        matches = list(directory.glob('**/bin/java'))
        if not matches:
            raise SystemExit('The downloaded JDK did not contain a Java executable.')
        java_home = matches[0].parent.parent

    user_bin, profile, profile_name = configure_user_bin()
    installed = [
        name
        for name in ('java', 'javac', 'jar', 'keytool')
        if install_user_shim(name, java_home / 'bin' / name, user_bin)
    ]
    os.environ['PATH'] = (
        f'{java_home / "bin"}{os.pathsep}{os.environ.get("PATH", "")}'
    )
    os.environ['JAVA_HOME'] = str(java_home)
    if java_major() < 21 or not shutil.which('keytool'):
        raise SystemExit('The local JDK installation did not initialize correctly.')
    if installed:
        print(
            f'Installed JDK commands in {user_bin}: {", ".join(installed)}.',
            flush=True,
        )
    print(
        f'Using local JDK {config["version"]}. New terminals load it through '
        f'{profile}; for this terminal, run: source ~/{profile_name}',
        flush=True,
    )


def configure_android_sdk(
    sdk_root=None,
    home=None,
    shell=None,
    project_root=ROOT,
):
    candidates = []
    if sdk_root:
        candidates.append(Path(sdk_root))
    for variable in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]))
    home_path = Path(home) if home else Path.home()
    candidates.extend([
        project_root / '.tools' / 'android-sdk',
        home_path / 'Library' / 'Android' / 'sdk',
        home_path / 'Android' / 'Sdk',
    ])
    sdk = next(
        (
            candidate.resolve()
            for candidate in candidates
            if (candidate / 'platform-tools' / 'adb').is_file()
        ),
        None,
    )
    if sdk is None:
        return None

    user_bin, profile, profile_name = configure_user_bin(home_path, shell)
    install_user_shim('adb', sdk / 'platform-tools' / 'adb', user_bin)
    local_properties = project_root / 'android' / 'local.properties'
    existing = (
        local_properties.read_text(encoding='utf-8')
        if local_properties.exists()
        else ''
    )
    lines = [
        line
        for line in existing.splitlines()
        if not line.startswith('sdk.dir=')
    ]
    lines.append(f'sdk.dir={sdk}')
    local_properties.parent.mkdir(parents=True, exist_ok=True)
    local_properties.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.environ['ANDROID_HOME'] = str(sdk)
    os.environ['ANDROID_SDK_ROOT'] = str(sdk)
    print(
        f'Configured Android SDK at {sdk}. New terminals load adb through '
        f'{profile}; for this terminal, run: source ~/{profile_name}',
        flush=True,
    )
    return sdk


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
    ensure_jdk()
    ensure_ffmpeg()
    configure_android_sdk()

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
