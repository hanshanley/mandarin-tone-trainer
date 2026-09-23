"""Label-independent acoustic features and alignment for pronunciation assessment."""
import hashlib
import io
import json
import re
from pathlib import Path

import librosa
import numpy as np
import parselmouth
from pypinyin import Style, lazy_pinyin
from opencc import OpenCC

from acoustic_analysis import prepare_signal


VERSION = 'ompal-acoustic-aligned-v1'
RATE = 16000
HOP = 160
INITIALS = ('', 'b', 'p', 'm', 'f', 'd', 't', 'n', 'l', 'g', 'k', 'h', 'j', 'q', 'x',
            'zh', 'ch', 'sh', 'r', 'z', 'c', 's', 'y', 'w')
FINALS = ('a', 'o', 'e', 'ai', 'ei', 'ao', 'ou', 'an', 'en', 'ang', 'eng', 'ong', 'i',
          'ia', 'ie', 'iao', 'iu', 'ian', 'in', 'iang', 'ing', 'iong', 'u', 'ua', 'uo',
          'uai', 'ui', 'uan', 'un', 'uang', 'ueng', 'v', 've', 'van', 'vn', 'er')
FEATURE_NAMES = (
    ['duration', 'utterance_duration', 'unit_length', 'relative_duration', 'position',
     'relative_rms', 'peak', 'rms', 'clipped_fraction', 'spectral_centroid',
     'spectral_flatness', 'zero_crossing', 'asr_match', 'asr_initial_match',
     'asr_final_match', 'asr_token_distance', 'asr_sentence_distance']
    + [f'{position}_tone_{tone}' for position in ('expected', 'previous', 'next') for tone in range(1, 6)]
    + [f'initial_{initial or "none"}' for initial in INITIALS]
    + [f'final_{final}' for final in FINALS]
    + [f'mfcc_{index}_{stat}' for index in range(13) for stat in ('mean', 'std')]
    + [f'{method}_{name}' for method in ('praat', 'yin') for name in (
        ['voiced_fraction', 'pitch_relative_mean', 'pitch_relative_std', 'pitch_range', 'pitch_start', 'pitch_end']
        + [f'contour_{i}' for i in range(12)]
        + [f'present_{i}' for i in range(12)]
    )]
    + ['tracker_agreement']
)


def transcript_characters(text):
    compact = re.sub(r'[\s，。！？、；：,.!?;:「」『』（）()“”"\'—…]', '', text)
    if not compact or not re.fullmatch(r'[\u3400-\u9fff]+', compact):
        raise ValueError('This judge currently requires a Hanzi-only reference transcript')
    return compact


def pinyin_tokens(text):
    values = lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True, errors='ignore')
    return [value.lower().replace('ü', 'v') for value in values if re.fullmatch(r'[A-Za-züv]+[1-5]', value)]


def pinyin_parts(value):
    match = re.fullmatch(r'([a-zv]+)([1-5])', value)
    if not match:
        raise ValueError(f'Invalid numbered pinyin: {value}')
    base, tone = match.groups()
    initial = next((part for part in ('zh', 'ch', 'sh', *INITIALS[1:]) if base.startswith(part)), '')
    return base, initial, base[len(initial):], int(tone)


def edit_alignment(expected, recognized):
    """Align unprompted ASR phones without treating forced alignment as identity."""
    size, other = len(expected), len(recognized)
    costs = np.zeros((size + 1, other + 1), dtype=int)
    costs[:, 0] = np.arange(size + 1)
    costs[0, :] = np.arange(other + 1)
    for i in range(1, size + 1):
        for j in range(1, other + 1):
            costs[i, j] = min(costs[i - 1, j] + 1, costs[i, j - 1] + 1,
                              costs[i - 1, j - 1] + (expected[i - 1] != recognized[j - 1]))
    matches = [None] * size
    i, j = size, other
    while i or j:
        if i and j and costs[i, j] == costs[i - 1, j - 1] + (expected[i - 1] != recognized[j - 1]):
            matches[i - 1] = recognized[j - 1]
            i, j = i - 1, j - 1
        elif i and costs[i, j] == costs[i - 1, j] + 1:
            i -= 1
        else:
            j -= 1
    return matches, float(costs[size, other]) / max(size, other, 1)


def normalized_edit(left, right):
    return edit_alignment(list(left), list(right))[1] if left or right else 0.0


def validate_alignment(timestamps, count, duration):
    if len(timestamps) != count:
        raise ValueError('Forced alignment does not cover the reference characters')
    previous = 0.0
    output = []
    for pair in timestamps:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('Malformed forced alignment')
        start, end = [float(value) / 1000 for value in pair]
        if not np.isfinite([start, end]).all() or start < previous - .01 or start < 0 or end <= start or end > duration + .02:
            raise ValueError('Non-monotonic or out-of-bounds forced alignment')
        output.append((start, min(end, duration)))
        previous = end
    return output


def utterance_features(payload):
    samples, _ = librosa.load(io.BytesIO(payload), sr=RATE, mono=True)
    if not .15 <= len(samples) / RATE <= 30:
        raise ValueError('Audio duration must be between 0.15 and 30 seconds')
    signal = prepare_signal(samples)
    if np.max(np.abs(signal)) < .0001:
        raise ValueError('No audible signal')
    rms = librosa.feature.rms(y=signal, frame_length=512, hop_length=HOP)[0]
    active = rms >= max(float(np.max(rms)) * .04, .00003)
    pitch = parselmouth.Sound(signal, RATE).to_pitch_ac(
        time_step=.01, pitch_floor=50, pitch_ceiling=600, voicing_threshold=.45,
    )
    praat = np.full(len(rms), np.nan)
    for time, frequency, strength in zip(pitch.xs(), pitch.selected_array['frequency'], pitch.selected_array['strength']):
        index = round(time * RATE / HOP)
        if 0 <= index < len(rms) and frequency >= 50 and strength >= .5:
            praat[index] = frequency
    yin = librosa.yin(signal, sr=RATE, fmin=50, fmax=600, frame_length=1024, hop_length=HOP)
    yin = yin[:len(rms)]
    if len(yin) != len(rms):
        raise ValueError('Pitch tracks do not align')
    praat[~active] = np.nan
    yin[~active] = np.nan
    usable = praat[np.isfinite(praat)]
    reference = float(np.median(usable)) if len(usable) else 200.0
    mfcc = librosa.feature.mfcc(y=signal, sr=RATE, n_mfcc=13, n_fft=512, hop_length=HOP)
    return {
        'samples': samples, 'signal': signal, 'rms': rms, 'praat': praat, 'yin': yin,
        'reference_pitch': reference, 'mfcc': mfcc,
        'centroid': librosa.feature.spectral_centroid(y=signal, sr=RATE, n_fft=512, hop_length=HOP)[0],
        'flatness': librosa.feature.spectral_flatness(y=signal, n_fft=512, hop_length=HOP)[0],
        'zcr': librosa.feature.zero_crossing_rate(signal, frame_length=512, hop_length=HOP)[0],
    }


def pitch_features(values, reference):
    voiced = np.flatnonzero(np.isfinite(values))
    if not len(voiced):
        return [0.0] * 30
    semitones = 12 * np.log2(values[voiced] / reference)
    summary = [
        len(voiced) / len(values), float(np.mean(semitones)), float(np.std(semitones)),
        float(np.quantile(semitones, .9) - np.quantile(semitones, .1)),
        float(np.median(semitones[:3])), float(np.median(semitones[-3:])),
    ]
    contour, present = [], []
    for indices in np.array_split(np.arange(len(values)), 12):
        block = values[indices]
        block = block[np.isfinite(block)]
        present.append(float(len(block) > 0))
        contour.append(float(np.median(12 * np.log2(block / reference))) if len(block) else 0.0)
    return summary + contour + present


def token_features(audio, intervals, phones, recognized, units):
    bases = [pinyin_parts(phone)[0] for phone in phones]
    recognized_bases = [pinyin_parts(phone)[0] for phone in recognized]
    matched, sentence_error = edit_alignment(bases, recognized_bases)
    duration = len(audio['signal']) / RATE
    mean_interval = np.mean([end - start for start, end in intervals])
    reference_rms = float(np.quantile(audio['rms'], .75))
    rows = []
    for first, last in units:
        if not 0 <= first < last <= len(phones):
            raise ValueError('Unit does not align with reference characters')
        start, end = intervals[first][0], intervals[last - 1][1]
        frame_start = max(0, round(start * RATE / HOP))
        frame_end = min(len(audio['rms']), round(end * RATE / HOP) + 1)
        samples = audio['samples'][round(start * RATE):round(end * RATE)]
        if frame_end - frame_start < 3 or not len(samples):
            raise ValueError('Unit has insufficient audio for acoustic measurement')
        frame = slice(frame_start, frame_end)
        pieces = [pinyin_parts(value) for value in phones[first:last]]
        expected_tones = [piece[3] for piece in pieces]
        candidate = [matched[index] or '' for index in range(first, last)]
        exact = float(np.mean([base == other for base, other in zip(bases[first:last], candidate)]))
        initial_matches, final_matches = [], []
        for piece, predicted in zip(pieces, candidate):
            recognized_part = pinyin_parts(predicted + '5') if predicted else ('', '', '', 5)
            initial_matches.append(float(bool(predicted) and piece[1] == recognized_part[1]))
            final_matches.append(float(bool(predicted) and piece[2] == recognized_part[2]))
        energy = float(np.sqrt(np.mean(samples ** 2)))
        values = [
            end - start, duration, last - first, (end - start) / (mean_interval * (last - first)),
            first / max(1, len(phones) - 1), energy / max(reference_rms, 1e-6),
            float(np.max(np.abs(samples))), energy, float(np.mean(np.abs(samples) >= .999)),
            float(np.mean(audio['centroid'][frame])) / (RATE / 2),
            float(np.mean(audio['flatness'][frame])), float(np.mean(audio['zcr'][frame])),
            exact, float(np.mean(initial_matches)), float(np.mean(final_matches)),
            normalized_edit(''.join(bases[first:last]), ''.join(candidate)), sentence_error,
        ]
        for tones in (expected_tones, [pinyin_parts(phones[first - 1])[3]] if first else [],
                      [pinyin_parts(phones[last])[3]] if last < len(phones) else []):
            values.extend([tones.count(tone) / max(len(tones), 1) for tone in range(1, 6)])
        values.extend([sum(piece[1] == initial for piece in pieces) / len(pieces) for initial in INITIALS])
        values.extend([sum(piece[2] == final for piece in pieces) / len(pieces) for final in FINALS])
        for index in range(13):
            values += [float(np.mean(audio['mfcc'][index, frame])), float(np.std(audio['mfcc'][index, frame]))]
        for method in ('praat', 'yin'):
            values += pitch_features(audio[method][frame], audio['reference_pitch'])
        left, right = audio['praat'][frame], audio['yin'][frame]
        valid = np.isfinite(left) & np.isfinite(right)
        values += [float(np.mean(np.abs(12 * np.log2(left[valid] / right[valid])) <= 2)) if valid.any() else 0.0]
        if len(values) != len(FEATURE_NAMES) or not np.isfinite(values).all():
            raise ValueError('Non-finite or malformed acoustic feature vector')
        rows.append({
            'features': values, 'start': start, 'end': end,
            'character_start': first, 'character_end': last,
            'expected_pinyin': phones[first:last], 'expected_tones': expected_tones,
            'voiced_frames': int(np.isfinite(left).sum()),
        })
    return rows


def analyze(path, text, recognition_model, alignment_model, expected_pinyin=None, units=None):
    path = Path(path)
    payload = path.read_bytes()
    text = transcript_characters(text)
    phones = expected_pinyin if expected_pinyin is not None else pinyin_tokens(text)
    if len(phones) != len(text):
        raise ValueError('Reference text and pinyin syllables do not align')
    for phone in phones:
        pinyin_parts(phone)
    audio = utterance_features(payload)
    # Recognition sees the waveform, never the expected transcript.
    signal = audio['signal']
    gain = min(.1 / max(float(np.sqrt(np.mean(signal ** 2))), 1e-8),
               .9 / float(np.max(np.abs(signal))))
    recognized = recognition_model.generate(
        input=np.ascontiguousarray(signal * gain, dtype=np.float32), fs=RATE, disable_pbar=True,
    )[0]
    # Alignment locates reference units; its success is not pronunciation gold.
    alignment_text = OpenCC('t2s').convert(text)
    if len(alignment_text) != len(text):
        raise ValueError('Traditional-to-simplified conversion changed character alignment')
    aligned = alignment_model.generate(
        input=(np.asarray(audio['samples'], dtype=np.float32), ' '.join(alignment_text)),
        data_type=('sound', 'text'), disable_pbar=True,
    )[0]
    intervals = validate_alignment(aligned.get('timestamp', []), len(text), len(signal) / RATE)
    if path.read_bytes() != payload:
        raise ValueError('Audio changed during analysis')
    units = units if units is not None else [(index, index + 1) for index in range(len(text))]
    return {
        'audio_sha256': hashlib.sha256(payload).hexdigest(),
        'feature_version': VERSION,
        'reference_text': text,
        'recognition_text': recognized.get('text', ''),
        'expected_pinyin': phones,
        'units': token_features(audio, intervals, phones, pinyin_tokens(recognized.get('text', '')), units),
    }


def load_engines():
    from funasr import AutoModel

    options = {'device': 'cpu', 'ncpu': 2, 'disable_update': True, 'disable_pbar': True}
    return AutoModel(model='paraformer-zh', **options), AutoModel(model='fa-zh', **options)
