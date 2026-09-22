#!/usr/bin/env python3
"""Resumable, content-addressed acoustic and ASR evidence; never invent human reviews."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE_VERSION = 'dc70-pyin60-praat60-world60-10ms-1'
ASR_VERSION = 'paraformer-unprompted-1'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    if not path.exists():
        return {}
    rows = {}
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f'{path}:{number}: incomplete evidence record; repair before resuming') from error
        rows[row['audio_path']] = row
    return rows


def measure(relative):
    from acoustic_analysis import extract

    result = extract(ROOT / relative)
    result['audio_path'] = relative
    result['evidence_version'] = PROFILE_VERSION
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--phase', choices=['profiles', 'asr'], required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or not 1 <= args.batch_size <= 32:
        parser.error('workers must be 1-4 and batch-size must be 1-32')
    candidates = json.loads(args.candidates.read_text(encoding='utf-8'))['candidates']
    paths = sorted({row['audio_path'] for row in candidates if row['kind'] in ('native', 'comparison')})
    output = args.output or ROOT / '.audit' / f'acoustic-{args.phase}.jsonl'
    version = PROFILE_VERSION if args.phase == 'profiles' else ASR_VERSION
    completed = read_jsonl(output)
    pending = [
        relative for relative in paths
        if completed.get(relative, {}).get('sha256') != sha256(ROOT / relative)
        or completed.get(relative, {}).get('evidence_version') != version
    ]
    if args.limit is not None:
        pending = pending[:args.limit]
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f'{args.phase}: {len(pending)} pending / {len(paths)} unique isolated clips', flush=True)
    with output.open('a', encoding='utf-8') as stream:
        if args.phase == 'profiles':
            with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
                for index, row in enumerate(pool.map(measure, pending, chunksize=8), 1):
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                    stream.flush()
                    if index % 100 == 0 or index == len(pending):
                        print(f'profiles: {index}/{len(pending)}', flush=True)
        elif pending:
            from funasr import AutoModel
            from audit_native_readings import recognized_pinyin

            model = AutoModel(
                model='paraformer-zh', device='cpu', ncpu=args.workers,
                disable_update=True, disable_pbar=True,
            )
            for offset in range(0, len(pending), args.batch_size):
                batch = pending[offset:offset + args.batch_size]
                by_key = {Path(relative).stem: relative for relative in batch}
                if len(by_key) != len(batch):
                    raise ValueError('ASR batch has ambiguous basenames; retry with --batch-size 1')
                hashes = {relative: sha256(ROOT / relative) for relative in batch}
                results = model.generate(
                    input=[str(ROOT / relative) for relative in batch],
                    batch_size=args.batch_size, disable_pbar=True,
                )
                if len(results) != len(batch):
                    raise ValueError('ASR returned a different number of results than input clips')
                if {result.get('key') for result in results} != set(by_key):
                    raise ValueError('ASR result keys do not match their input clips')
                for result in results:
                    relative = by_key[result['key']]
                    if sha256(ROOT / relative) != hashes[relative]:
                        raise ValueError(f'Audio changed during recognition: {relative}')
                    transcript = result.get('text', '').strip()
                    row = {
                        'audio_path': relative,
                        'sha256': hashes[relative],
                        'evidence_version': ASR_VERSION,
                        'model': 'paraformer-zh',
                        'text': transcript,
                        'recognized_pinyin': recognized_pinyin(transcript),
                        'timestamps_ms': result.get('timestamp', []),
                    }
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                stream.flush()
                print(f'asr: {min(offset + len(batch), len(pending))}/{len(pending)}', flush=True)


if __name__ == '__main__':
    main()
