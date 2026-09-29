#!/usr/bin/env python3
"""Map whole, silence-separated publisher drill utterances to exact book entries."""
import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / 'audio/glossika/example_index.json'
SCAN = ROOT / '.audit/glossika-example-regions.json'
INTROS = ROOT / '.audit/glossika-example-intros.jsonl'
RECOGNITION = ROOT / '.audit/glossika-example-recognition.jsonl'
MAPPINGS = ROOT / 'data/glossika_example_audio.json'
PRACTICE = ROOT / 'data/glossika_practice.json'
METHOD = 'publisher-isolated-drills-v1'
SCAN_VERSION = 'rms5ms-db35-40-45-50-gap250ms-fine-intro-2'
INTRO_VERSION = 'whisper-small-unprompted-english-header-1'
ASR_VERSION = 'paraformer-whole-drill-regions-1'


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def read_lines(path, key):
    if not path.exists():
        return {}
    return {row[key]: row for row in (json.loads(line) for line in path.read_text().splitlines())}


def source_index():
    index = read_json(INDEX)
    summary = read_json(ROOT / 'data/glossika_example_summary.json')
    actual = digest(INDEX.read_bytes())
    if not index.get('extraction_complete') or actual != summary.get('index_sha256'):
        raise ValueError('Individual example extraction is incomplete or differs from its source summary')
    return index, actual, summary


def regions_for(rms, hop, rate, db, minimum_gap=.25):
    import numpy as np

    threshold = max(float(np.max(rms)) * 10 ** (-db / 20), .00005)
    active = rms > threshold
    starts = np.flatnonzero(np.diff(np.r_[False, active].astype(int)) == 1)
    ends = np.flatnonzero(np.diff(np.r_[active, False].astype(int)) == -1) + 1
    groups = []
    for start, end in zip(starts, ends):
        if groups and (start - groups[-1][1]) * hop / rate < minimum_gap:
            groups[-1][1] = int(end)
        else:
            groups.append([int(start), int(end)])
    return [[start * hop, end * hop] for start, end in groups]


def scan():
    import numpy as np
    import soundfile as sf

    index, index_hash, _ = source_index()
    result = []
    for lesson in index['lessons']:
        path = ROOT / lesson['audio_path']
        if digest(path.read_bytes()) != lesson['audio_sha256']:
            raise ValueError(f'Changed source lesson: {path}')
        samples, rate = sf.read(path, dtype='float32', always_2d=True)
        if not len(samples) or not np.isfinite(samples).all():
            raise ValueError(f'Invalid lesson samples: {path}')
        hop = round(rate * .005)
        frames = len(samples) // hop
        rms = np.sqrt(np.mean(samples[:frames * hop].reshape(frames, hop, -1).astype('float64') ** 2, axis=(1, 2)))
        result.append({
            'lesson_id': lesson['lesson_id'], 'audio_path': lesson['audio_path'],
            'sha256': lesson['audio_sha256'], 'sample_rate': rate,
            'source_frames': len(samples), 'channels': samples.shape[1], 'expected_items': len(lesson['items']),
            'rms_peak': float(rms.max()), 'rms_hop': hop,
            'regions': {str(db): regions_for(rms, hop, rate, db) for db in (35, 40, 45, 50)},
            'intro_regions': regions_for(rms[:round(14 * rate / hop)], hop, rate, 40, minimum_gap=.1),
        })
    write_json(SCAN, {'version': SCAN_VERSION, 'index_sha256': index_hash, 'lessons': result})
    print(json.dumps({'lessons_scanned': len(result), 'index_sha256': index_hash}), flush=True)


ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
        'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen',
        'eighteen', 'nineteen']
NUMBERS = {name: number for number, name in enumerate(ONES)}
NUMBERS.update({'twenty': 20, 'thirty': 30})
NUMBERS.update({f'{tens} {ones}': value + number for tens, value in [('twenty', 20), ('thirty', 30)]
                for number, ones in enumerate(ONES[1:10], 1)})


def header_match(lesson_id, words):
    text = ''
    endpoints = []
    for word in words:
        text += word['word'].lower().replace('-', ' ')
        endpoints.append((len(text), word['end']))
    if not re.search(r'chinese.*mandarin.*training', text, re.S):
        return None
    number_pattern = r'\d+|' + '|'.join(re.escape(word) for word in sorted(NUMBERS, key=len, reverse=True))
    if lesson_id.startswith('vowel-part-'):
        match = re.search(r'\bpart\s+(' + number_pattern + r')\b', text)
        expected = [int(lesson_id.rsplit('-', 1)[1])]
        matches = [match] if match else []
        values = [match[1]] if match else []
    else:
        anchor = re.search(r'tone\s+training', text)
        if not anchor:
            return None
        expected = [int(tone) for tone in lesson_id.split('-')[1]]
        matches = list(re.finditer(r'\b(' + number_pattern + r')\b', text[anchor.end():]))[:len(expected)]
        values = [match[1] for match in matches]
    numbers = [int(value) if value.isdigit() else NUMBERS[value] for value in values]
    if len(numbers) != len(expected):
        return None
    end = matches[-1].end() + (anchor.end() if not lesson_id.startswith('vowel-part-') else 0)
    timestamp = next(time for offset, time in endpoints if offset >= end)
    return {'numbers': numbers, 'expected_numbers': expected, 'label_matches': numbers == expected,
            'header_end_seconds': timestamp, 'transcript': text.strip()}


def collect_intros(limit=None):
    import librosa
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    from verify_comparison_identity import StableWhisperFeatures

    scan_data = read_json(SCAN)
    cached = read_lines(INTROS, 'lesson_id')
    refreshed = False
    for row in cached.values():
        header = header_match(row['lesson_id'], row['words'])
        if header != row.get('header'):
            row['header'] = header
            refreshed = True
    if refreshed:
        with INTROS.open('w', encoding='utf-8') as output:
            for row in cached.values():
                output.write(json.dumps(row, ensure_ascii=False) + '\n')
    pending = [row for row in scan_data['lessons'] if cached.get(row['lesson_id'], {}).get('sha256') != row['sha256']
               or cached.get(row['lesson_id'], {}).get('version') != INTRO_VERSION]
    if limit:
        pending = pending[:limit]
    if not pending:
        print('All lesson introductions have current recognition evidence.')
        return
    model_dir = Path(download_model('small', local_files_only=True))
    model = WhisperModel(str(model_dir), device='cpu', compute_type='int8', cpu_threads=2)
    model.feature_extractor = StableWhisperFeatures(model.feature_extractor)
    model_hash = digest((model_dir / 'model.bin').read_bytes())
    INTROS.parent.mkdir(parents=True, exist_ok=True)
    with INTROS.open('a', encoding='utf-8') as output:
        for number, row in enumerate(pending, 1):
            samples, _ = librosa.load(ROOT / row['audio_path'], sr=16000, mono=True, duration=14)
            segments, _ = model.transcribe(
                np.ascontiguousarray(samples, dtype=np.float32), language='en', beam_size=5,
                condition_on_previous_text=False, word_timestamps=True, temperature=0,
            )
            segments = list(segments)
            words = [{'word': word.word, 'start': word.start, 'end': word.end}
                     for segment in segments for word in segment.words or []]
            match = header_match(row['lesson_id'], words)
            record = {'lesson_id': row['lesson_id'], 'sha256': row['sha256'], 'version': INTRO_VERSION,
                      'model_sha256': model_hash, 'words': words, 'header': match,
                      'minimum_log_probability': min((segment.avg_logprob for segment in segments), default=-100)}
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
            output.flush()
            print(f"Intro {number}/{len(pending)} {row['lesson_id']}: {'matched' if match else 'UNRESOLVED'}", flush=True)


def cadence_groups(regions, rate):
    groups = []
    for number, pair in enumerate(regions):
        if groups and (pair[0] - regions[groups[-1][-1]][1]) / rate < .65:
            groups[-1].append(number)
        else:
            groups.append([number])
    return groups


def body_regions(scan_row, intro):
    if not intro or intro.get('sha256') != scan_row['sha256'] or intro.get('version') != INTRO_VERSION or not intro.get('header'):
        raise ValueError('No verified original lesson introduction')
    rate = scan_row['sample_rate']
    end = intro['header']['header_end_seconds']
    regions = scan_row['regions']['40']
    if scan_row['lesson_id'].startswith('vowel-part-'):
        # A vowel demonstration follows the English title, before the five-tone rows.
        groups = cadence_groups(regions, rate)
        first = next((group[0] for group in groups if len(group) >= 4
                      and 3 < regions[group[0]][0] / rate < 14
                      and all((regions[number][1] - regions[number][0]) / rate < 2 for number in group)), None)
        if first is None:
            raise ValueError('No complete isolated five-tone cadence follows the verified introduction')
        return regions, first
    candidates = [(abs(finish / rate - end), index) for index, (_, finish) in enumerate(regions)
                  if 1 <= finish / rate <= 12 and index + 1 < len(regions)
                  and regions[index + 1][0] / rate > end - .1]
    if not candidates:
        raise ValueError('No silence boundary after verified introduction')
    delta, last_intro = min(candidates)
    if delta > .5:
        fine = scan_row.get('intro_regions', [])
        options = [(abs(finish / rate - end), index) for index, (_, finish) in enumerate(fine)
                   if 1 <= finish / rate <= 12 and index + 1 < len(fine)
                   and fine[index + 1][0] / rate > end - .1]
        if not options:
            raise ValueError('Recognized introduction does not end at an isolated speech boundary')
        delta, position = min(options)
        if delta > .5:
            raise ValueError('Recognized introduction does not end at an isolated speech boundary')
        finish = fine[position][1]
        first_body = fine[position + 1][0]
        remaining = [[max(start, first_body), stop] for start, stop in regions if stop > first_body]
        if not remaining:
            raise ValueError('Verified introduction has no following standalone drills')
        return [[regions[0][0], finish], *remaining], 1
    return regions, last_intro + 1


def interval_for(scan_row, regions, index):
    rate = scan_row['sample_rate']
    start, end = regions[index]
    def conservative_bounds(pair):
        matches = [pair, *(
            other for alternative in scan_row['regions'].values() for other in alternative
            if abs(other[0] - pair[0]) / rate <= .15 and abs(other[1] - pair[1]) / rate <= .15
        )]
        return min(other[0] for other in matches), max(other[1] for other in matches)

    agreement = sum(any(abs(pair[0] - start) / rate <= .15 and abs(pair[1] - end) / rate <= .15
                        for pair in alternative) for alternative in scan_row['regions'].values())
    if agreement < 2:
        raise ValueError('Utterance boundary is unstable across signal thresholds')
    start, end = conservative_bounds((start, end))
    previous = conservative_bounds(regions[index - 1])[1] if index else 0
    following = conservative_bounds(regions[index + 1])[0] if index + 1 < len(regions) else scan_row['source_frames']
    clip_start = max(previous + round(rate * .08), start - round(rate * .24))
    clip_end = min(following - round(rate * .08), end + round(rate * .24))
    if (start - clip_start) / rate < .08 or (clip_end - end) / rate < .08:
        raise ValueError('Insufficient original silence around a complete example')
    return {'sample_rate': rate, 'start_sample': clip_start, 'end_sample': clip_end,
            'speech_start_sample': start, 'speech_end_sample': end,
            'source_frames': scan_row['source_frames'], 'channels': scan_row['channels']}


def collect_recognition(limit=None):
    import math
    import tempfile
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from scipy.io import wavfile
    from funasr import AutoModel
    from audit_native_readings import recognized_pinyin
    from collect_acoustic_evidence import decoded_bases

    scanned = read_json(SCAN)
    intros = read_lines(INTROS, 'lesson_id')
    cached = read_lines(RECOGNITION, 'region_id')
    model = None
    done = 0
    RECOGNITION.parent.mkdir(parents=True, exist_ok=True)
    with RECOGNITION.open('a', encoding='utf-8') as output:
        for row in scanned['lessons']:
            try:
                regions, offset = body_regions(row, intros.get(row['lesson_id']))
            except ValueError as error:
                print(f"Recognition held: {row['lesson_id']}: {error}", flush=True)
                continue
            jobs = []
            first_region = 0 if not row['lesson_id'].startswith('vowel-part-') else offset
            for number in range(first_region, len(regions)):
                if (regions[number][1] - regions[number][0]) / row['sample_rate'] > 3:
                    continue
                identifier = f"{row['lesson_id']}-region-{number:04d}"
                try:
                    segment = interval_for(row, regions, number)
                except ValueError:
                    continue
                old = cached.get(identifier, {})
                if old.get('version') == ASR_VERSION and old.get('sha256') == row['sha256'] and old.get('segment') == segment:
                    continue
                jobs.append((identifier, number, segment))
            if not jobs:
                continue
            if limit:
                jobs = jobs[:max(0, limit - done)]
            if not jobs:
                break
            if model is None:
                model = AutoModel(model='paraformer-zh', device='cpu', ncpu=2, disable_update=True, disable_pbar=True)
            samples, rate = sf.read(ROOT / row['audio_path'], dtype='float32', always_2d=True)
            divisor = math.gcd(rate, 16000)
            mono = resample_poly(samples.mean(axis=1), 16000 // divisor, rate // divisor)
            for offset_in_jobs in range(0, len(jobs), 16):
                batch = jobs[offset_in_jobs:offset_in_jobs + 16]
                with tempfile.TemporaryDirectory(prefix='glossika-asr-', dir=ROOT / '.audit') as directory:
                    files = []
                    for identifier, _, segment in batch:
                        start = round(segment['start_sample'] * 16000 / rate)
                        end = round(segment['end_sample'] * 16000 / rate)
                        clip = mono[start:end]
                        if not len(clip) or not np.isfinite(clip).all():
                            raise ValueError('Invalid standalone region for recognition')
                        filename = Path(directory) / f'{identifier}.wav'
                        wavfile.write(filename, 16000, np.ascontiguousarray(clip, dtype=np.float32))
                        files.append(str(filename))
                    results = model.generate(input=files, batch_size=len(files), disable_pbar=True)
                by_key = {result.get('key'): result for result in results}
                if set(by_key) != {job[0] for job in batch}:
                    raise ValueError('Recognition output does not match the requested regions')
                for identifier, number, segment in batch:
                    result = by_key[identifier]
                    text = result.get('text', '').strip()
                    recognition = {'text': text, 'recognized_pinyin': recognized_pinyin(text)}
                    record = {'region_id': identifier, 'lesson_id': row['lesson_id'], 'region_index': number,
                              'sha256': row['sha256'], 'version': ASR_VERSION, 'segment': segment,
                              'transcript': text, 'decoded_bases': decoded_bases(recognition),
                              'timestamps_ms': result.get('timestamp', [])}
                    output.write(json.dumps(record, ensure_ascii=False) + '\n')
                    done += 1
                output.flush()
            print(f"Recognized {row['lesson_id']}: {len(jobs)} regions, {done} newly checked", flush=True)


def phonetic_match(expected, observed):
    if isinstance(observed, set):
        return len(expected) == 1 and expected[0] in observed
    if observed == expected:
        return True
    return bool(len(expected) == 1 and observed and all(base == expected[0] for base in observed))


def alignment_cost(expected, observed):
    if phonetic_match(expected, observed):
        return 0
    if not observed:
        return 2
    return 6


def align_items(expected, observed):
    """Retain only one-to-one positions shared by every minimum-cost alignment."""
    rows, columns = len(expected), len(observed)
    gap = 4
    forward = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(rows + 1):
        forward[i][0] = i * gap
    for j in range(columns + 1):
        forward[0][j] = j * gap
    for i in range(1, rows + 1):
        for j in range(1, columns + 1):
            forward[i][j] = min(
                forward[i - 1][j - 1] + alignment_cost(expected[i - 1], observed[j - 1]),
                forward[i - 1][j] + gap, forward[i][j - 1] + gap,
            )
    backward = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(rows, -1, -1):
        backward[i][columns] = (rows - i) * gap
    for j in range(columns, -1, -1):
        backward[rows][j] = (columns - j) * gap
    for i in range(rows - 1, -1, -1):
        for j in range(columns - 1, -1, -1):
            backward[i][j] = min(
                alignment_cost(expected[i], observed[j]) + backward[i + 1][j + 1],
                gap + backward[i + 1][j], gap + backward[i][j + 1],
            )
    optimum = forward[rows][columns]
    result = []
    for i in range(rows):
        positions = [j for j in range(columns)
                     if forward[i][j] + alignment_cost(expected[i], observed[j]) + backward[i + 1][j + 1] == optimum]
        deletable = any(forward[i][j] + gap + backward[i + 1][j] == optimum for j in range(columns + 1))
        result.append(positions[0] if len(positions) == 1 and not deletable else None)
    return result


def anchored_positions(items, observed, positions):
    confirmed = {
        i for i, position in enumerate(positions)
        if position is not None and phonetic_match(items[i]['pinyin_syllables'], observed[position])
    }
    exact_order = (len(items) == len(observed) and positions == list(range(len(items)))
                   and len(confirmed) >= max(1, (3 * len(items) + 3) // 4))
    accepted = {}
    for i, position in enumerate(positions):
        if position is None:
            continue
        if i in confirmed:
            accepted[i] = 'unprompted_phonetic_match'
            continue
        if exact_order:
            accepted[i] = 'publisher_exact_count_order_with_phonetic_anchors'
            continue
        if items[i]['kind'] == 'syllable_drill':
            family = [number for number, item in enumerate(items)
                      if item['pinyin_syllables'] == items[i]['pinyin_syllables']]
            mapped = [positions[number] for number in family]
            if (len(family) == 5 and all(number is not None for number in mapped)
                    and mapped == list(range(mapped[0], mapped[0] + 5))
                    and any(number in confirmed for number in family)):
                accepted[i] = 'publisher_five_tone_family_order'
                continue
        left = next((number for number in range(i - 1, max(-1, i - 5), -1) if number in confirmed), None)
        right = next((number for number in range(i + 1, min(len(items), i + 5)) if number in confirmed), None)
        if left is not None and right is not None:
            block = positions[left:right + 1]
            if all(number is not None for number in block) and block == list(range(block[0], block[0] + len(block))):
                accepted[i] = 'publisher_order_between_phonetic_anchors'
    return accepted


def align_syllable_groups(items, speech, regions, offset, rate):
    if len(items) % 5:
        raise ValueError('Source syllable rows do not contain five tone columns')
    rows = []
    for start in range(0, len(items), 5):
        row = items[start:start + 5]
        if ([item['lexical_tones'] for item in row] != [[1], [2], [3], [4], [0]]
                or any(item['pinyin_syllables'] != row[0]['pinyin_syllables'] for item in row)):
            raise ValueError('Source five-tone row is inconsistent')
        rows.append({'kind': 'word_drill', 'pinyin_syllables': row[0]['pinyin_syllables']})
    groups = []
    for group in cadence_groups(regions, rate):
        if group[0] < offset:
            continue
        if len(group) % 5 == 0:
            groups.extend(group[start:start + 5] for start in range(0, len(group), 5))
        else:
            groups.append(group)
    recognized_groups = []
    for group in groups:
        bases = set()
        for number in group:
            recognized = speech[number - offset]
            if recognized and len(set(recognized)) == 1:
                bases.add(recognized[0])
        recognized_groups.append(bases)
    ordered_grid = len(groups) == len(rows) and all(4 <= len(group) <= 6 for group in groups)
    if ordered_grid:
        positions = list(range(len(rows)))
        accepted = {number: 'publisher_five_column_table_and_recorded_cadence' for number in positions}
    else:
        positions = align_items([row['pinyin_syllables'] for row in rows], recognized_groups)
        accepted = anchored_positions(rows, recognized_groups, positions)
    item_positions = [None] * len(items)
    item_support = {}
    for row, group_number in enumerate(positions):
        if group_number is None or row not in accepted or len(groups[group_number]) != 5:
            continue
        for tone, region in enumerate(groups[group_number]):
            item = row * 5 + tone
            item_positions[item] = region - offset
            item_support[item] = accepted[row]
    return item_positions, item_support


def publish_mappings():
    index, index_hash, _ = source_index()
    scanned = read_json(SCAN)
    if scanned['index_sha256'] != index_hash or scanned['version'] != SCAN_VERSION:
        raise ValueError('Audio-region scan does not match the current source index')
    by_lesson = {row['lesson_id']: row for row in scanned['lessons']}
    intros = read_lines(INTROS, 'lesson_id')
    recognized = read_lines(RECOGNITION, 'region_id')
    lessons = []
    for lesson in index['lessons']:
        row = by_lesson[lesson['lesson_id']]
        intro = intros.get(lesson['lesson_id'])
        records = []
        try:
            regions, offset = body_regions(row, intro)
        except ValueError as error:
            lessons.append({'lesson_id': lesson['lesson_id'], 'sha256': row['sha256'],
                            'intro_verified': False, 'items': [
                                {'item_id': item['id'], 'segment': None, 'status': 'unresolved', 'reason': str(error)}
                                for item in lesson['items']]})
            continue
        if lesson['items'][0]['kind'] == 'word_drill' and len(lesson['items']) >= 2:
            for earlier in range(max(0, offset - 2), offset):
                pair = [recognized.get(f"{row['lesson_id']}-region-{number:04d}") for number in (earlier, earlier + 1)]
                if all(result and phonetic_match(item['pinyin_syllables'], result['decoded_bases'])
                       for item, result in zip(lesson['items'][:2], pair)):
                    offset = earlier
                    break
        speech = []
        for number in range(offset, len(regions)):
            identifier = f"{row['lesson_id']}-region-{number:04d}"
            recognition = recognized.get(identifier)
            if recognition and (recognition.get('sha256') != row['sha256'] or recognition.get('version') != ASR_VERSION):
                raise ValueError('Stale standalone-utterance recognition evidence')
            speech.append(recognition.get('decoded_bases') if recognition else None)
        if lesson['items'][0]['kind'] == 'syllable_drill':
            positions, accepted = align_syllable_groups(
                lesson['items'], speech, regions, offset, row['sample_rate'],
            )
        else:
            positions = align_items([item['pinyin_syllables'] for item in lesson['items']], speech)
            accepted = anchored_positions(lesson['items'], speech, positions)
        for number, item in enumerate(lesson['items']):
            position = positions[number]
            reason = None
            segment = None
            if position is None:
                reason = 'No unique correspondence between the printed item and recorded utterances'
            elif number not in accepted:
                reason = 'Individual example order lacks sufficient independent phonetic anchors'
            else:
                try:
                    segment = interval_for(row, regions, offset + position)
                    recognition = recognized.get(f"{row['lesson_id']}-region-{offset + position:04d}")
                    if recognition and recognition['segment'] != segment:
                        raise ValueError('Recognition was measured against different audio boundaries')
                except ValueError as error:
                    reason = str(error)
                    segment = None
            records.append({
                'item_id': item['id'], 'status': 'mapped' if segment else 'unresolved',
                'region_index': offset + position if position is not None else None,
                'segment': segment, 'phonetic_support': accepted.get(number), 'reason': reason,
            })
        lessons.append({
            'lesson_id': lesson['lesson_id'], 'sha256': row['sha256'],
            'intro_verified': True, 'intro_numbers': intro['header']['numbers'],
            'intro_label_matches': intro['header']['label_matches'],
            'intro_end_seconds': intro['header']['header_end_seconds'],
            'intro_evidence_sha256': digest(json.dumps(intro, ensure_ascii=False, sort_keys=True).encode()),
            'printed_items': len(lesson['items']), 'recorded_regions': len(speech),
            'independently_recognized_regions': sum(bool(bases) for bases in speech),
            'items': records,
        })
    counts = Counter(item['status'] for lesson in lessons for item in lesson['items'])
    output = {'version': 1, 'method': METHOD, 'book_sha256': index['book_sha256'],
              'index_sha256': index_hash, 'pipeline_sha256': digest(Path(__file__).read_bytes()),
              'scan_version': SCAN_VERSION, 'intro_version': INTRO_VERSION, 'recognition_version': ASR_VERSION,
              'source_files_changed': False, 'source_labels_changed': False,
              'independent_pronunciation_certification': False, 'counts': dict(counts), 'lessons': lessons}
    write_json(MAPPINGS, output)
    print(json.dumps({'mapping_counts': dict(counts), 'lessons': len(lessons)}, indent=2))
    restore_practice()


def restore_practice():
    from build_hsk_data import pattern, sandhi_surface

    index, index_hash, _ = source_index()
    boundaries = read_json(MAPPINGS)
    if boundaries.get('version') != 1 or boundaries.get('method') != METHOD or boundaries['index_sha256'] != index_hash:
        raise ValueError('Frozen utterance mappings do not match the original example index')
    if boundaries['book_sha256'] != index['book_sha256']:
        raise ValueError('Frozen mappings reference a different book')
    mapped_lessons = {lesson['lesson_id']: lesson for lesson in boundaries['lessons']}
    if set(mapped_lessons) != {lesson['lesson_id'] for lesson in index['lessons']}:
        raise ValueError('Frozen mapping inventory is incomplete')
    examples = []
    for lesson in index['lessons']:
        if digest((ROOT / lesson['audio_path']).read_bytes()) != lesson['audio_sha256']:
            raise ValueError(f"Changed original lesson: {lesson['audio_path']}")
        mapped = mapped_lessons[lesson['lesson_id']]
        by_id = {item['item_id']: item for item in mapped['items']}
        if set(by_id) != {item['id'] for item in lesson['items']} or len(by_id) != len(mapped['items']):
            raise ValueError('Frozen mapping rows differ from the source book')
        for item in lesson['items']:
            match = by_id[item['id']]
            surface, tags, _ = sandhi_surface(item['word'], item['lexical_tones'])
            reason = match['reason']
            if item.get('source_pattern_matches_lexical') is False:
                reason = 'The printed pronunciation and lesson tone heading disagree'
            elif not mapped.get('intro_label_matches', True):
                reason = 'The spoken lesson announcement and printed section disagree; the item remains listening-only'
            elif not all(re.fullmatch(r'[a-zv]+', base) for base in item['pinyin_syllables']):
                reason = 'Source syllable notation has no supported four-tone reference key'
            elif item['kind'] == 'syllable_drill' and item['lexical_tones'] == [0]:
                reason = 'An isolated neutral-tone drill is not a contextual neutral-tone quiz example'
            elif 'third_tone_grouping_ambiguous' in tags:
                reason = 'Spoken third-tone grouping is not uniquely established for this item'
            elif 'N' in pattern(surface):
                reason = 'Contextual neutral-tone realization needs its own supported spoken-pattern assessment'
            elif pattern(surface) != item['lexical_pattern']:
                reason = 'Connected-speech tone changes have not been individually verified for this recording'
            examples.append({
                **item, 'lesson_id': lesson['lesson_id'], 'source_audio_path': lesson['audio_path'],
                'source_audio_sha256': lesson['audio_sha256'], 'segment': match['segment'],
                'mapping': {
                    'status': match['status'], 'intro_verified': mapped['intro_verified'],
                    'boundary_stable': match['status'] == 'mapped', 'full_utterance': match['status'] == 'mapped',
                    'phonetic_support': match.get('phonetic_support'), 'reason': match['reason'],
                },
                'surface_pattern': pattern(surface), 'sandhi_tags': tags,
                'quiz_eligible': match['status'] == 'mapped' and reason is None, 'block_reason': reason,
            })
    counts = {
        'indexed_examples': len(examples), 'mapped_examples': sum(item['mapping']['status'] == 'mapped' for item in examples),
        'quiz_candidates_before_complete_references': sum(item['quiz_eligible'] for item in examples),
        'by_kind': dict(Counter(item['kind'] for item in examples)),
        'unresolved_mapping_reasons': dict(Counter(item['mapping']['reason'] for item in examples if item['mapping']['status'] != 'mapped')),
    }
    catalog = read_json(ROOT / 'data/glossika_recordings.json')
    output = {
        'version': 1, 'source': 'glossika', 'available': True, 'method': METHOD,
        'distribution_scope': 'local_only', 'book_sha256': index['book_sha256'],
        'archive_sha256': catalog['archive_sha256'], 'index_sha256': index_hash,
        'boundaries_sha256': digest(MAPPINGS.read_bytes()), 'examples': examples, 'counts': counts,
        'independent_pronunciation_certification': False,
    }
    write_json(PRACTICE, output)
    print(json.dumps(counts, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('scan', 'intros', 'recognition', 'publish', 'restore'), required=True)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.phase == 'scan':
        scan()
    elif args.phase == 'intros':
        collect_intros(args.limit)
    elif args.phase == 'recognition':
        collect_recognition(args.limit)
    elif args.phase == 'publish':
        publish_mappings()
    else:
        restore_practice()


if __name__ == '__main__':
    main()
