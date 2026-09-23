#!/usr/bin/env python3
"""Download both public audio collections for local, unverified listening review."""
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
from pathlib import Path, PurePosixPath

from add_pinyin_syllables import TONE_MARKS, TONE_VALUES


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_URL = 'https://mandarin-native.com/audio_manifest.json'
AUDIO_URL = 'https://mandarin-native.com/audios_palavras/'
EXPLORE_URL = 'https://mandarin-native.com/data.json'
SITE_URL = 'https://mandarin-native.com/'
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


def unverified_metadata():
    return {
        'source': 'mandarin_native',
        'word': None,
        'candidate_hsk_ids': [],
        'language_code': 'zh',
        'language_status': 'unverified',
        'speaker': 'unknown',
        'surface_pattern': None,
        'license': None,
        'rights_status': 'unverified',
        'review_status': 'pending',
        'quiz_eligible': False,
    }


def pending_recording(key, words):
    normalized = normalized_source_key(key)
    candidates = [
        word['id'] for word in words
        if normalized_source_key(word.get('pinyin', '')) == normalized
    ]
    return {
        **unverified_metadata(),
        'source_audio_key': key,
        'candidate_hsk_ids': candidates,
        'audio_path': f'audio/mandarin_native/{key}.mp3',
        'filename': key + '.mp3',
        'source_url': AUDIO_URL + urllib.parse.quote(key, safe='') + '.mp3',
        'source_recording_id': 'mandarin-native/audios_palavras/' + key,
        'recording_type': 'word_candidate',
        'notes': 'Downloaded for local review. Filename matches are candidates, not verified readings. Listening accuracy and redistribution permission are unverified.',
    }


def context_paths(reference):
    if not isinstance(reference, str) or not reference.startswith('audios/'):
        raise ValueError(f'Unexpected Explore audio reference: {reference!r}')
    name = reference.removeprefix('audios/')
    validate_keys([name])
    suffix = PurePosixPath(name).suffix
    if suffix not in ('.mp3', '.m4a'):
        raise ValueError(f'Unsupported Explore audio format: {reference}')
    identifier = hashlib.sha256(reference.encode('utf-8')).hexdigest()
    return {
        'source_audio_key': name,
        'source_audio_reference': reference,
        'source_recording_id': 'mandarin-native/context/' + identifier,
        'source_url': SITE_URL + urllib.parse.quote(reference, safe='/'),
        'audio_path': f'audio/mandarin_native/context/{identifier}{suffix}',
        'filename': identifier + suffix,
    }


def explore_inventory(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('The Explore dataset must be a nonempty list')
    recordings = {}
    vocabulary = {}
    entry_ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('tokens'), list):
            raise ValueError('Invalid Explore entry or token list')
        entry_id = row.get('id')
        if not isinstance(entry_id, str) or not entry_id or entry_id in entry_ids:
            raise ValueError(f'Missing or duplicate Explore entry ID: {entry_id!r}')
        entry_ids.add(entry_id)
        paths = context_paths(row.get('audio'))
        identifier = paths['source_recording_id']
        if identifier not in recordings:
            recordings[identifier] = {
                **unverified_metadata(),
                **paths,
                'recording_type': 'context_sentence',
                'source_entry_ids': [],
                'context_words': [],
                'notes': 'Complete contextual sentence, not an isolated word or tone exemplar. No timing/alignment or spoken-tone labels have been verified. Local review only; reuse rights are unverified.',
            }
        recording = recordings[identifier]
        recording['source_entry_ids'].append(entry_id)
        for token in row['tokens']:
            if not isinstance(token, dict) or not isinstance(token.get('hanzi'), str):
                raise ValueError(f'Invalid Explore token in {entry_id}')
            pinyin = token.get('pinyin') or ''
            if not isinstance(pinyin, str):
                raise ValueError(f'Invalid Explore pinyin in {entry_id}')
            if not pinyin.strip():
                continue
            word = token['hanzi']
            if not word.strip():
                raise ValueError(f'Empty vocabulary token in {entry_id}')
            pinyin = pinyin.replace('ɡ', 'g')
            if word not in recording['context_words']:
                recording['context_words'].append(word)
            entry = vocabulary.setdefault(word, {
                'word': word, 'source_pinyin_labels': set(), 'context_recording_ids': set(),
            })
            entry['source_pinyin_labels'].add(pinyin)
            entry['context_recording_ids'].add(identifier)
    return list(recordings.values()), [
        {
            'word': entry['word'],
            'source_pinyin_labels': sorted(entry['source_pinyin_labels']),
            'context_recording_ids': sorted(entry['context_recording_ids']),
        }
        for _, entry in sorted(vocabulary.items())
    ]


def valid_audio_payload(payload, suffix):
    if len(payload) <= 128:
        return False
    if suffix == '.mp3':
        return payload.startswith(MP3_MAGIC)
    if suffix == '.m4a':
        return payload[4:8] == b'ftyp'
    if suffix == '.wav':
        return payload[:4] == b'RIFF' and payload[8:12] == b'WAVE'
    return False


def download_recording(recording, root=ROOT):
    key = validate_keys([recording['source_audio_key']])[0]
    contextual = recording.get('recording_type') == 'context_sentence'
    if contextual:
        paths = context_paths(recording.get('source_audio_reference'))
        if any(recording.get(field) != value for field, value in paths.items()):
            raise ValueError(f'Explore recording paths do not match their source: {key}')
        relative, expected_url = paths['audio_path'], paths['source_url']
    else:
        if recording.get('recording_type') != 'word_candidate':
            raise ValueError(f'Unsupported imported recording type: {recording.get("recording_type")}')
        relative = f'audio/mandarin_native/{key}.mp3'
        resolved_key = recording.get('resolved_audio_key', key)
        if resolved_key not in {key, encoded_pinyin_key(key)}:
            raise ValueError(f'Unexpected resolved audio key for {key}')
        expected_url = AUDIO_URL + urllib.parse.quote(resolved_key, safe='') + '.mp3'
    if recording['audio_path'] != relative or recording['source_url'] != expected_url:
        raise ValueError(f'Unexpected recording path or source URL for {key}')
    suffix = PurePosixPath(relative).suffix
    destination = root / relative
    if destination.is_symlink() or not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Audio path escapes the local corpus: {destination}')
    expected_hash = recording.get('sha256')
    if expected_hash and not re.fullmatch(r'[a-f0-9]{64}', expected_hash):
        raise ValueError(f'Invalid recorded hash for {key}')
    existing = destination.read_bytes() if destination.is_file() else None
    if existing and valid_audio_payload(existing, suffix) and (
        not expected_hash or hashlib.sha256(existing).hexdigest() == expected_hash
    ):
        payload = existing
        downloaded = False
    else:
        try:
            payload = fetch_bytes(expected_url)
        except urllib.error.HTTPError as error:
            normalized_key = encoded_pinyin_key(key)
            if contextual or error.code != 404 or resolved_key != key or normalized_key == key:
                raise RuntimeError(f'HTTP {error.code} downloading {expected_url}') from error
            expected_url = AUDIO_URL + urllib.parse.quote(normalized_key, safe='') + '.mp3'
            payload = fetch_bytes(expected_url)
            recording = {
                **recording,
                'resolved_audio_key': normalized_key,
                'source_url': expected_url,
                'notes': recording.get('notes', '') + ' The manifest URL returned 404; resolved through the site\'s Unicode/numbered-pinyin normalization.',
            }
        if not valid_audio_payload(payload, suffix):
            raise ValueError(f'Not a valid {suffix} audio payload: {expected_url}')
        actual_hash = hashlib.sha256(payload).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            raise ValueError(f'Upstream audio changed for {key}; refusing to replace the pinned recording')
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(suffix + '.part')
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
    parser.add_argument('--standalone-only', action='store_true', help='restore only direct word recordings used by normal practice')
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error('--workers must be between 1 and 4')
    index = json.loads(INDEX.read_text(encoding='utf-8'))
    existing = index.get('recordings', [])
    if not existing or args.refresh_index:
        client = fetch_bytes(SITE_URL + 'cards.js').decode('utf-8')
        match = re.search(r"""const BUILD_V\s*=\s*['"]([A-Za-z0-9._-]+)['"]""", client)
        if not match:
            raise ValueError('Cannot determine the live source client cache version')
        cache_version = match.group(1)
        raw = fetch_bytes(MANIFEST_URL + '?v=' + cache_version)
        keys = validate_keys(json.loads(raw))
        explore_raw = fetch_bytes(EXPLORE_URL + '?v=' + cache_version)
        explore_rows = json.loads(explore_raw)
        context_recordings, vocabulary = explore_inventory(explore_rows)
        words = json.loads((ROOT / 'data/hsk_words.json').read_text(encoding='utf-8'))
        by_id = {recording['source_recording_id']: recording for recording in existing}
        proposed = [pending_recording(key, words) for key in keys] + context_recordings
        records = []
        for recording in proposed:
            saved = by_id.get(recording['source_recording_id'])
            if saved and recording['recording_type'] == 'context_sentence':
                saved = {
                    **saved,
                    'context_words': recording['context_words'],
                    'source_entry_ids': recording['source_entry_ids'],
                }
            records.append(saved or recording)
        # Keep pinned recordings even if the live site later stops listing them.
        current_ids = {recording['source_recording_id'] for recording in proposed}
        records.extend(recording for identifier, recording in by_id.items() if identifier not in current_ids)
        index.update({
            'version': 1,
            'source': 'mandarin_native',
            'manifest_url': MANIFEST_URL,
            'upstream_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'upstream_manifest_keys': keys,
            'source_client_cache_version': cache_version,
            'explore_dataset_url': EXPLORE_URL,
            'explore_dataset_sha256': hashlib.sha256(explore_raw).hexdigest(),
            'upstream_context_references': sorted({recording['source_audio_reference'] for recording in context_recordings}),
            'explore_entry_count': len(explore_rows),
            'explore_vocabulary': vocabulary,
            'indexed_at': datetime.now(timezone.utc).isoformat(),
        })
    else:
        records = existing
    identifiers = [recording['source_recording_id'] for recording in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Duplicate recording IDs in local import index')
    selected = [recording for recording in records if not args.standalone_only or recording['recording_type'] == 'word_candidate']
    retained = [recording for recording in records if args.standalone_only and recording['recording_type'] != 'word_candidate']
    completed = []
    downloaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_recording, recording) for recording in selected]
        for future in concurrent.futures.as_completed(futures):
            recording, was_downloaded = future.result()
            completed.append(recording)
            downloaded += was_downloaded
            if len(completed) % 100 == 0:
                print(f'Mandarin Native: {len(completed)}/{len(selected)}', flush=True)
    index['recordings'] = sorted(completed + retained, key=lambda recording: recording['source_recording_id'])
    temporary = INDEX.with_suffix('.json.part')
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(INDEX)
    matched = sum(bool(recording['candidate_hsk_ids']) for recording in completed)
    word_count = sum(recording['recording_type'] == 'word_candidate' for recording in completed)
    context_count = sum(recording['recording_type'] == 'context_sentence' for recording in completed)
    print(f'Mandarin Native: indexed={len(completed)}, downloaded={downloaded}, existing={len(completed)-downloaded}, with_vocabulary_candidates={matched}.')
    print(f'Collections: {word_count} standalone word clips; {context_count} contextual sentence clips; {len(index.get("explore_vocabulary", []))} Explore vocabulary entries.')
    print('Imported clips remain unverified; no listening approvals or redistribution rights were granted.')


if __name__ == '__main__':
    main()
