#!/usr/bin/env python3
"""Resumable, content-addressed acoustic and ASR evidence; never invent human reviews."""
import argparse
import concurrent.futures
import hashlib
import json
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from runtime_data import read_words


ROOT = Path(__file__).resolve().parents[1]
PROFILE_VERSION = 'dc70-pyin60-praat60-world60-10ms-1'
ASR_VERSION = 'paraformer-unprompted-1'
ALIGNMENT_VERSION = 'fa-zh-after-identity-match-1'
PREPARED_ASR_VERSION = 'paraformer-dc70-rms010-trim30-1'


@lru_cache(maxsize=1)
def syllable_inventory():
    words = read_words()
    return frozenset(
        base.replace('ü', 'v') for word in words for base in word['pinyin_syllables']
    ) - {'r', 'm', 'n', 'ng', 'hm', 'hng'}


def unique_segmentation(spelling, inventory):
    @lru_cache(maxsize=None)
    def split(position):
        if position == len(spelling):
            return [()]
        options = []
        for end in range(position + 1, min(len(spelling), position + 6) + 1):
            base = spelling[position:end]
            if base not in inventory:
                continue
            for tail in split(end):
                options.append((base,) + tail)
                if len(options) > 1:
                    return options
        return options
    possibilities = split(0)
    return list(possibilities[0]) if len(possibilities) == 1 else None


def identity_encoding(recognition, expected_bases):
    if decoded_bases(recognition) != expected_bases:
        return None
    return 'hanzi_pinyin' if recognition.get('recognized_pinyin') else 'literal_pinyin'


def decoded_bases(recognition):
    """Interpret ASR output without supplying the expected answer."""
    from audit_native_readings import bases

    recognized = bases(recognition.get('recognized_pinyin', []))
    text = recognition.get('text', '').strip().lower().replace('ü', 'v')
    # Mixed Hanzi/Latin output is incomplete, not a matching Hanzi suffix.
    if recognized:
        return recognized if not re.search(r'[a-z]', text) else None
    if re.fullmatch(r"[a-zv]+(?:[ '\-]+[a-zv]+)*", text):
        parts = re.split(r"[ '\-]+", text)
        if len(parts) > 1:
            return parts if all(part in syllable_inventory() for part in parts) else None
        return unique_segmentation(text, syllable_inventory())
    return None


def resolved_recognition(raw, prepared):
    """Use complementary measurements, never override a phonetic disagreement."""
    raw_bases, prepared_bases = decoded_bases(raw), decoded_bases(prepared)
    if raw_bases and prepared_bases and raw_bases != prepared_bases:
        return None, 'raw and prepared ASR disagree on syllable identity'
    if prepared_bases:
        return prepared, None
    if raw_bases:
        return raw, None
    return None, 'neither raw nor prepared ASR resolved the syllable sequence'


def prepare_recognition(samples, sample_rate):
    import librosa
    import numpy as np
    from acoustic_analysis import prepare_signal

    signal = prepare_signal(samples, sample_rate)
    signal, bounds = librosa.effects.trim(signal, top_db=30, frame_length=512, hop_length=160)
    if not len(signal) or not np.max(np.abs(signal)):
        raise ValueError('No active audio for recognition')
    rms = float(np.sqrt(np.mean(signal ** 2)))
    gain = min(.1 / rms, .9 / float(np.max(np.abs(signal))))
    return np.ascontiguousarray(signal * gain, dtype=np.float32), {
        'trim_start_seconds': round(float(bounds[0] / sample_rate), 6),
        'trim_end_seconds': round(float(bounds[1] / sample_rate), 6),
        'gain': round(gain, 6),
        'sample_rate': sample_rate,
    }


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
    parser.add_argument('--phase', choices=['profiles', 'asr', 'prepared-asr', 'alignment'], required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or not 1 <= args.batch_size <= 32:
        parser.error('workers must be 1-4 and batch-size must be 1-32')
    candidates = json.loads(args.candidates.read_text(encoding='utf-8'))['candidates']
    paths = sorted({row['audio_path'] for row in candidates if row['kind'] in ('native', 'comparison')})
    if args.phase == 'profiles':
        paths = sorted(set(paths) | {
            row['source_segment']['audio_path'] for row in candidates if row.get('source_segment')
        })
    targets = {}
    if args.phase == 'alignment':
        recognition = read_jsonl(ROOT / '.audit/acoustic-asr.jsonl')
        prepared = read_jsonl(ROOT / '.audit/acoustic-prepared-asr.jsonl')
        for row in candidates:
            if row['kind'] != 'native' or len(row['pinyin_syllables']) < 2:
                continue
            text = ''.join(re.findall(r'[\u3400-\u9fff]', row['word']))
            if len(text) != len(row['pinyin_syllables']):
                continue
            expected = [base.replace('ü', 'v') for base in row['pinyin_syllables']]
            evidence = recognition.get(row['audio_path'], {})
            secondary = prepared.get(row['audio_path'])
            if secondary:
                if secondary.get('sha256') != row['sha256'] or secondary.get('evidence_version') != PREPARED_ASR_VERSION:
                    raise ValueError(f'Stale prepared ASR evidence: {row["audio_path"]}')
                evidence, _ = resolved_recognition(evidence, secondary)
                if evidence is None:
                    continue
            if evidence.get('sha256') != row['sha256'] or not identity_encoding(evidence, expected):
                continue
            previous = targets.get(row['audio_path'])
            if previous and previous['bases'] != expected:
                raise ValueError(f'Ambiguous phonetic segmentation: {row["audio_path"]}')
            targets[row['audio_path']] = {'text': text, 'bases': expected}
        paths = sorted(targets)
    output = args.output or ROOT / '.audit' / f'acoustic-{args.phase}.jsonl'
    version = {'profiles': PROFILE_VERSION, 'asr': ASR_VERSION, 'prepared-asr': PREPARED_ASR_VERSION,
               'alignment': ALIGNMENT_VERSION}[args.phase]
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
        elif pending and args.phase in ('asr', 'prepared-asr'):
            from funasr import AutoModel
            from audit_native_readings import recognized_pinyin
            import io
            import librosa
            import soundfile

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
                preparation = {}
                with tempfile.TemporaryDirectory(prefix='prepared-asr-', dir=output.parent) as temporary:
                    if args.phase == 'prepared-asr':
                        inputs = []
                        for relative in batch:
                            payload = (ROOT / relative).read_bytes()
                            if hashlib.sha256(payload).hexdigest() != hashes[relative]:
                                raise ValueError(f'Audio changed during preprocessing: {relative}')
                            samples, sample_rate = librosa.load(io.BytesIO(payload), sr=16000, mono=True)
                            signal, details = prepare_recognition(samples, sample_rate)
                            waveform = Path(temporary) / (Path(relative).stem + '.wav')
                            soundfile.write(waveform, signal, sample_rate, subtype='FLOAT')
                            inputs.append(str(waveform))
                            preparation[relative] = details
                    else:
                        inputs = [str(ROOT / relative) for relative in batch]
                    results = model.generate(
                        input=inputs, batch_size=args.batch_size, disable_pbar=True,
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
                        'evidence_version': version,
                        'model': 'paraformer-zh',
                        'text': transcript,
                        'recognized_pinyin': recognized_pinyin(transcript),
                        'timestamps_ms': [
                            [round(value + preparation.get(relative, {}).get('trim_start_seconds', 0) * 1000)
                             for value in timestamp]
                            for timestamp in result.get('timestamp', [])
                        ],
                        **({'preparation': preparation[relative]} if relative in preparation else {}),
                    }
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                stream.flush()
                done = min(offset + len(batch), len(pending))
                if done % 256 == 0 or done == len(pending):
                    print(f'{args.phase}: {done}/{len(pending)}', flush=True)
        elif pending:
            from funasr import AutoModel

            model = AutoModel(model='fa-zh', device='cpu', ncpu=args.workers, disable_update=True, disable_pbar=True)
            for index, relative in enumerate(pending, 1):
                target = targets[relative]
                digest = sha256(ROOT / relative)
                result = model.generate(
                    input=(str(ROOT / relative), ' '.join(target['text'])),
                    data_type=('sound', 'text'), disable_pbar=True,
                )
                if len(result) != 1 or digest != sha256(ROOT / relative):
                    raise ValueError(f'Invalid alignment result or changed audio: {relative}')
                row = {
                    'audio_path': relative, 'sha256': digest, 'evidence_version': ALIGNMENT_VERSION,
                    'conditioned_text': target['text'], 'phonetic_bases': target['bases'],
                    'timestamps_ms': result[0].get('timestamp', []),
                    'identity_was_checked_before_alignment': True,
                }
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                stream.flush()
                if index % 100 == 0 or index == len(pending):
                    print(f'alignment: {index}/{len(pending)}', flush=True)


if __name__ == '__main__':
    main()
