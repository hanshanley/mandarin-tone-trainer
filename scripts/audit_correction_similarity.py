#!/usr/bin/env python3
"""Find duplicate or near-duplicate tones within each syllable family."""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KEY_RE = re.compile(r'([a-zv]+)([1-4])$')


def normalized_waveform(path, length=8000):
    import librosa
    import numpy as np

    samples, _ = librosa.load(path, sr=16000, mono=True)
    samples, _ = librosa.effects.trim(samples, top_db=32)
    samples = samples - samples.mean()
    peak = float(np.max(np.abs(samples))) if len(samples) else 0
    if peak:
        samples = samples / peak
    if len(samples) < 2:
        return np.zeros(length)
    return np.interp(
        np.linspace(0, len(samples) - 1, length),
        np.arange(len(samples)),
        samples,
    )


def similarity(left, right, maximum_lag=80):
    import numpy as np

    best = 0.0
    for lag in range(-maximum_lag, maximum_lag + 1):
        if lag < 0:
            a, b = left[-lag:], right[:lag]
        elif lag > 0:
            a, b = left[:-lag], right[lag:]
        else:
            a, b = left, right
        denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denominator:
            best = max(best, abs(float(np.dot(a, b)) / denominator))
    return best


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=0.995)
    parser.add_argument(
        '--output',
        type=Path,
        default=ROOT / '.audit' / 'correction-similarity-audit.json',
    )
    parser.add_argument('--fail-on-unquarantined', action='store_true')
    args = parser.parse_args()

    quality = json.loads(
        (ROOT / 'data' / 'correction_audio_quality.json').read_text(
            encoding='utf-8'
        )
    )
    sources = {
        'pinyin_public': (ROOT / 'audio' / 'pinyin_public', ''),
        'audio_cmn': (ROOT / 'audio' / 'audio_cmn' / 'syllabs', 'cmn-'),
    }
    report = {'sources': {}, 'unquarantined': []}
    for source, (directory, prefix) in sources.items():
        families = defaultdict(dict)
        for path in directory.glob('*.mp3'):
            key = path.stem.removeprefix(prefix)
            match = KEY_RE.fullmatch(key)
            if match:
                families[match.group(1)][match.group(2)] = path
        flags = []
        for base, tones in sorted(families.items()):
            waves = {
                tone: normalized_waveform(path)
                for tone, path in tones.items()
            }
            ordered = sorted(waves)
            for index, left_tone in enumerate(ordered):
                for right_tone in ordered[index + 1:]:
                    score = similarity(
                        waves[left_tone],
                        waves[right_tone],
                    )
                    if score < args.threshold:
                        continue
                    keys = [base + left_tone, base + right_tone]
                    healthy = [
                        key
                        for key in keys
                        if quality.get(source, {}).get(key, {}).get('status')
                        != 'bad'
                    ]
                    item = {
                        'keys': keys,
                        'similarity': score,
                        'quarantined': len(healthy) <= 1,
                    }
                    flags.append(item)
                    if not item['quarantined']:
                        report['unquarantined'].append({
                            'source': source,
                            **item,
                        })
        report['sources'][source] = flags

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(
        'Similarity audit complete: '
        f"unquarantined={len(report['unquarantined'])}"
    )
    if args.fail_on_unquarantined and report['unquarantined']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
