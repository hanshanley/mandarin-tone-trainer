"""Time-aligned pitch evidence for automatic tone screening, not human certification."""
import hashlib
import io
from pathlib import Path

import librosa
import numpy as np
import parselmouth
import pyworld
from scipy.signal import butter, sosfiltfilt


VERSION = 'spectral-consensus-1'
SAMPLE_RATE = 16000
HOP = 160
PITCH_FLOOR = 60
PITCH_CEILING = 600
METHODS = ('pyin', 'praat', 'world')


def prepare_signal(samples, sample_rate=SAMPLE_RATE):
    samples = np.asarray(samples, dtype=np.float64)
    if len(samples) < 64 or not np.isfinite(samples).all():
        raise ValueError('Audio is too short or contains non-finite samples')
    centered = samples - np.mean(samples)
    # Match the reference player's low-frequency cleanup before estimating F0.
    return np.ascontiguousarray(sosfiltfilt(butter(3, 70, fs=sample_rate, btype='highpass', output='sos'), centered))


def on_grid(times, frequencies, size):
    result = np.full(size, np.nan)
    for time, frequency in zip(times, frequencies):
        index = round(float(time) * SAMPLE_RATE / HOP)
        if 0 <= index < size and np.isfinite(frequency) and PITCH_FLOOR <= frequency <= PITCH_CEILING:
            result[index] = frequency
    return result


def extract(path):
    path = Path(path)
    payload = path.read_bytes()
    samples, sample_rate = librosa.load(io.BytesIO(payload), sr=SAMPLE_RATE, mono=True)
    signal = prepare_signal(samples, sample_rate)
    rms = librosa.feature.rms(y=signal, frame_length=512, hop_length=HOP)[0]
    size = len(rms)
    frequencies, _, probabilities = librosa.pyin(
        signal, fmin=PITCH_FLOOR, fmax=PITCH_CEILING, sr=sample_rate,
        frame_length=1024, hop_length=HOP, fill_na=np.nan,
    )
    frequencies[probabilities < .1] = np.nan
    pyin = on_grid(librosa.times_like(frequencies, sr=sample_rate, hop_length=HOP), frequencies, size)
    pitch = parselmouth.Sound(signal, sample_rate).to_pitch_ac(
        time_step=.01, pitch_floor=PITCH_FLOOR, pitch_ceiling=PITCH_CEILING,
        voicing_threshold=.5,
    )
    frequencies = pitch.selected_array['frequency'].copy()
    frequencies[pitch.selected_array['strength'] < .55] = np.nan
    praat = on_grid(pitch.xs(), frequencies, size)
    frequencies, times = pyworld.harvest(
        signal, sample_rate, f0_floor=PITCH_FLOOR, f0_ceil=PITCH_CEILING, frame_period=10,
    )
    frequencies = pyworld.stonemask(signal, frequencies, times, sample_rate)
    world = on_grid(times, frequencies, size)
    tracks = {'pyin': pyin, 'praat': praat, 'world': world}
    active = rms >= max(float(np.max(rms)) * .08, .00005)
    for values in tracks.values():
        values[~active] = np.nan
    return {
        'version': VERSION,
        'audio_path': path.as_posix(),
        'sha256': hashlib.sha256(payload).hexdigest(),
        'duration': round(len(samples) / sample_rate, 5),
        'dc_offset': round(float(np.mean(samples)), 6),
        'clipped_fraction': round(float(np.mean(np.abs(samples) >= .999)), 6),
        'rms': np.round(rms, 6).tolist(),
        'tracks': {
            method: [round(float(value), 3) if np.isfinite(value) else None for value in values]
            for method, values in tracks.items()
        },
    }


def segment(profile, start=0, end=None):
    end = profile['duration'] if end is None else end
    if not 0 <= start < end <= profile['duration'] + .02:
        return {'status': 'review', 'reason': 'invalid syllable interval'}
    tracks = {
        method: np.array([value if value is not None else np.nan for value in profile['tracks'][method]])
        for method in METHODS
    }
    size = len(tracks['pyin'])
    times = np.arange(size) * HOP / SAMPLE_RATE
    valid = (times >= start) & (times <= end)
    consensus = np.full(size, np.nan)
    for index in np.flatnonzero(valid):
        pitches = [values[index] for values in tracks.values() if np.isfinite(values[index])]
        pairs = [
            (abs(12 * np.log2(left / right)), np.sqrt(left * right))
            for number, left in enumerate(pitches)
            for right in pitches[number + 1:]
        ]
        if pairs:
            difference, frequency = min(pairs)
            if difference <= 2:
                consensus[index] = frequency
    voiced = np.flatnonzero(np.isfinite(consensus))
    if len(voiced) < 12:
        return {'status': 'review', 'reason': 'insufficient jointly voiced frames'}
    first, last = voiced[0], voiced[-1]
    duration = (last - first) * HOP / SAMPLE_RATE
    if duration < .12 or len(voiced) / (last - first + 1) < .45:
        return {'status': 'review', 'reason': 'insufficient continuous pitch evidence'}
    points = np.linspace(first, last, 19)[1:-1]
    radius = max(1, (last - first) / 36)
    curves = {}
    for method, values in tracks.items():
        curve = np.full(17, np.nan)
        for index, center in enumerate(points):
            window = values[max(first, int(center - radius)):min(last + 1, int(center + radius) + 1)]
            window = window[np.isfinite(window)]
            if len(window):
                curve[index] = np.median(window)
        present = np.flatnonzero(np.isfinite(curve))
        if len(present) < 13 or present[0] > 1 or present[-1] < 15 or np.max(np.diff(present)) > 4:
            continue
        curve = np.interp(np.arange(17), present, curve[present])
        if np.mean(curve <= PITCH_FLOOR * 1.05) > .15:
            continue
        curves[method] = np.round(curve, 3).tolist()
    if len(curves) < 2:
        return {'status': 'review', 'reason': 'fewer than two usable time-aligned pitch tracks'}
    deviations = []
    ordered = list(curves.values())
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            deviations.append(float(np.sqrt(np.mean((12 * np.log2(np.array(left) / right)) ** 2))))
    if min(deviations) > 2:
        return {'status': 'review', 'reason': 'pitch trackers disagree', 'deviation': round(min(deviations), 3)}
    return {
        'status': 'measured',
        'start': round(float(times[first]), 4),
        'end': round(float(times[last]), 4),
        'voiced_seconds': round(len(voiced) * .01, 3),
        'curves': curves,
        'median_hz': round(float(np.median(consensus[voiced])), 3),
        'high_hz': round(float(np.quantile(consensus[voiced], .9)), 3),
    }


def curve_features(values):
    values = np.asarray(values, dtype=float)
    semitones = 12 * np.log2(values / np.median(values))
    smooth = np.array([np.median(semitones[max(0, i - 1):i + 2]) for i in range(len(values))])
    first = float(np.median(smooth[:3]))
    last = float(np.median(smooth[-3:]))
    low_index = int(np.argmin(smooth))
    low = float(smooth[low_index])
    return {
        'delta': last - first,
        'range': float(np.quantile(smooth, .95) - np.quantile(smooth, .05)),
        'dip': first - low,
        'rebound': last - low,
        'trough': low_index / (len(smooth) - 1),
        'median_hz': float(np.median(values)),
        'start_hz': float(np.median(values[:3])),
    }


def classify_curve(values, high_reference=None, connected=False):
    feature = curve_features(values)
    relative = feature['median_hz'] / high_reference if high_reference else None
    start_relative = feature['start_hz'] / high_reference if high_reference else None
    if feature['range'] <= 2.6 and abs(feature['delta']) <= 1.8:
        if relative is None:
            return None
        if relative >= .82:
            return '1'
        if connected and relative <= .65:
            return '3'
        return None
    if feature['delta'] >= 2.2 and feature['dip'] <= 2 and feature['trough'] <= .4:
        return '2'
    if feature['dip'] >= 3 and feature['rebound'] >= 2.5 and .25 <= feature['trough'] <= .8:
        return '3'
    if feature['delta'] <= -3.2 and feature['rebound'] <= 1.8 and feature['trough'] >= .6:
        if connected and start_relative is not None and start_relative < .78:
            return '3'
        return '4'
    if connected and relative is not None and relative < .65 and feature['delta'] <= 0:
        return '3'
    return None


def decide(measured, expected, high_reference=None, connected=False):
    if measured['status'] != 'measured':
        return measured
    votes = {
        method: classify_curve(curve, high_reference, connected)
        for method, curve in measured['curves'].items()
    }
    resolved = [value for value in votes.values() if value is not None]
    if len(resolved) < 2 or set(resolved) != {str(expected)}:
        return {
            'status': 'review', 'reason': 'ambiguous or conflicting tone evidence',
            'expected': str(expected), 'votes': votes,
        }
    return {
        'status': 'screened',
        'expected': str(expected),
        'votes': votes,
        'voiced_seconds': measured['voiced_seconds'],
        'start': measured['start'],
        'end': measured['end'],
    }
