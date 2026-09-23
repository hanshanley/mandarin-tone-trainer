#!/usr/bin/env python3
"""Build source vocabulary and reproducible, transcript-aligned word excerpts."""
import argparse
import hashlib
import io
import json
import re
import subprocess
import unicodedata
import wave
from collections import Counter, defaultdict
from pathlib import Path

from add_pinyin_syllables import load_vocab, plain, split, syllable_tones
from build_hsk_data import pattern, sandhi_surface
from collect_acoustic_evidence import read_jsonl
from download_mandarin_native import fetch_bytes, normalized_source_key
from runtime_data import ROOT, read_words


SOURCE_CACHE = ROOT / 'imports/mandarin_native/explore.json'
SENTENCE_EVIDENCE = ROOT / '.audit/context-transcripts.jsonl'
RATE = 16000
ALIGNMENT_METHOD = 'exact-unprompted-transcript-fa-zh-1'


def write_json(path, value):
    temporary = path.with_suffix('.json.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def source_rows():
    index = json.loads((ROOT / 'data/mandarin_native_recordings.json').read_text(encoding='utf-8'))
    payload = SOURCE_CACHE.read_bytes() if SOURCE_CACHE.is_file() else fetch_bytes(
        index['explore_dataset_url'] + '?v=' + index['source_client_cache_version']
    )
    if hashlib.sha256(payload).hexdigest() != index['explore_dataset_sha256']:
        raise ValueError('Explore source snapshot differs from the imported recordings')
    if not SOURCE_CACHE.is_file():
        SOURCE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SOURCE_CACHE.write_bytes(payload)
    return json.loads(payload), index


def parse_token(token, inventory):
    hanzi, pinyin = token.get('hanzi', ''), token.get('pinyin', '')
    if not re.fullmatch(r'[\u3400-\u9fff]+', hanzi) or not pinyin.strip():
        return None
    pinyin = unicodedata.normalize('NFC', pinyin).replace('ɡ', 'g').lower()
    item = {'pinyin': pinyin, 'lexical_tones': [0] * len(hanzi)}
    syllables = split(item, inventory)
    if not syllables or any(base in ('r', 'm', 'n', 'ng', 'hm', 'hng') for base in syllables):
        return None
    item['pinyin_syllables'] = syllables
    tones = syllable_tones(item)
    if tones is None or len(tones) != len(hanzi):
        return None
    letters = [letter for letter in pinyin if plain(letter)]
    fragments, offset = [], 0
    for base in syllables:
        fragments.append(''.join(letters[offset:offset + len(base)]))
        offset += len(base)
    if offset != len(letters):
        return None
    return {'word': hanzi, 'pinyin_syllables': syllables, 'lexical_tones': tones, 'fragments': fragments}


def reading_key(word):
    return word['word'], tuple(base.replace('ü', 'v') for base in word['pinyin_syllables']), tuple(word['lexical_tones'])


def vocabulary(rows, hsk_words, inventory):
    existing = {reading_key(word): word['id'] for word in hsk_words}
    discovered = {}
    rejected = Counter()
    for row in rows:
        for token in row['tokens']:
            if not token.get('pinyin', '').strip():
                continue
            parsed = parse_token(token, inventory)
            if parsed is None:
                rejected['unresolved source-token segmentation'] += 1
                continue
            values = [parsed] + [
                {'word': char, 'pinyin_syllables': [base], 'lexical_tones': [tone], 'fragments': [fragment]}
                for char, base, tone, fragment in zip(
                    parsed['word'], parsed['pinyin_syllables'], parsed['lexical_tones'], parsed['fragments']
                )
            ]
            for value in values:
                key = reading_key(value)
                if key in existing or key in discovered:
                    continue
                identifier = 'MN-' + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
                surface, tags, aligned = sandhi_surface(value['word'], value['lexical_tones'])
                discovered[key] = {
                    'id': identifier, 'word': value['word'], 'traditional': value['word'],
                    'pinyin': ' '.join(value['fragments']), 'pos': '', 'level': 'source',
                    'pinyin_syllables': value['pinyin_syllables'],
                    'lexical_tones': value['lexical_tones'], 'lexical_pattern': pattern(value['lexical_tones']),
                    'default_surface_tones': surface, 'default_surface_pattern': pattern(surface),
                    'sandhi_tags': tags,
                    'surface_label_needs_clip_review': not aligned or 'third_tone_grouping_ambiguous' in tags,
                    'vocabulary_source': 'mandarin_native',
                    'source_entry_id': row['id'],
                    'definition': '',
                }
    return sorted(discovered.values(), key=lambda word: word['id']), dict(rejected)


def sentence_text(row):
    return ''.join(re.findall(r'[\u3400-\u9fff]', ''.join(token['hanzi'] for token in row['tokens'])))


def decode_pcm(relative_path):
    file = (ROOT / relative_path).resolve()
    if not file.is_relative_to(ROOT) or not relative_path.startswith('audio/'):
        raise ValueError(f'Audio path escapes source corpus: {relative_path}')
    return subprocess.run(
        ['ffmpeg', '-v', 'error', '-i', str(file), '-f', 's16le', '-acodec', 'pcm_s16le',
         '-ar', str(RATE), '-ac', '1', '-'],
        check=True, capture_output=True,
    ).stdout


def crop_wave(pcm, first, last):
    if not isinstance(first, int) or not isinstance(last, int) or not 0 <= first < last <= len(pcm) // 2:
        raise ValueError('Crop boundaries are outside the decoded source')
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm[first * 2:last * 2])
    return stream.getvalue()


def validated_timestamps(row, evidence, duration):
    text = sentence_text(row)
    if evidence.get('recognized_text') != text or evidence.get('source_text') != text:
        return None
    stamps = evidence.get('timestamps_ms', [])
    if len(stamps) != len(text):
        return None
    previous = 0
    for stamp in stamps:
        if not isinstance(stamp, list) or len(stamp) != 2:
            return None
        start, end = stamp
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        if start < previous - 10 or start < 0 or end <= start or end > duration * 1000 + 5:
            return None
        previous = end
    return stamps


def spans_for(row, words_by_reading, inventory):
    tokens, offset, all_tones, all_chars = [], 0, [], []
    phrases, phrase_start = [], 0
    for token in row['tokens']:
        chars = ''.join(re.findall(r'[\u3400-\u9fff]', token['hanzi']))
        if not chars:
            if token.get('pinyin', '').strip():
                return []
            if token['hanzi'].strip() and offset > phrase_start:
                phrases.append((phrase_start, offset))
                phrase_start = offset
            continue
        parsed = parse_token(token, inventory)
        if not parsed:
            return []
        tokens.append((offset, parsed))
        offset += len(chars)
        all_chars.extend(chars)
        all_tones.extend(parsed['lexical_tones'])
    if offset > phrase_start:
        phrases.append((phrase_start, offset))
    surface = list(all_tones)
    uncertain = set()
    for first, last in phrases:
        tones = all_tones[first:last]
        values, _, aligned = sandhi_surface(''.join(all_chars[first:last]), tones)
        if not aligned:
            return []
        surface[first:last] = values
        position = first
        while position < last:
            end = position
            while end < last and all_tones[end] == 3:
                end += 1
            if end - position >= 3:
                uncertain.update(range(position, end))
            position = max(position + 1, end)
    result = []
    for start, token in tokens:
        units = [(start, token)] + [
            (start + index, {'word': char, 'pinyin_syllables': [base], 'lexical_tones': [tone]})
            for index, (char, base, tone) in enumerate(zip(
                token['word'], token['pinyin_syllables'], token['lexical_tones']
            ))
        ]
        for position, unit in units:
            identifier = words_by_reading.get(reading_key(unit))
            size = len(unit['word'])
            if identifier and not uncertain.intersection(range(position, position + size)):
                result.append({
                    'word_id': identifier, 'word': unit['word'], 'character_start': position,
                    'character_end': position + size,
                    'surface_pattern': pattern(surface[position:position + size]),
                })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True, choices=['vocabulary', 'align', 'extract', 'restore'])
    parser.add_argument('--max-examples', type=int, default=2)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if not 1 <= args.max_examples <= 5:
        parser.error('--max-examples must be 1-5')
    output_path = ROOT / 'data/context_word_recordings.json'
    if args.phase == 'restore':
        index = json.loads(output_path.read_text(encoding='utf-8'))
        grouped = defaultdict(list)
        for recording in index['recordings']:
            grouped[recording['source_segment']['audio_path']].append(recording)
        restored = 0
        for relative, recordings in grouped.items():
            pending = [r for r in recordings if not (ROOT / r['audio_path']).is_file()
                       or hashlib.sha256((ROOT / r['audio_path']).read_bytes()).hexdigest() != r['sha256']]
            if not pending:
                continue
            if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != pending[0]['source_segment']['sha256']:
                raise ValueError(f'Source audio changed: {relative}')
            pcm = decode_pcm(relative)
            for recording in pending:
                origin = recording['source_segment']
                payload = crop_wave(pcm, origin['start_sample'], origin['end_sample'])
                if hashlib.sha256(payload).hexdigest() != recording['sha256']:
                    raise ValueError(f'Crop reconstruction changed: {recording["audio_path"]}')
                path = ROOT / recording['audio_path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                restored += 1
        print(f'Restored {restored} exact word excerpts.')
        return
    rows, index = source_rows()
    inventory = load_vocab(ROOT / 'audio/audio_cmn/syllabs')
    if args.phase == 'vocabulary':
        from add_definitions import CEDICT_URL, choose_definition, load_cedict, parse_cedict

        hsk = json.loads((ROOT / 'data/hsk_words.json').read_text(encoding='utf-8'))
        words, rejected = vocabulary(rows, hsk, inventory)
        dictionary_path = SOURCE_CACHE.parent / 'cedict.txt.gz'
        if not dictionary_path.is_file():
            dictionary_path.write_bytes(fetch_bytes(CEDICT_URL))
        definitions = parse_cedict(load_cedict(dictionary_path))
        for word in words:
            word['definition'] = choose_definition(word, definitions.get(word['word'], []))
        write_json(ROOT / 'data/mandarin_native_words.json', {
            'version': 1, 'source_dataset_sha256': index['explore_dataset_sha256'],
            'definition_source': CEDICT_URL, 'definition_license': 'CC BY-SA 4.0',
            'definition_source_sha256': hashlib.sha256(dictionary_path.read_bytes()).hexdigest(),
            'words': words, 'unresolved': rejected,
        })
        all_words = hsk + words
        for recording in index['recordings']:
            if recording['recording_type'] != 'word_candidate':
                continue
            key = normalized_source_key(recording['source_audio_key'])
            recording['candidate_hsk_ids'] = [
                word['id'] for word in all_words if normalized_source_key(word['pinyin']) == key
            ]
        write_json(ROOT / 'data/mandarin_native_recordings.json', index)
        print(f'Added {len(words)} source word/character readings ({sum(bool(word["definition"]) for word in words)} definitions); unresolved={rejected}')
        return
    by_reference = {r['source_audio_reference']: r for r in index['recordings'] if r['recording_type'] == 'context_sentence'}
    if args.phase == 'align':
        from funasr import AutoModel
        from collect_acoustic_evidence import prepare_recognition
        import numpy as np

        completed = read_jsonl(SENTENCE_EVIDENCE)
        model = AutoModel(model='paraformer-zh', device='cpu', ncpu=2, disable_update=True, disable_pbar=True)
        aligner = AutoModel(model='fa-zh', device='cpu', ncpu=2, disable_update=True, disable_pbar=True)
        pending = [row for row in rows if (
            completed.get(by_reference[row['audio']]['audio_path'], {}).get('sha256') != by_reference[row['audio']]['sha256']
            or completed.get(by_reference[row['audio']]['audio_path'], {}).get('source_text') != sentence_text(row)
            or completed.get(by_reference[row['audio']]['audio_path'], {}).get('method') != ALIGNMENT_METHOD
        )]
        if args.limit is not None:
            pending = pending[:args.limit]
        SENTENCE_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        with SENTENCE_EVIDENCE.open('a', encoding='utf-8') as output:
            for number, row in enumerate(pending, 1):
                recording = by_reference[row['audio']]
                relative = recording['audio_path']
                if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != recording['sha256']:
                    raise ValueError(f'Source file changed: {relative}')
                pcm = decode_pcm(relative)
                signal = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
                prepared, _ = prepare_recognition(signal, RATE)
                recognition = model.generate(input=prepared, fs=RATE, disable_pbar=True)[0]
                recognized = ''.join(re.findall(r'[\u3400-\u9fff]', recognition.get('text', '')))
                text = sentence_text(row)
                stamps = []
                if recognized == text and not re.search(r'[A-Za-z0-9]', recognition.get('text', '')):
                    result = aligner.generate(
                        input=(signal, ' '.join(text)), data_type=('sound', 'text'), disable_pbar=True,
                    )[0]
                    stamps = result.get('timestamp', [])
                result = {
                    'audio_path': relative, 'sha256': recording['sha256'], 'method': ALIGNMENT_METHOD,
                    'source_text': text, 'recognized_text': recognized,
                    'transcript': recognition.get('text', ''), 'timestamps_ms': stamps,
                    'duration': len(signal) / RATE,
                }
                output.write(json.dumps(result, ensure_ascii=False) + '\n')
                output.flush()
                if number % 100 == 0 or number == len(pending):
                    print(f'Context transcript/alignment: {number}/{len(pending)}', flush=True)
        return
    completed = read_jsonl(SENTENCE_EVIDENCE)
    words = read_words()
    words_by_reading = {reading_key(word): word['id'] for word in words}
    proposals = defaultdict(dict)
    skipped = Counter()
    for row in rows:
        parent = by_reference[row['audio']]
        evidence = completed.get(parent['audio_path'], {})
        if evidence.get('sha256') != parent['sha256'] or evidence.get('method') != ALIGNMENT_METHOD:
            skipped['no current sentence evidence'] += 1
            continue
        stamps = validated_timestamps(row, evidence, evidence['duration'])
        if not stamps:
            skipped['sentence transcript or boundaries unresolved'] += 1
            continue
        for span in spans_for(row, words_by_reading, inventory):
            first, last = span['character_start'], span['character_end']
            start, end = stamps[first][0], stamps[last - 1][1]
            duration = (end - start) / 1000
            if duration < .12 * (last - first) or duration > 1.2 * (last - first) or span['surface_pattern'] == 'N':
                continue
            origin = {
                'audio_path': parent['audio_path'], 'sha256': parent['sha256'],
                'start_sample': round(start * RATE / 1000), 'end_sample': round(end * RATE / 1000),
                'sample_rate': RATE,
            }
            key = hashlib.sha256(json.dumps([span, origin], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            proposals[span['word_id']][key] = {
                'source': 'mandarin_native', 'recording_type': 'aligned_word',
                'word': span['word'], 'candidate_hsk_ids': [span['word_id']], 'hsk_id': span['word_id'],
                'surface_pattern': span['surface_pattern'],
                'audio_path': f'audio/mandarin_native/excerpts/{key}.wav', 'filename': key + '.wav',
                'source_audio_key': key, 'source_recording_id': 'mandarin-native/word-excerpt/' + key,
                'source_url': parent['source_url'], 'source_segment': origin,
                'source_entry_id': row['id'], 'source_text': sentence_text(row),
                'character_span': [first, last], 'alignment_method': ALIGNMENT_METHOD,
                'speaker': 'source sentence', 'language_code': 'zh', 'language_status': 'unverified',
                'license': parent['license'], 'rights_status': parent['rights_status'],
                'review_status': 'pending', 'quiz_eligible': False,
                'notes': 'Aligned word excerpt. Full-sentence ASR matched the source; the cropped recording still requires independent identity and tone screening.',
            }
    chosen = []
    for choices in proposals.values():
        ordered = sorted(choices.values(), key=lambda r: (
            -((r['source_segment']['end_sample'] - r['source_segment']['start_sample']) / len(r['word'])),
            r['audio_path'],
        ))
        chosen.extend(ordered[:args.max_examples])
    existing = {r['audio_path']: r for r in json.loads(output_path.read_text(encoding='utf-8'))['recordings']}
    grouped = defaultdict(list)
    for recording in chosen:
        grouped[recording['source_segment']['audio_path']].append(recording)
    generated = []
    for relative, recordings in grouped.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != recordings[0]['source_segment']['sha256']:
            raise ValueError(f'Source file changed: {relative}')
        pcm = decode_pcm(relative)
        for recording in recordings:
            origin = recording['source_segment']
            payload = crop_wave(pcm, origin['start_sample'], origin['end_sample'])
            digest = hashlib.sha256(payload).hexdigest()
            previous = existing.get(recording['audio_path'])
            if previous and previous.get('sha256') != digest:
                raise ValueError('Existing crop differs from its source reconstruction')
            path = ROOT / recording['audio_path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            generated.append(previous or {**recording, 'sha256': digest, 'byte_length': len(payload)})
    write_json(output_path, {
        'version': 1, 'source_dataset_sha256': index['explore_dataset_sha256'],
        'recordings': generated, 'unresolved_sentences': dict(skipped),
    })
    print(f'Extracted {len(generated)} word examples for {len(proposals)} readings; exclusions={dict(skipped)}')


if __name__ == '__main__':
    main()
