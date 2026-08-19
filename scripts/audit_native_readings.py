#!/usr/bin/env python3
"""Resumably screen native word recordings for reading mismatches."""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def normalized_syllables(values):
    result = []
    for value in values:
        match = re.fullmatch(r'([a-züv]+)([1-5]?)', value.strip().lower())
        if match:
            result.append(match.group(1).replace('ü', 'v') + match.group(2))
    return result


def recognized_pinyin(text):
    from pypinyin import Style, lazy_pinyin

    values = lazy_pinyin(
        text,
        style=Style.TONE3,
        neutral_tone_with_five=True,
        errors='ignore',
    )
    return normalized_syllables(values)


def bases(values):
    return [re.sub(r'[1-5]$', '', value) for value in values]


def polyphonic_bases(word):
    from pypinyin import Style, pinyin

    if len(word) != 1:
        return set()
    return {
        re.sub(r'[1-5]$', '', value.lower().replace('ü', 'v'))
        for value in pinyin(word, style=Style.TONE3, heteronym=True)[0]
    }


def classify(recognized, expected, transcript='', word=''):
    if transcript and word and normalized_text(transcript) == normalized_text(word):
        return 'text_match'
    if not recognized:
        return 'unrecognized'
    if any(recognized == reading for reading in expected):
        return 'exact'
    if any(bases(recognized) == bases(reading) for reading in expected):
        return 'base_match'
    return 'review'


def normalized_text(value):
    return ''.join(re.findall(r'[\u3400-\u9fffA-Za-z0-9]+', value)).lower()


def load_completed(path):
    completed = set()
    if not path.is_file():
        return completed
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            completed.add(json.loads(line)['audio_path'])
        except (json.JSONDecodeError, KeyError):
            continue
    return completed


def main():
    from faster_whisper import WhisperModel

    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='small')
    parser.add_argument('--output', type=Path, default=ROOT / '.audit' / 'native-readings.jsonl')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--polyphonic-single-character', action='store_true')
    parser.add_argument('--completed-from', type=Path, action='append', default=[])
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit('--shard-index must be between 0 and --shard-count - 1')

    words = json.loads((ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8'))
    recordings = json.loads((ROOT / 'data' / 'recordings.json').read_text(encoding='utf-8'))
    expected = defaultdict(set)
    for word in words:
        syllables = word.get('pinyin_syllables') or []
        tones = word.get('lexical_tones') or []
        if len(syllables) == len(tones):
            reading = tuple(
                base.replace('ü', 'v') + ('5' if tone == 0 else str(tone))
                for base, tone in zip(syllables, tones)
            )
            expected[word['word']].add(reading)

    candidates = [
        recording
        for recording in recordings
        if recording.get('source') == 'audio_cmn'
        and recording.get('recording_type') == 'isolated_word'
        and recording.get('word') in expected
    ]
    if args.polyphonic_single_character:
        candidates = [
            recording
            for recording in candidates
            if len(polyphonic_bases(recording['word'])) > 1
        ]
    completed = load_completed(args.output)
    for completed_path in args.completed_from:
        completed.update(load_completed(completed_path))
    candidates = [
        recording
        for recording in candidates
        if recording['audio_path'] not in completed
    ]
    candidates = [
        recording
        for index, recording in enumerate(candidates)
        if index % args.shard_count == args.shard_index
    ]
    if args.limit is not None:
        candidates = candidates[:args.limit]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(args.model, device='cpu', compute_type='int8')
    counts = Counter()
    with args.output.open('a', encoding='utf-8') as output:
        for index, recording in enumerate(candidates, 1):
            audio_path = recording['audio_path']
            segments, _ = model.transcribe(
                str(ROOT / audio_path),
                language='zh',
                beam_size=5,
                temperature=0,
                condition_on_previous_text=False,
                without_timestamps=True,
                initial_prompt='普通话词语',
            )
            transcript = ''.join(segment.text for segment in segments).strip()
            recognized = recognized_pinyin(transcript)
            readings = [list(reading) for reading in sorted(expected[recording['word']])]
            status = classify(
                recognized,
                readings,
                transcript=transcript,
                word=recording['word'],
            )
            counts[status] += 1
            output.write(json.dumps({
                'audio_path': audio_path,
                'word': recording['word'],
                'expected': readings,
                'transcript': transcript,
                'recognized_pinyin': recognized,
                'status': status,
                'model': args.model,
            }, ensure_ascii=False) + '\n')
            output.flush()
            if index % 25 == 0 or index == len(candidates):
                print(
                    f'{index}/{len(candidates)} '
                    + ' '.join(f'{key}={value}' for key, value in sorted(counts.items())),
                    flush=True,
                )


if __name__ == '__main__':
    main()
