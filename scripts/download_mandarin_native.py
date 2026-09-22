#!/usr/bin/env python3
"""Download publicly exposed word clips for local, unverified listening review."""
import argparse
import concurrent.futures
import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from add_pinyin_syllables import TONE_MARKS, TONE_VALUES


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_URL = 'https://mandarin-native.com/audio_manifest.json'
AUDIO_URL = 'https://mandarin-native.com/audios_palavras/'
INDEX = ROOT / 'data/mandarin_native_recordings.json'
MP3_MAGIC = (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')
MAX_BYTES = 20 * 1024 * 1024


def encoded_pinyin_key(value):
    value = unicodedata.normalize('NFC', value).replace('ɡ', 'g').lower()
    parts = []
    for char in value:
        if char in TONE_VALUES:
            parts.append(char.translate(TONE_MARKS) + str(TONE_VALUES[char]))
        else:
            parts.append('v' if char == 'ü' else '_' if char == ' ' else char)
    return ''.join(parts)


def normalized_source_key(value):
    return re.sub(r"[_\-'’]", '', encoded_pinyin_key(value))


def validate_keys(payload):
    if not isinstance(payload, list) or not payload:
        raise ValueError('The upstream audio manifest must be a nonempty list')
    keys = []
    for key in payload:
        if (
            not isinstance(key, str) or not key or key.startswith('.')
            or any(char in key for char in '/\\?#')
            or any(ord(char) < 32 for char in key)
        ):
            raise ValueError(f'Unsafe upstream audio key: {key!r}')
        keys.append(key)
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate keys in upstream audio manifest')
    return keys


def fetch_bytes(url, attempts=4):
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={
                'User-Agent': 'MandarinToneTrainer/1.0 (local listening review)',
            })
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError(f'Download exceeds {MAX_BYTES} bytes: {url}')
            return payload
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            retry_after = error.headers.get('Retry-After', '')
            delay = min(int(retry_after), 60) if retry_after.isdigit() else 2 ** attempt
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            delay = 2 ** attempt
        time.sleep(delay)


def pending_recording(key, words):
    normalized = normalized_source_key(key)
    candidates = [
        word['id'] for word in words
        if normalized_source_key(word.get('pinyin', '')) == normalized
    ]
    return {
        'source': 'mandarin_native',
        'source_audio_key': key,
        'word': None,
        'candidate_hsk_ids': candidates,
        'audio_path': f'audio/mandarin_native/{key}.mp3',
        'filename': key + '.mp3',
        'source_url': AUDIO_URL + urllib.parse.quote(key, safe='') + '.mp3',
        'source_recording_id': 'mandarin-native/audios_palavras/' + key,
        'language_code': 'zh',
        'language_status': 'unverified',
        'recording_type': 'word_candidate',
        'speaker': 'unknown',
        'surface_pattern': None,
        'license': None,
        'rights_status': 'unverified',
        'review_status': 'pending',
        'quiz_eligible': False,
        'notes': 'Downloaded for local review. Filename matches are candidates, not verified readings. Listening accuracy and redistribution permission are unverified.',
    }


def download_recording(recording, root=ROOT):
    key = validate_keys([recording['source_audio_key']])[0]
    relative = f'audio/mandarin_native/{key}.mp3'
    resolved_key = recording.get('resolved_audio_key', key)
    if resolved_key not in {key, encoded_pinyin_key(key)}:
        raise ValueError(f'Unexpected resolved audio key for {key}')
    expected_url = AUDIO_URL + urllib.parse.quote(resolved_key, safe='') + '.mp3'
    if recording['audio_path'] != relative or recording['source_url'] != expected_url:
        raise ValueError(f'Unexpected recording path or source URL for {key}')
    destination = root / relative
    if destination.is_symlink() or not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Audio path escapes the local corpus: {destination}')
    expected_hash = recording.get('sha256')
    if expected_hash and not re.fullmatch(r'[a-f0-9]{64}', expected_hash):
        raise ValueError(f'Invalid recorded hash for {key}')
    existing = destination.read_bytes() if destination.is_file() else None
    if existing and existing.startswith(MP3_MAGIC) and len(existing) > 128 and (
        not expected_hash or hashlib.sha256(existing).hexdigest() == expected_hash
    ):
        payload = existing
        downloaded = False
    else:
        try:
            payload = fetch_bytes(expected_url)
        except urllib.error.HTTPError as error:
            normalized_key = encoded_pinyin_key(key)
            if error.code != 404 or resolved_key != key or normalized_key == key:
                raise RuntimeError(f'HTTP {error.code} downloading {expected_url}') from error
            expected_url = AUDIO_URL + urllib.parse.quote(normalized_key, safe='') + '.mp3'
            payload = fetch_bytes(expected_url)
            recording = {
                **recording,
                'resolved_audio_key': normalized_key,
                'source_url': expected_url,
                'notes': recording.get('notes', '') + ' The manifest URL returned 404; resolved through the site\'s Unicode/numbered-pinyin normalization.',
            }
        if not payload.startswith(MP3_MAGIC) or len(payload) <= 128:
            raise ValueError(f'Not a valid MP3 payload: {expected_url}')
        actual_hash = hashlib.sha256(payload).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            raise ValueError(f'Upstream audio changed for {key}; refusing to replace the pinned recording')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix('.mp3.part')
        temporary.write_bytes(payload)
        temporary.replace(destination)
        downloaded = True
    return {
        **recording,
        'sha256': hashlib.sha256(payload).hexdigest(),
        'byte_length': len(payload),
    }, downloaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--refresh-index', action='store_true', help='discover newly published keys without changing existing labels or approvals')
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error('--workers must be between 1 and 4')
    index = json.loads(INDEX.read_text(encoding='utf-8'))
    existing = index.get('recordings', [])
    if not existing or args.refresh_index:
        raw = fetch_bytes(MANIFEST_URL)
        keys = validate_keys(json.loads(raw))
        words = json.loads((ROOT / 'data/hsk_words.json').read_text(encoding='utf-8'))
        by_key = {recording['source_audio_key']: recording for recording in existing}
        records = [by_key.get(key) or pending_recording(key, words) for key in keys]
        # Keep pinned recordings even if the live site later stops listing them.
        key_set = set(keys)
        records.extend(recording for key, recording in by_key.items() if key not in key_set)
        index.update({
            'version': 1,
            'source': 'mandarin_native',
            'manifest_url': MANIFEST_URL,
            'upstream_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'upstream_manifest_keys': keys,
            'indexed_at': datetime.now(timezone.utc).isoformat(),
        })
    else:
        records = existing
        validate_keys([recording['source_audio_key'] for recording in records])
    completed = []
    downloaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_recording, recording) for recording in records]
        for future in concurrent.futures.as_completed(futures):
            recording, was_downloaded = future.result()
            completed.append(recording)
            downloaded += was_downloaded
            if len(completed) % 100 == 0:
                print(f'Mandarin Native: {len(completed)}/{len(records)}', flush=True)
    index['recordings'] = sorted(completed, key=lambda recording: recording['source_audio_key'])
    temporary = INDEX.with_suffix('.json.part')
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(INDEX)
    matched = sum(bool(recording['candidate_hsk_ids']) for recording in completed)
    print(f'Mandarin Native: indexed={len(completed)}, downloaded={downloaded}, existing={len(completed)-downloaded}, with_vocabulary_candidates={matched}.')
    print('Imported clips remain unverified; no listening approvals or redistribution rights were granted.')


if __name__ == '__main__':
    main()
