#!/usr/bin/env python3
"""Restore the pinned, expert-annotated OMPAL calibration corpus."""
import concurrent.futures
import hashlib
import json
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from download_mandarin_native import fetch_bytes


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config/pronunciation_judge.json'
MANIFEST = ROOT / 'data/pronunciation_judge_reference.json'
OUTPUT = ROOT / 'imports/pronunciation_judge/ompal'


def blob_hash(payload):
    return hashlib.sha1(b'blob ' + str(len(payload)).encode('ascii') + b'\0' + payload).hexdigest()


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root, relative):
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith('/') or '\\' in relative or any(part in ('', '.', '..') for part in parts):
        raise ValueError(f'Invalid reference path: {relative}')
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('Reference path escapes its directory')
    return result


def main():
    config = json.loads(CONFIG.read_text())['reference']
    if MANIFEST.is_file():
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        if manifest['revision'] != config['revision'] or manifest['repository'] != config['repository']:
            raise ValueError('Reference revision differs from its pinned manifest')
    else:
        tree_url = f'https://api.github.com/repos/{config["repository"]}/git/trees/{config["revision"]}?recursive=1'
        tree = json.loads(fetch_bytes(tree_url))
        if tree.get('truncated'):
            raise ValueError('Truncated reference inventory')
        metadata = {'README.md', 'LICENSE', 'native_scores.json', 'non-native_scores.json', 'non-native_scores-detail.json'}
        files = [
            {'path': item['path'], 'git_blob_sha1': item['sha'], 'bytes': item['size']}
            for item in tree['tree'] if item['type'] == 'blob'
            and (item['path'] in metadata or (item['path'].startswith('wav/') and item['path'].endswith('.wav')))
        ]
        if sum(item['path'].endswith('.wav') for item in files) != config['expected_audio_files']:
            raise ValueError('Unexpected reference audio count')
        manifest = {**config, 'files': files}
    OUTPUT.mkdir(parents=True, exist_ok=True)

    def restore(item):
        path = safe_path(OUTPUT, item['path'])
        payload = path.read_bytes() if path.is_file() else None
        if payload is None or blob_hash(payload) != item['git_blob_sha1']:
            url = f'https://raw.githubusercontent.com/{config["repository"]}/{config["revision"]}/' + quote(item['path'], safe='/')
            payload = fetch_bytes(url)
            if blob_hash(payload) != item['git_blob_sha1']:
                raise ValueError(f'Upstream reference hash mismatch: {item["path"]}')
            if len(payload) != item['bytes']:
                raise ValueError(f'Upstream reference size mismatch: {item["path"]}')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + '.part')
            temporary.write_bytes(payload)
            temporary.replace(path)
        digest = hashlib.sha256(payload).hexdigest()
        if item.get('sha256') and item['sha256'] != digest:
            raise ValueError(f'Pinned reference SHA-256 changed: {item["path"]}')
        return {**item, 'sha256': digest}

    files = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for number, item in enumerate(pool.map(restore, manifest['files']), 1):
            files.append(item)
            if number % 200 == 0:
                print(f'OMPAL reference: {number}/{len(manifest["files"])}', flush=True)
    manifest['files'] = files
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    temporary = MANIFEST.with_suffix('.json.part')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(MANIFEST)
    print(f'Restored {len(files)} pinned reference files. Research/training only; not added to practice audio.')


if __name__ == '__main__':
    main()
