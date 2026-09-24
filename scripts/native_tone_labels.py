"""Immutable original-label hypotheses for intact native tone recordings."""
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from add_definitions import load_cedict, parse_cedict
from build_hsk_data import pattern, sandhi_surface
from runtime_data import ROOT, read_words


INPUTS = [
    'data/hsk_words.json', 'data/mandarin_native_words.json', 'data/recordings.json',
    'data/mandarin_native_recordings.json', 'data/pinyin_public_recordings.json',
    'data/correction_audio_quality.json', 'config/source_snapshots.json',
]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def input_hashes():
    return {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() for relative in INPUTS}


def is_direct(path):
    return (isinstance(path, str) and path.startswith('audio/')
            and not any(char in path for char in ('\\', '?', '#'))
            and '/excerpts/' not in path and '/context/' not in path
            and not any(part in ('', '.', '..') for part in path.split('/')))


def safe_audio(path):
    if not is_direct(path):
        raise ValueError('Only direct word/syllable recordings are accepted')
    resolved = (ROOT / path).resolve()
    if not resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file():
        raise ValueError(f'Missing or out-of-repository audio: {path}')
    return resolved


def decoded_fingerprints(files):
    """Group exact decoded duplicates independently of MP3 tags/container metadata."""
    cache_path = ROOT / '.audit/native-decoded-fingerprints.json'
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}

    def measure(item):
        relative, sha256 = item
        old = cache.get(relative, {})
        if old.get('sha256') == sha256 and old.get('method') == 'mono16k-pcm16-v1':
            return relative, old
        path = safe_audio(relative)
        output = subprocess.run(
            ['ffmpeg', '-v', 'error', '-i', str(path), '-ar', '16000', '-ac', '1', '-f', 's16le', '-'],
            capture_output=True, check=True,
        ).stdout
        if not output or hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise ValueError(f'Empty or changed audio while fingerprinting: {relative}')
        return relative, {'sha256': sha256, 'method': 'mono16k-pcm16-v1',
                          'pcm_sha256': hashlib.sha256(output).hexdigest()}

    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, (relative, value) in enumerate(pool.map(measure, files.items()), 1):
            cache[relative] = value
            if index % 1000 == 0:
                print(f'Decoded native-source fingerprints: {index}/{len(files)}', flush=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix('.json.part')
    temporary.write_text(json.dumps(cache, indent=2) + '\n')
    temporary.replace(cache_path)
    return {path: cache[path]['pcm_sha256'] for path in files}


def valid_pattern(value, count):
    return bool(isinstance(value, str) and re.fullmatch(r'[1-4N](?:-[1-4N])*', value)
                and len(value.split('-')) == count)


def hypotheses(word, recording, dictionary):
    """Keep lexical citations distinct from plausible spoken realizations."""
    count = len(word['pinyin_syllables'])
    out = []

    def add(value, origin, role, ambiguous=False):
        if valid_pattern(value, count):
            item = {'pattern': value, 'origin': origin, 'role': role, 'ambiguous': ambiguous}
            if item not in out:
                out.append(item)

    add(word['lexical_pattern'], 'original_vocabulary', 'lexical')
    add(recording.get('surface_pattern'), 'recording_annotation', 'spoken')
    add(word.get('default_surface_pattern'), 'vocabulary_default_surface', 'spoken',
        bool(word.get('surface_label_needs_clip_review')))
    readings = [(word['lexical_pattern'], 'original_lexical_sandhi')]
    expected_bases = [base.replace('ü', 'v') for base in word['pinyin_syllables']]
    for item in dictionary.get(word['word'], []):
        reading = item['reading']
        if len(reading) == count and [token[:-1] for token in reading] == expected_bases:
            tones = '-'.join('N' if token[-1] == '5' else token[-1] for token in reading)
            add(tones, 'CC-CEDICT', 'lexical')
            readings.append((tones, 'dictionary_sandhi'))
    for lexical, origin in readings:
        if not valid_pattern(lexical, count):
            continue
        tones = [0 if tone == 'N' else int(tone) for tone in lexical.split('-')]
        surface, tags, aligned = sandhi_surface(word['word'], tones)
        add(pattern(surface), origin, 'spoken', not aligned or 'third_tone_grouping_ambiguous' in tags)
    return out


def original_inventory():
    snapshots = json.loads((ROOT / 'config/source_snapshots.json').read_text())
    quality = json.loads((ROOT / 'data/correction_audio_quality.json').read_text())
    words = read_words()
    word_ids = {word['id']: word for word in words}
    by_text = {}
    for word in words:
        by_text.setdefault(word['word'], []).append(word)
    imported = json.loads((ROOT / 'data/mandarin_native_recordings.json').read_text())
    dictionary_path = ROOT / 'imports/mandarin_native/cedict.txt.gz'
    metadata = json.loads((ROOT / 'data/mandarin_native_words.json').read_text())
    if not dictionary_path.is_file() or hashlib.sha256(dictionary_path.read_bytes()).hexdigest() != metadata['definition_source_sha256']:
        raise ValueError('The pinned dictionary snapshot is missing or has changed')
    dictionary = parse_cedict(load_cedict(dictionary_path))
    rows = []
    files = {}

    def attach(item):
        path = item['audio_path']
        if not is_direct(path):
            raise ValueError('Sentence audio cannot enter the tone inventory')
        if path not in files:
            payload = safe_audio(path).read_bytes()
            files[path] = hashlib.sha256(payload).hexdigest()
        item['sha256'] = files[path]
        item['id'] = digest([item['kind'], path, item.get('word_id'), item.get('key')])
        item['label_identity'] = digest({
            'id': item['id'], 'sha256': item['sha256'], 'syllables': item['syllables'],
            'original_labels': item['original_labels'], 'hypotheses': item['hypotheses'],
        })
        rows.append(item)

    recordings = json.loads((ROOT / 'data/recordings.json').read_text()) + imported['recordings']
    for recording in recordings:
        if recording.get('source') not in ('audio_cmn', 'mandarin_native') or recording.get('language_code', 'zh') != 'zh':
            continue
        if recording.get('source_segment') or recording.get('recording_type') in ('context_sentence', 'aligned_word'):
            continue
        if not is_direct(recording.get('audio_path')):
            continue
        selected = ([word_ids[key] for key in recording['candidate_hsk_ids'] if key in word_ids]
                    if isinstance(recording.get('candidate_hsk_ids'), list) else by_text.get(recording['word'], []))
        for word in selected:
            if recording.get('hsk_id') and recording['hsk_id'] != word['id']:
                continue
            all_hypotheses = hypotheses(word, recording, dictionary)
            supplied = recording.get('surface_pattern') or word.get('default_surface_pattern') or word['lexical_pattern']
            quarantined = (
                recording.get('review_status') == 'rejected'
                or recording.get('source') == 'audio_cmn' and recording.get('quiz_eligible') is False
            )
            attach({
                'kind': 'word', 'word_id': word['id'], 'word': word['word'], 'pinyin': word['pinyin'],
                'audio_path': recording['audio_path'], 'source': recording['source'],
                'source_url': recording.get('source_url'), 'license': recording.get('license'),
                'source_revision': snapshots['audio_cmn']['revision'] if recording['source'] == 'audio_cmn' else imported.get('upstream_manifest_sha256'),
                'speaker': None, 'speaker_identity_known': False,
                'syllables': [base.replace('ü', 'v') for base in word['pinyin_syllables']],
                'original_labels': {'lexical': word['lexical_pattern'], 'surface': supplied,
                                    'recording_surface': recording.get('surface_pattern')},
                'hypotheses': all_hypotheses,
                'weak_training_label': supplied,
                'quarantined': quarantined,
                'original_note': recording.get('notes', ''),
            })
    public = json.loads((ROOT / 'data/pinyin_public_recordings.json').read_text())
    corpora = {
        'audio_cmn_syllables': [
            ('ju4' if path.stem == 'cmn-jv4' else path.stem.removeprefix('cmn-'),
             path.relative_to(ROOT).as_posix(), 'audio_cmn')
            for path in sorted((ROOT / 'audio/audio_cmn/syllabs').glob('*.mp3'))
        ],
        'pinyin_public': [(key, row['audio_path'], 'pinyin_public') for key, row in sorted(public.items())],
    }
    for source, values in corpora.items():
        for key, relative, policy_source in values:
            if not re.fullmatch(r'[a-zv]+[1-4]', key):
                continue
            reference = public[key] if source == 'pinyin_public' else {}
            origin = (reference.get('source_url') if reference else
                      f'{snapshots["audio_cmn"]["repository"]}/blob/{snapshots["audio_cmn"]["revision"]}/64k/syllabs/{Path(relative).name}')
            attach({
                'kind': 'syllable', 'key': key, 'word': None, 'word_id': None,
                'syllables': [key[:-1]], 'audio_path': relative, 'source': source,
                'source_url': origin, 'license': reference.get('license', 'CC-BY-SA'),
                'source_revision': snapshots['pinyin_public' if source == 'pinyin_public' else 'audio_cmn']['revision'],
                'speaker': None, 'speaker_identity_known': False,
                'original_labels': {'lexical': key[-1], 'surface': key[-1], 'recording_surface': key[-1]},
                'hypotheses': [{'pattern': key[-1], 'origin': 'original_source_filename', 'role': 'spoken', 'ambiguous': False}],
                'weak_training_label': key[-1],
                'quarantined': quality.get(policy_source, {}).get(key, {}).get('status') == 'bad',
            })
    fingerprints = decoded_fingerprints(files)
    for row in rows:
        row['decoded_audio_sha256'] = fingerprints[row['audio_path']]
    result = {
        'version': 1, 'input_hashes': input_hashes(),
        'dictionary_sha256': hashlib.sha256(dictionary_path.read_bytes()).hexdigest() if dictionary else None,
        'labels_are_independent_gold': False, 'speaker_identity_known': False,
        'records': rows,
    }
    result['inventory_sha256'] = digest(result)
    return result


def verify_inventory(value, verify_audio=True):
    claimed = value.get('inventory_sha256')
    if claimed != digest({key: item for key, item in value.items() if key != 'inventory_sha256'}):
        raise ValueError('Inventory was modified after creation')
    ids = set()
    checked = {}
    for row in value['records']:
        if row['id'] in ids or not valid_pattern(row['weak_training_label'], len(row['syllables'])):
            raise ValueError('Duplicate inventory identity or malformed original tone labels')
        ids.add(row['id'])
        if not is_direct(row['audio_path']):
            raise ValueError('Inventory contains non-direct audio')
        identity = digest({
            'id': row['id'], 'sha256': row['sha256'], 'syllables': row['syllables'],
            'original_labels': row['original_labels'], 'hypotheses': row['hypotheses'],
        })
        if row['label_identity'] != identity or not re.fullmatch(r'[a-f0-9]{64}', row.get('decoded_audio_sha256', '')):
            raise ValueError('Original label or decoded-duplicate identity changed')
        if verify_audio:
            path = row['audio_path']
            if path not in checked:
                checked[path] = hashlib.sha256(safe_audio(path).read_bytes()).hexdigest()
            if checked[path] != row['sha256']:
                raise ValueError(f'Audio changed after label inventory: {path}')
    return value
