#!/usr/bin/env python3
"""Independent ASR checks for unresolved single-syllable comparison candidates."""
import argparse
import hashlib
import json
from pathlib import Path

import librosa
import numpy as np

from audit_native_readings import recognized_pinyin
from collect_acoustic_evidence import decoded_bases, prepare_recognition, read_jsonl
from native_tone_labels import safe_audio, verify_inventory
from runtime_data import ROOT


VERSION = 'whisper-small-dual-unprompted-v1'
OUTPUT = ROOT / '.audit/comparison-independent-identity.jsonl'


class StableWhisperFeatures:
    """Use the installed Whisper configuration with a finite, non-BLAS mel contraction."""
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    def __call__(self, waveform, padding=160, chunk_length=None):
        original = self.original
        if chunk_length is not None:
            original.n_samples = chunk_length * original.sampling_rate
            original.nb_max_frames = original.n_samples // original.hop_length
        waveform = np.asarray(waveform, dtype=np.float32)
        if padding:
            waveform = np.pad(waveform, (0, padding))
        window = np.hanning(original.n_fft + 1)[:-1].astype(np.float32)
        spectrum = original.stft(waveform, original.n_fft, original.hop_length,
                                 window=window, return_complex=True).astype(np.complex64)
        power = np.abs(spectrum[..., :-1]) ** 2
        mel = np.einsum('ij,jk->ik', original.mel_filters, power, optimize=False)
        logarithm = np.log10(np.maximum(mel, 1e-10))
        features = (np.maximum(logarithm, logarithm.max() - 8.) + 4.) / 4.
        if not np.isfinite(features).all():
            raise ValueError('Non-finite independent recognition features')
        return features


def no_primary_contradiction(raw, prepared, expected):
    return all(not (bases and len(bases) == 1 and bases != [expected])
               for bases in (decoded_bases(raw), decoded_bases(prepared)))


def main():
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model

    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--outcomes', type=Path, default=ROOT / '.audit/mixed-audio-cross-fit.json')
    parser.add_argument('--gap-keys', type=Path, default=ROOT / '.audit/comparison-coverage-gaps-for-plan.json')
    args = parser.parse_args()
    inventory = verify_inventory(json.loads(args.inventory.read_text()))
    outcomes = json.loads(args.outcomes.read_text())['outcomes']
    keys = {row['key'] for row in json.loads(args.gap_keys.read_text())}
    raw = read_jsonl(ROOT / '.audit/acoustic-asr.jsonl')
    prepared = read_jsonl(ROOT / '.audit/acoustic-prepared-asr.jsonl')
    candidates = {}
    for row in inventory['records']:
        if row['quarantined'] or len(row['syllables']) != 1:
            continue
        key = row['syllables'][0] + row['weak_training_label']
        result = outcomes.get(row['id'], {})
        if key not in keys or result.get('reason') != 'identity_unresolved':
            continue
        if (result.get('predicted_tone') != row['weak_training_label']
                or result.get('expected_probability', 0) < .95 or result.get('margin', 0) < .25):
            continue
        a, b = raw.get(row['audio_path']), prepared.get(row['audio_path'])
        if not a or not b or a.get('sha256') != b.get('sha256') or a.get('sha256') != row['sha256']:
            continue
        if no_primary_contradiction(a, b, row['syllables'][0]):
            candidates[row['audio_path']] = row
    model_dir = Path(download_model('small', local_files_only=True))
    model_hash = hashlib.sha256((model_dir / 'model.bin').read_bytes()).hexdigest()
    done = read_jsonl(OUTPUT)
    pending = [row for path, row in sorted(candidates.items())
               if done.get(path, {}).get('sha256') != row['sha256']
               or done.get(path, {}).get('version') != VERSION or done.get(path, {}).get('model_sha256') != model_hash]
    print(f'Independent unresolved-syllable checks: {len(pending)} pending / {len(candidates)} candidates', flush=True)
    if not pending:
        return
    model = WhisperModel(str(model_dir), device='cpu', compute_type='int8', cpu_threads=2)
    model.feature_extractor = StableWhisperFeatures(model.feature_extractor)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open('a', encoding='utf-8') as output:
        for index, row in enumerate(pending, 1):
            path = safe_audio(row['audio_path'])
            samples, rate = librosa.load(path, sr=16000, mono=True)
            clean, _ = prepare_recognition(samples, rate)
            inputs = [('raw', samples), ('prepared', np.pad(clean, (4000, 4000)))]
            checks = []
            for name, signal in inputs:
                segments, _ = model.transcribe(
                    np.ascontiguousarray(signal, dtype=np.float32), language='zh', beam_size=5, temperature=0,
                    condition_on_previous_text=False, without_timestamps=True,
                )
                pieces = list(segments)
                text = ''.join(piece.text for piece in pieces).strip()
                decoded = decoded_bases({'text': text, 'recognized_pinyin': recognized_pinyin(text)})
                checks.append({
                    'input': name, 'audio_sha256': row['sha256'], 'transcript': text, 'decoded_bases': decoded,
                    'minimum_log_probability': min((piece.avg_logprob for piece in pieces), default=-100.),
                })
            if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('Audio changed during independent recognition')
            result = {
                'audio_path': row['audio_path'], 'sha256': row['sha256'], 'version': VERSION,
                'model_sha256': model_hash, 'checks': checks,
                'identity_supported': all(check['decoded_bases'] == row['syllables']
                                          and check['minimum_log_probability'] >= -1. for check in checks),
            }
            output.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
            output.flush()
            if index % 25 == 0 or index == len(pending):
                print(f'Independent identity: {index}/{len(pending)}', flush=True)


if __name__ == '__main__':
    main()
