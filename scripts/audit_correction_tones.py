#!/usr/bin/env python3
"""Audit comparison-audio tone contours with independent pitch trackers."""
import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def contour(path, method):
    import librosa
    import numpy as np
    import parselmouth

    samples, sample_rate = librosa.load(path, sr=16000, mono=True)
    samples, _ = librosa.effects.trim(samples, top_db=32)
    if method == 'pyin':
        frequencies, _, _ = librosa.pyin(
            samples,
            fmin=70,
            fmax=500,
            sr=sample_rate,
            frame_length=1024,
            hop_length=160,
            fill_na=np.nan,
        )
    else:
        pitch = parselmouth.Sound(
            samples.astype(float),
            sample_rate,
        ).to_pitch_ac(
            time_step=0.01,
            pitch_floor=70,
            pitch_ceiling=500,
            voicing_threshold=0.5,
        )
        frequencies = pitch.selected_array['frequency'].astype(float)
        frequencies[frequencies <= 0] = np.nan
    frequencies = frequencies[
        np.isfinite(frequencies) & (frequencies > 85)
    ]
    if len(frequencies) < 5:
        return None
    semitones = 12 * np.log2(frequencies / np.median(frequencies))
    semitones = semitones[np.abs(semitones) <= 12]
    if len(semitones) < 5:
        return None
    return np.interp(
        np.linspace(0, len(semitones) - 1, 9),
        np.arange(len(semitones)),
        semitones,
    ).tolist()


def contour_metrics(values):
    import numpy as np

    values = np.asarray(values)
    start = float(np.median(values[1:3]))
    end = float(np.median(values[6:8]))
    core = values[1:8]
    return {
        'delta': end - start,
        'rise': end - float(np.min(values[1:5])),
        'fall': start - end,
        'range': float(np.ptp(core)),
    }


def contradiction(key, curves):
    tone = key[-1:] if key[-1:] in '1234' else None
    if tone is None or any(curves.get(method) is None for method in ('pyin', 'praat')):
        return None
    metrics = {
        method: contour_metrics(curves[method])
        for method in ('pyin', 'praat')
    }
    if tone == '1' and all(
        abs(values['delta']) > 3.5 and values['range'] > 4
        for values in metrics.values()
    ):
        return 'strongly non-level tone 1', metrics
    if tone == '2':
        if all(
            values['rise'] < 1 and values['range'] < 2
            for values in metrics.values()
        ):
            return 'flat tone-1-like tone 2', metrics
        if all(
            values['fall'] > 3 and values['range'] > 4
            for values in metrics.values()
        ):
            return 'falling tone 2', metrics
    if tone == '4':
        if all(
            values['fall'] < 1 and values['range'] < 2
            for values in metrics.values()
        ):
            return 'flat tone-1-like tone 4', metrics
        if all(
            values['delta'] > 3 and values['range'] > 4
            for values in metrics.values()
        ):
            return 'rising tone 4', metrics
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--output',
        type=Path,
        default=ROOT / '.audit' / 'correction-tone-audit.json',
    )
    parser.add_argument('--fail-on-unquarantined', action='store_true')
    args = parser.parse_args()

    words = json.loads((ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8'))
    quality = json.loads(
        (ROOT / 'data' / 'correction_audio_quality.json').read_text(encoding='utf-8')
    )
    required = {
        f"{base.replace('ü', 'v')}{tone}"
        for word in words
        for base in (word.get('pinyin_syllables') or [])
        for tone in range(1, 5)
    }
    sources = {
        'pinyin_public': (ROOT / 'audio' / 'pinyin_public', ''),
        'audio_cmn': (ROOT / 'audio' / 'audio_cmn' / 'syllabs', 'cmn-'),
    }
    report = {'sources': {}, 'unquarantined': []}
    for source, (directory, prefix) in sources.items():
        flags = []
        paths = sorted(directory.glob('*.mp3'))
        for index, path in enumerate(paths, 1):
            key = path.stem.removeprefix(prefix)
            curves = {
                method: contour(path, method)
                for method in ('pyin', 'praat')
            }
            result = contradiction(key, curves)
            if result is not None:
                reason, metrics = result
                item = {
                    'key': key,
                    'reason': reason,
                    'metrics': metrics,
                    'required': key in required,
                    'quarantined': (
                        quality.get(source, {}).get(key, {}).get('status')
                        == 'bad'
                    ),
                }
                flags.append(item)
                if item['required'] and not item['quarantined']:
                    report['unquarantined'].append({
                        'source': source,
                        **item,
                    })
            if index % 250 == 0:
                print(f'{source}: {index}/{len(paths)}', flush=True)
        report['sources'][source] = flags

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(
        f"Tone audit complete: unquarantined={len(report['unquarantined'])}"
    )
    if args.fail_on_unquarantined and report['unquarantined']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
