#!/usr/bin/env python3
"""Resumably screen correction clips for base-syllable mismatches."""
import argparse
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KEY_RE = re.compile(r'([a-zv]+)([1-4])$')


def normalized_bases(text):
    from pypinyin import Style, lazy_pinyin

    result = []
    for value in lazy_pinyin(
        text,
        style=Style.TONE3,
        neutral_tone_with_five=True,
        errors='ignore',
    ):
        match = re.fullmatch(
            r'([a-züv]+)[1-5]?',
            value.strip().lower(),
        )
        if match:
            result.append(match.group(1).replace('ü', 'v'))
    return result


def classify(expected_base, recognized):
    if not recognized:
        return 'unrecognized'
    if expected_base in recognized:
        return 'match'
    return 'review'


def load_completed(paths):
    completed = set()
    for path in paths:
        if not path.is_file():
            continue
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
    parser.add_argument(
        '--output',
        type=Path,
        default=ROOT / '.audit' / 'correction-identity.jsonl',
    )
    parser.add_argument('--completed-from', type=Path, action='append', default=[])
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit('--shard-index must be between 0 and --shard-count - 1')

    candidates = []
    for source, directory, prefix in [
        ('pinyin_public', ROOT / 'audio' / 'pinyin_public', ''),
        ('audio_cmn', ROOT / 'audio' / 'audio_cmn' / 'syllabs', 'cmn-'),
    ]:
        for path in sorted(directory.glob('*.mp3')):
            key = path.stem.removeprefix(prefix)
            match = KEY_RE.fullmatch(key)
            if match:
                candidates.append({
                    'source': source,
                    'key': key,
                    'base': match.group(1),
                    'audio_path': str(path.relative_to(ROOT)),
                })

    completed = load_completed([args.output, *args.completed_from])
    candidates = [
        candidate
        for candidate in candidates
        if candidate['audio_path'] not in completed
    ]
    candidates = [
        candidate
        for index, candidate in enumerate(candidates)
        if index % args.shard_count == args.shard_index
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(args.model, device='cpu', compute_type='int8')
    counts = Counter()
    with args.output.open('a', encoding='utf-8') as output:
        for index, candidate in enumerate(candidates, 1):
            segments, _ = model.transcribe(
                str(ROOT / candidate['audio_path']),
                language='zh',
                beam_size=5,
                temperature=0,
                condition_on_previous_text=False,
                without_timestamps=True,
                initial_prompt='单个普通话音节',
            )
            transcript = ''.join(segment.text for segment in segments).strip()
            recognized = normalized_bases(transcript)
            status = classify(candidate['base'], recognized)
            counts[status] += 1
            output.write(json.dumps({
                **candidate,
                'transcript': transcript,
                'recognized_bases': recognized,
                'status': status,
                'model': args.model,
            }, ensure_ascii=False) + '\n')
            output.flush()
            if index % 25 == 0 or index == len(candidates):
                print(
                    f'{index}/{len(candidates)} '
                    + ' '.join(
                        f'{key}={value}'
                        for key, value in sorted(counts.items())
                    ),
                    flush=True,
                )


if __name__ == '__main__':
    main()
