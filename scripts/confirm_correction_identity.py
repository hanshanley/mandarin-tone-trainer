#!/usr/bin/env python3
"""Confirm correction-audio identity candidates with Mandarin Paraformer."""
import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    from funasr import AutoModel
    from audit_correction_identity import (
        classify,
        load_completed,
        normalized_bases,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--input',
        type=Path,
        default=ROOT / '.audit' / 'correction-identity-review.jsonl',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=ROOT / '.audit' / 'correction-identity-paraformer.jsonl',
    )
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit('--shard-index must be between 0 and --shard-count - 1')

    rows = [
        json.loads(line)
        for line in args.input.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    completed = load_completed([args.output])
    rows = [
        row
        for index, row in enumerate(rows)
        if index % args.shard_count == args.shard_index
        and row['audio_path'] not in completed
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    model = AutoModel(
        model='paraformer-zh',
        device='cpu',
        disable_update=True,
    )
    counts = Counter()
    with args.output.open('a', encoding='utf-8') as output:
        for index, row in enumerate(rows, 1):
            result = model.generate(
                input=str(ROOT / row['audio_path']),
                batch_size_s=20,
            )[0]
            transcript = result.get('text', '').strip()
            recognized = normalized_bases(transcript)
            status = classify(row['base'], recognized)
            counts[status] += 1
            output.write(json.dumps({
                **row,
                'paraformer_transcript': transcript,
                'paraformer_bases': recognized,
                'paraformer_status': status,
            }, ensure_ascii=False) + '\n')
            output.flush()
            if index % 25 == 0 or index == len(rows):
                print(
                    f'{index}/{len(rows)} '
                    + ' '.join(
                        f'{key}={value}'
                        for key, value in sorted(counts.items())
                    ),
                    flush=True,
                )


if __name__ == '__main__':
    main()
