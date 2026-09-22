#!/usr/bin/env python3
"""Audit comparison-audio tone contours with independent pitch trackers."""
import argparse
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METHODS = ('pyin', 'praat', 'world')


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
    elif method == 'praat':
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
    else:
        import pyworld

        frequencies, time_axis = pyworld.harvest(
            samples.astype('float64'),
            sample_rate,
            f0_floor=70,
            f0_ceil=500,
            frame_period=10,
        )
        frequencies = pyworld.stonemask(
            samples.astype('float64'),
            frequencies,
            time_axis,
            sample_rate,
        )
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
        'dip': start - float(np.min(values[1:6])),
    }


def contradiction(key, curves):
    tone = key[-1:] if re.fullmatch(r'[a-zv]+[1-4]', key) else None
    if tone is None or any(not valid_curve(curves.get(method)) for method in METHODS):
        return None
    metrics = {
        method: contour_metrics(curves[method])
        for method in METHODS
    }
    if tone == '1' and all(
        abs(values['delta']) > 3.5 and values['range'] > 4
        for values in metrics.values()
    ):
        return 'strongly non-level tone 1', metrics
    if tone == '2':
        if all(
            values['rise'] < 1.25 and values['range'] < 2
            for values in metrics.values()
        ):
            return 'flat tone-1-like tone 2', metrics
        if all(
            values['fall'] > 3 and values['range'] > 4
            for values in metrics.values()
        ):
            return 'falling tone 2', metrics
    if tone == '3' and all(
        values['delta'] > 3 and values['dip'] < 1.25
        for values in metrics.values()
    ):
        return 'rising-only isolated tone 3; listening review required', metrics
    if tone == '4':
        if all(
            values['fall'] < 1.25 and values['range'] < 2
            for values in metrics.values()
        ):
            return 'flat tone-1-like tone 4', metrics
        if all(
            values['delta'] > 3 and values['range'] > 4
            for values in metrics.values()
        ):
            return 'rising tone 4', metrics
    return None


def valid_curve(values):
    import math

    return (
        isinstance(values, (list, tuple))
        and len(values) == 9
        and all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)
    )


def assess(key, curves):
    import numpy as np

    missing = [method for method in METHODS if not valid_curve(curves.get(method))]
    if missing:
        return {
            'status': 'review',
            'reason': 'missing or invalid pitch tracks: ' + ', '.join(missing),
        }
    result = contradiction(key, curves)
    if result is not None:
        reason, metrics = result
        return {'status': 'review', 'reason': reason, 'metrics': metrics}
    centered = [
        np.asarray(curves[method][1:8]) - np.median(curves[method][1:8])
        for method in METHODS
    ]
    disagreement = max(
        float(np.sqrt(np.mean((left - right) ** 2)))
        for index, left in enumerate(centered)
        for right in centered[index + 1:]
    )
    if disagreement > 3:
        return {
            'status': 'review',
            'reason': 'pitch trackers disagree on contour shape',
            'disagreement_semitones': disagreement,
        }
    return {
        'status': 'screened_only',
        'reason': 'no heuristic flag; independent listening approval still required',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--output',
        type=Path,
        default=ROOT / '.audit' / 'correction-tone-audit.json',
    )
    parser.add_argument('--fail-on-unquarantined', action='store_true')
    parser.add_argument('--keys', nargs='+', help='screen only these syllable-tone keys; not a whole-corpus audit')
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
    report = {
        'scope': 'selected_keys' if args.keys else 'whole_comparison_corpus',
        'requested_keys': args.keys,
        'certifies_accuracy': False,
        'sources': {},
        'unquarantined': [],
    }
    found_keys = set()
    for source, (directory, prefix) in sources.items():
        results = []
        paths = sorted(directory.glob('*.mp3'))
        if not paths:
            raise SystemExit(f'No audio to audit in {directory}; run setup first')
        for index, path in enumerate(paths, 1):
            key = path.stem.removeprefix(prefix)
            if key == 'jv4':
                key = 'ju4'
            if not re.fullmatch(r'[a-zv]+[1-4]', key):
                continue
            if args.keys and key not in args.keys:
                continue
            found_keys.add(key)
            curves = {
                method: contour(path, method)
                for method in METHODS
            }
            item = {
                'key': key,
                'audio_path': path.relative_to(ROOT).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'curves': curves,
                **assess(key, curves),
                'required': key in required,
                'quarantined': quality.get(source, {}).get(key, {}).get('status') == 'bad',
            }
            results.append(item)
            if item['status'] == 'review' and item['required'] and not item['quarantined']:
                report['unquarantined'].append({'source': source, **item})
            if index % 250 == 0:
                print(f'{source}: {index}/{len(paths)}', flush=True)
        report['sources'][source] = results

    if args.keys and set(args.keys) - found_keys:
        raise SystemExit(f"No recordings found for requested keys: {sorted(set(args.keys) - found_keys)}")
    report['summary'] = {
        status: sum(
            item['status'] == status
            for results in report['sources'].values()
            for item in results
        )
        for status in ('review', 'screened_only')
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    print(
        f"Tone screening ({report['scope']}): {report['summary']}; "
        f"unquarantined={len(report['unquarantined'])}. "
        "This is not listening approval or an accuracy certificate."
    )
    if args.fail_on_unquarantined and report['unquarantined']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
