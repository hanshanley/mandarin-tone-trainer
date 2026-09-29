#!/usr/bin/env python3
"""Build a local, reproducible item index from the pinned Glossika PDF."""
import argparse
import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOK_SHA256 = '598fb985ed52628ec1a5d27e0a9e41347d49244f81abcd713d6aa233dacf49ba'
PYMUPDF_VERSION = '1.26.5'
METHOD = 'pymupdf-indesign-geometry-v1'
ADVERTISED_COUNTS = {
    'syllable_bases': 385,
    'syllable_items': 1925,
    'two_tone_items': 1082,
    'three_tone_items': 2048,
    'total_items': 5055,
}
PINNED_PART_ROW_COUNTS = {
    1: 7, 2: 11, 3: 19, 4: 4, 5: 18, 6: 4, 7: 6, 8: 17, 9: 1,
    10: 7, 11: 18, 12: 4, 13: 18, 14: 4, 15: 14, 16: 5, 17: 18,
    18: 6, 19: 7, 20: 15, 21: 10, 22: 19, 23: 6, 24: 12, 25: 13,
    26: 19, 27: 7, 28: 16, 29: 9, 30: 14, 31: 4, 32: 17, 33: 11,
    34: 19, 35: 4,
}
TONE_MARKS = {'\u0304': 1, '\u0301': 2, '\u030c': 3, '\u0300': 4}
HEADING_RE = re.compile(r'MANDARIN [1-4](?:\+[1-4]|(?:-[1-4]){2})')
PART_RE = re.compile(r'Part (\d+):')
MC_RE = re.compile(r'MC\d{2,3}')


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def bounds_union(bounds):
    return [
        round(min(box[0] for box in bounds), 3),
        round(min(box[1] for box in bounds), 3),
        round(max(box[2] for box in bounds), 3),
        round(max(box[3] for box in bounds), 3),
    ]


def is_cjk(char):
    return (
        '\u3400' <= char <= '\u4dbf'
        or '\u4e00' <= char <= '\u9fff'
        or '\uf900' <= char <= '\ufaff'
        or '\U00020000' <= char <= '\U0002fa1f'
    )


def cjk_text(value):
    return ''.join(char for char in value if is_cjk(char))


def source_syllable(value):
    """Return the canonical base and source tone for one marked pinyin cell."""
    text = unicodedata.normalize('NFC', value.strip())
    letters = []
    tone = 0
    for char in unicodedata.normalize('NFD', text):
        if char in TONE_MARKS:
            if tone and tone != TONE_MARKS[char]:
                return None
            tone = TONE_MARKS[char]
        elif char == '\u0308':
            if not letters or letters[-1] != 'u':
                return None
            letters[-1] = 'v'
        elif char.isascii() and char.isalpha():
            letters.append(char.lower())
        elif char == '·':
            continue
        else:
            return None
    if not letters or (tone == 0 and not text.endswith('·')):
        return None
    return ''.join(letters), tone


def token_records(chars):
    """Split positioned PDF characters on source whitespace, preserving bounds."""
    records = []
    current = []
    for char in sorted(chars, key=lambda item: (item['bbox'][0], item['bbox'][1])):
        if char['c'].isspace():
            if current:
                records.append({
                    'text': ''.join(item['c'] for item in current),
                    'bounds': bounds_union([item['bbox'] for item in current]),
                })
                current = []
        else:
            current.append(char)
    if current:
        records.append({
            'text': ''.join(item['c'] for item in current),
            'bounds': bounds_union([item['bbox'] for item in current]),
        })
    return records


def normalized_spans(page):
    spans = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            for span in line['spans']:
                spans.append({
                    'text': ''.join(char['c'] for char in span['chars']),
                    'bbox': tuple(span['bbox']),
                    'origin': tuple(span['origin']),
                    'font': span['font'],
                    'size': span['size'],
                    'chars': [
                        {'c': char['c'], 'bbox': tuple(char['bbox'])}
                        for char in span['chars']
                    ],
                })
    return spans


def baseline_groups(spans, tolerance=0.5):
    groups = []
    for span in sorted(spans, key=lambda item: (item['origin'][1], item['bbox'][0])):
        group = next(
            (candidate for candidate in groups
             if abs(candidate['origin_y'] - span['origin'][1]) < tolerance),
            None,
        )
        if group is None:
            group = {'origin_y': span['origin'][1], 'spans': [], 'chars': []}
            groups.append(group)
        group['spans'].append(span)
        group['chars'].extend(span['chars'])
    return sorted(groups, key=lambda item: item['origin_y'])


def syllable_row(group):
    tokens = token_records(group['chars'])
    if len(tokens) < 5:
        return None
    parsed = [source_syllable(token['text']) for token in tokens[:5]]
    first_four = parsed[:4]
    if (
        not all(first_four)
        or [item[1] for item in first_four] != [1, 2, 3, 4]
        or len({item[0] for item in first_four}) != 1
    ):
        return None
    base = first_four[0][0]
    neutral = tokens[4]
    if parsed[4] != (base, 0):
        match = re.match(r'^([A-Za-züÜvV]+·)', neutral['text'])
        if not match or source_syllable(match.group(1)) != (base, 0):
            return {
                'error': 'malformed_neutral_cell',
                'source_text': neutral['text'],
                'bounds': neutral['bounds'],
            }
        neutral = {**neutral, 'text': match.group(1)}
        neutral_chars = []
        remaining = len(neutral['text'])
        for char in sorted(group['chars'], key=lambda item: item['bbox'][0]):
            if char['bbox'][0] < tokens[4]['bounds'][0]:
                continue
            if remaining <= 0:
                break
            neutral_chars.append(char)
            remaining -= len(char['c'])
        neutral['bounds'] = bounds_union([char['bbox'] for char in neutral_chars])
    return {'base': base, 'cells': [*tokens[:4], neutral]}


def page_headings(spans):
    headings = []
    for span in spans:
        text = span['text'].strip()
        part = PART_RE.match(text)
        if part:
            headings.append({
                'kind': 'part',
                'number': int(part.group(1)),
                'text': text,
                'y': span['bbox'][1],
                'bounds': [round(value, 3) for value in span['bbox']],
            })
        if HEADING_RE.fullmatch(text):
            headings.append({
                'kind': 'word',
                'text': text,
                'y': span['bbox'][1],
                'bounds': [round(value, 3) for value in span['bbox']],
            })
    return sorted(headings, key=lambda item: item['y'])


def extract_syllable_lessons(document, page_map, lessons):
    by_part = {}
    unresolved = []
    for printed_page in range(8, 31):
        pdf_page = page_map[printed_page]
        spans = normalized_spans(document[pdf_page - 1])
        headings = [item for item in page_headings(spans) if item['kind'] == 'part']
        page_rows = []
        for group in baseline_groups(spans):
            row = syllable_row(group)
            if row is None:
                continue
            prior = [heading for heading in headings if heading['y'] < group['origin_y']]
            source_row = len(page_rows) + 1
            if not prior:
                unresolved.append({
                    'reason': 'syllable_row_without_part_heading',
                    'printed_page': printed_page,
                    'pdf_page': pdf_page,
                    'source_row': source_row,
                })
                continue
            heading = prior[-1]
            if row.get('error'):
                unresolved.append({
                    'reason': row['error'],
                    'printed_page': printed_page,
                    'pdf_page': pdf_page,
                    'source_row': source_row,
                    'source_pattern': heading['text'],
                    'bounds': row['bounds'],
                })
                continue
            page_rows.append(row)
            by_part.setdefault(heading['number'], []).append({
                **row,
                'source_pattern': heading['text'],
                'printed_page': printed_page,
                'pdf_page': pdf_page,
                'source_row': source_row,
            })

    result = {}
    for lesson in lessons:
        match = re.fullmatch(r'Vowel Part (\d+)\.mp3', lesson['archive_member'])
        if not match:
            continue
        part = int(match.group(1))
        items = []
        for row in by_part.get(part, []):
            for cell in row['cells']:
                parsed = source_syllable(cell['text'])
                ordinal = len(items) + 1
                tone = parsed[1]
                items.append({
                    'id': f'{lesson["id"]}-{ordinal:04d}',
                    'ordinal': ordinal,
                    'printed_page': row['printed_page'],
                    'pdf_page': row['pdf_page'],
                    'word': row['base'],
                    'word_label_type': 'pinyin_syllable',
                    'traditional': None,
                    'pinyin': cell['text'],
                    'pinyin_syllables': [row['base']],
                    'lexical_tones': [tone],
                    'lexical_pattern': 'N' if tone == 0 else str(tone),
                    'source_tone_notation': [
                        'middle_dot' if tone == 0 else 'diacritic'
                    ],
                    'source_pattern': row['source_pattern'],
                    'source_row': row['source_row'],
                    'bounds': cell['bounds'],
                    'kind': 'syllable_drill',
                })
        result[lesson['id']] = items
    missing_parts = sorted(set(range(1, 36)) - set(by_part))
    for part in missing_parts:
        lesson = next(
            row for row in lessons if row['archive_member'] == f'Vowel Part {part}.mp3'
        )
        unresolved.append({
            'reason': 'part_has_no_extractable_rows',
            'lesson_id': lesson['id'],
            'printed_pages': lesson['printed_pages'],
        })
    for part, expected in PINNED_PART_ROW_COUNTS.items():
        extracted = len(by_part.get(part, []))
        if extracted != expected:
            unresolved.append({
                'reason': 'pinned_part_row_count_changed',
                'lesson_id': f'vowel-part-{part}',
                'expected_rows': expected,
                'extracted_rows': extracted,
            })
    return result, unresolved


def pinyin_letters(value):
    letters = []
    tones = []
    boundaries = set()
    neutral_markers = set()
    for char in unicodedata.normalize('NFD', unicodedata.normalize('NFC', value.strip())):
        if char in TONE_MARKS:
            if not tones or tones[-1] not in (0, TONE_MARKS[char]):
                raise ValueError('multiple tone marks on one pinyin letter')
            tones[-1] = TONE_MARKS[char]
        elif char == '\u0308':
            if not letters or letters[-1] != 'u':
                raise ValueError('diaeresis is not attached to u')
            letters[-1] = 'v'
        elif char.isascii() and char.isalpha():
            letters.append(char.lower())
            tones.append(0)
        elif char in "'’ -":
            boundaries.add(len(letters))
        elif char == '·':
            neutral_markers.add(len(letters))
        else:
            raise ValueError(f'unsupported pinyin character U+{ord(char):04X}')
    if not letters:
        raise ValueError('empty pinyin')
    return ''.join(letters), tones, boundaries, neutral_markers


def split_pinyin(value, expected_count, inventory):
    try:
        letters, letter_tones, boundaries, neutral_markers = pinyin_letters(value)
    except ValueError as error:
        return None, str(error)
    if any(boundary in (0, len(letters)) for boundary in boundaries):
        return None, 'pinyin boundary is not between syllables'

    choices = tuple(sorted(set(inventory) | {'r'}, key=lambda item: (-len(item), item)))

    def candidates_for(require_vowel_separator):
        @lru_cache(maxsize=None)
        def walk(position, remaining):
            if remaining == 0:
                return ((),) if position == len(letters) else ()
            candidates = []
            for syllable in choices:
                if not letters.startswith(syllable, position):
                    continue
                if (
                    require_vowel_separator
                    and position
                    and syllable[0] in 'aeo'
                    and position not in boundaries
                ):
                    continue
                end = position + len(syllable)
                if any(position < boundary < end for boundary in boundaries):
                    continue
                for rest in walk(end, remaining - 1):
                    candidates.append((syllable, *rest))
                    if len(candidates) > 100:
                        return tuple(candidates)
            return tuple(candidates)

        candidates = []
        for candidate in sorted(set(walk(0, expected_count))):
            position = 0
            candidate_tones = []
            valid = True
            for syllable in candidate:
                end = position + len(syllable)
                marked = {tone for tone in letter_tones[position:end] if tone}
                if len(marked) > 1 or (end in neutral_markers and marked):
                    valid = False
                    break
                candidate_tones.append(next(iter(marked), 0))
                position = end
            endpoints = {
                sum(len(syllable) for syllable in candidate[:index])
                for index in range(1, len(candidate) + 1)
            }
            if valid and neutral_markers.issubset(endpoints):
                candidates.append((candidate, candidate_tones))
        return candidates

    candidates = candidates_for(require_vowel_separator=True)
    source_error = None
    if not candidates:
        candidates = candidates_for(require_vowel_separator=False)
        if candidates:
            source_error = 'source_missing_pinyin_separator'
    if not candidates:
        return None, 'pinyin does not segment into the source syllable inventory'
    if len(candidates) != 1:
        tone_sequences = {tuple(item[1]) for item in candidates}
        return {
            'syllables': [],
            'tones': list(next(iter(tone_sequences))) if len(tone_sequences) == 1 else [],
            'candidates': [list(item[0]) for item in candidates],
        }, f'ambiguous pinyin segmentation ({len(candidates)} candidates)'
    syllables, tones = candidates[0]
    notations = []
    position = 0
    for syllable, tone in zip(syllables, tones):
        position += len(syllable)
        notations.append(
            'diacritic' if tone else (
                'middle_dot' if position in neutral_markers else 'unmarked'
            )
        )
    return {
        'syllables': list(syllables),
        'tones': tones,
        'source_tone_notation': notations,
    }, source_error


def joined_row_text(spans):
    ordered = sorted(spans, key=lambda item: item['bbox'][0])
    text = ''
    previous = None
    for span in ordered:
        gap = span['bbox'][0] - previous['bbox'][2] if previous else 0
        if (
            previous
            and gap > 6
            and text
            and not text[-1].isspace()
            and span['text']
            and not span['text'][0].isspace()
        ):
            text += ' '
        text += span['text']
        previous = span
    return text.strip()


def parse_word_row(spans, anchor):
    anchor_center = (anchor['bbox'][1] + anchor['bbox'][3]) / 2
    row_spans = [
        span for span in spans
        if abs((span['bbox'][1] + span['bbox'][3]) / 2 - anchor_center) <= 3.6
    ]
    text = joined_row_text(row_spans)
    tokens = text.split()
    if len(tokens) != 4 or tokens[0] != anchor['text'].strip():
        return None, {
            'reason': 'word_row_does_not_have_four_source_fields',
            'source_text': text,
            'bounds': bounds_union([span['bbox'] for span in row_spans]),
        }
    historical_category, traditional_source, simplified, pinyin = tokens
    traditional = cjk_text(traditional_source)
    simplified_hanzi = cjk_text(simplified)
    if not traditional or simplified_hanzi != simplified:
        return None, {
            'reason': 'word_row_has_invalid_hanzi_fields',
            'source_text': text,
            'bounds': bounds_union([span['bbox'] for span in row_spans]),
        }
    markers = ''.join(char for char in traditional_source if not is_cjk(char))
    return {
        'historical_category': historical_category,
        'traditional': traditional,
        'traditional_source': traditional_source,
        'historical_coda_markers': markers or None,
        'word': simplified,
        'pinyin': pinyin,
        'bounds': bounds_union([span['bbox'] for span in row_spans]),
    }, None


def heading_tones(heading):
    suffix = heading.removeprefix('MANDARIN ')
    separator = '+' if '+' in suffix else '-'
    return [int(value) for value in suffix.split(separator)]


def extract_word_lessons(document, page_map, lessons, inventory):
    by_heading = {}
    unresolved = []
    current_heading = None
    for printed_page in range(34, 163):
        pdf_page = page_map[printed_page]
        spans = normalized_spans(document[pdf_page - 1])
        events = []
        for heading in page_headings(spans):
            if heading['kind'] == 'word':
                events.append((heading['y'], 0, 'heading', heading))
        for span in spans:
            if MC_RE.fullmatch(span['text'].strip()):
                events.append((span['bbox'][1], 1, 'row', span))
        page_row = 0
        for _, _, kind, value in sorted(events, key=lambda item: (item[0], item[1])):
            if kind == 'heading':
                current_heading = value['text']
                by_heading.setdefault(current_heading, [])
                continue
            page_row += 1
            if current_heading is None:
                unresolved.append({
                    'reason': 'word_row_without_heading',
                    'printed_page': printed_page,
                    'pdf_page': pdf_page,
                    'source_row': page_row,
                })
                continue
            row, error = parse_word_row(spans, value)
            if error:
                unresolved.append({
                    **error,
                    'printed_page': printed_page,
                    'pdf_page': pdf_page,
                    'source_row': page_row,
                    'source_pattern': current_heading,
                })
                continue
            by_heading.setdefault(current_heading, []).append({
                **row,
                'printed_page': printed_page,
                'pdf_page': pdf_page,
                'source_row': page_row,
                'source_pattern': current_heading,
            })

    result = {}
    for lesson in lessons:
        if lesson['category'] not in ('two-tone', 'three-tone'):
            continue
        rows = by_heading.get(lesson['book_heading'], [])
        items = []
        expected_tones = heading_tones(lesson['book_heading'])
        for row in rows:
            ordinal = len(items) + 1
            item_id = f'{lesson["id"]}-{ordinal:04d}'
            expected_count = len(cjk_text(row['word']))
            reading, reason = split_pinyin(row['pinyin'], expected_count, inventory)
            if reason:
                syllables = reading['syllables'] if reading else []
                tones = reading['tones'] if reading else []
                notations = reading.get('source_tone_notation', []) if reading else []
                candidates = reading.get('candidates') if reading else None
                pattern = (
                    '-'.join('N' if tone == 0 else str(tone) for tone in tones)
                    if tones else None
                )
                unresolved.append({
                    'reason': reason,
                    'lesson_id': lesson['id'],
                    'item_id': item_id,
                    'word': row['word'],
                    'pinyin': row['pinyin'],
                    'pinyin_syllables': syllables,
                    'pinyin_syllable_candidates': candidates,
                    'lexical_pattern': pattern,
                    'source_tone_notation': notations,
                    'source_pattern': row['source_pattern'],
                    'source_note': (
                        'The exact source pinyin omits a separator before an internal '
                        'a/e/o-initial syllable; segmentation is recorded without '
                        'altering the source text.'
                        if reason == 'source_missing_pinyin_separator' else None
                    ),
                    'printed_page': row['printed_page'],
                    'pdf_page': row['pdf_page'],
                    'source_row': row['source_row'],
                    'bounds': row['bounds'],
                })
            else:
                syllables = reading['syllables']
                tones = reading['tones']
                notations = reading['source_tone_notation']
                candidates = None
                pattern = '-'.join('N' if tone == 0 else str(tone) for tone in tones)
                if tones != expected_tones:
                    unresolved.append({
                        'reason': 'source_heading_inconsistency',
                        'lesson_id': lesson['id'],
                        'item_id': item_id,
                        'word': row['word'],
                        'pinyin': row['pinyin'],
                        'printed_page': row['printed_page'],
                        'pdf_page': row['pdf_page'],
                        'source_row': row['source_row'],
                        'source_pattern': row['source_pattern'],
                        'lexical_pattern': pattern,
                        'source_note': (
                            'The exact source pinyin diacritics disagree with the printed '
                            'lesson heading; neither value was repaired.'
                        ),
                        'bounds': row['bounds'],
                    })
            items.append({
                'id': item_id,
                'ordinal': ordinal,
                'printed_page': row['printed_page'],
                'pdf_page': row['pdf_page'],
                'word': row['word'],
                'traditional': row['traditional'],
                'traditional_source': row['traditional_source'],
                'historical_category': row['historical_category'],
                'historical_coda_markers': row['historical_coda_markers'],
                'pinyin': row['pinyin'],
                'pinyin_syllables': syllables,
                'pinyin_syllable_candidates': candidates,
                'lexical_tones': tones,
                'lexical_pattern': pattern,
                'source_tone_notation': notations,
                'source_pattern': row['source_pattern'],
                'source_pattern_matches_lexical': tones == expected_tones if tones else None,
                'source_row': row['source_row'],
                'bounds': row['bounds'],
                'kind': 'word_drill',
            })
        result[lesson['id']] = items
    missing_headings = sorted({
        lesson['book_heading'] for lesson in lessons
        if lesson['category'] in ('two-tone', 'three-tone')
    } - set(by_heading))
    for heading in missing_headings:
        unresolved.append({'reason': 'catalog_heading_not_found', 'source_pattern': heading})
    return result, unresolved


def discrepancy(name, extracted):
    advertised = ADVERTISED_COUNTS[name]
    return {
        'name': name,
        'advertised': advertised,
        'extracted': extracted,
        'difference': extracted - advertised,
        'reason': 'The pinned PDF contains fewer geometrically identifiable source rows; no entries were fabricated.',
    }


def load_pinyin_inventory(path):
    values = json.loads(path.read_text(encoding='utf-8'))
    inventory = {
        syllable.lower().replace('ü', 'v')
        for word in values
        for syllable in word.get('pinyin_syllables', [])
        if isinstance(syllable, str) and re.fullmatch(r'[a-züv]+', syllable.lower())
    }
    if len(inventory) < 390:
        raise ValueError('Repository pinyin inventory is unexpectedly incomplete')
    return inventory


def build_index(
    document,
    catalog,
    book_sha256,
    catalog_sha256,
    pinyin_inventory,
    pinyin_inventory_sha256,
):
    if book_sha256 != BOOK_SHA256:
        raise ValueError('Glossika PDF differs from the pinned original')
    if catalog.get('book', {}).get('sha256') != BOOK_SHA256:
        raise ValueError('Glossika catalog does not reference the pinned book')
    lessons = catalog.get('lessons', [])
    if len(lessons) != 117:
        raise ValueError('Glossika catalog must contain all 117 lesson recordings')
    page_map = {row['printed_page']: row['pdf_page'] for row in catalog.get('pages', [])}
    required_pages = set(range(8, 31)) | set(range(34, 163))
    if not required_pages.issubset(page_map):
        raise ValueError('Glossika catalog is missing required printed-to-physical page mappings')

    syllable_items, unresolved = extract_syllable_lessons(document, page_map, lessons)
    source_inventory = {
        item['pinyin_syllables'][0]
        for items in syllable_items.values()
        for item in items
    }
    word_items, word_unresolved = extract_word_lessons(
        document, page_map, lessons, pinyin_inventory
    )
    unresolved.extend(word_unresolved)

    indexed_lessons = []
    excluded_lessons = []
    for lesson in lessons:
        if lesson['category'] == 'consonants':
            excluded_lessons.append({
                'lesson_id': lesson['id'],
                'audio_path': lesson['audio_path'],
                'audio_sha256': lesson['sha256'],
                'reason': 'Consonant overview track has no individually tone-graded example rows.',
            })
            continue
        items = syllable_items.get(lesson['id'], word_items.get(lesson['id'], []))
        indexed_lessons.append({
            'lesson_id': lesson['id'],
            'audio_path': lesson['audio_path'],
            'audio_sha256': lesson['sha256'],
            'category': lesson['category'],
            'items': items,
        })

    category_counts = {
        category: sum(
            len(lesson['items']) for lesson in indexed_lessons
            if lesson['category'] == category
        )
        for category in ('syllables', 'two-tone', 'three-tone')
    }
    syllable_bases = category_counts['syllables'] // 5
    total_items = sum(category_counts.values())
    discrepancies = [
        discrepancy('syllable_bases', syllable_bases),
        discrepancy('syllable_items', category_counts['syllables']),
        discrepancy('two_tone_items', category_counts['two-tone']),
        discrepancy('three_tone_items', category_counts['three-tone']),
        discrepancy('total_items', total_items),
    ]
    discrepancies = [item for item in discrepancies if item['difference']]
    hard_errors = {
        'syllable_row_without_part_heading',
        'malformed_neutral_cell',
        'part_has_no_extractable_rows',
        'pinned_part_row_count_changed',
        'word_row_without_heading',
        'word_row_does_not_have_four_source_fields',
        'word_row_has_invalid_hanzi_fields',
        'catalog_heading_not_found',
    }
    unresolved_counts = {
        reason: sum(1 for item in unresolved if item['reason'] == reason)
        for reason in sorted({item['reason'] for item in unresolved})
    }
    return {
        'version': 1,
        'method': METHOD,
        'method_version': 1,
        'book_sha256': book_sha256,
        'catalog_sha256': catalog_sha256,
        'pinyin_inventory_sha256': pinyin_inventory_sha256,
        'pymupdf_version': PYMUPDF_VERSION,
        'tone_convention': {
            'lexical_tones': '1..4; neutral tone is integer 0',
            'lexical_pattern': 'Hyphen-separated tones; neutral tone is N',
            'pinyin_syllables': 'Lowercase bare pinyin; ü is normalized to v',
            'source_tone_notation': 'Per syllable: diacritic, middle_dot, or unmarked',
            'segmentation': 'Source separators are mandatory boundaries; an internal a/e/o onset is accepted only after a source separator.',
            'source_corrections': 'None. Exact source text is retained and source inconsistencies are reported in unresolved.',
        },
        'order_guarantee': 'Lesson order follows data/glossika_recordings.json; item order is printed-page order, then geometric top-to-bottom row order, with tones 1,2,3,4,neutral across syllable rows.',
        'extraction_complete': not any(item['reason'] in hard_errors for item in unresolved),
        'annotation_complete': not unresolved,
        'lessons': indexed_lessons,
        'counts': {
            'indexed_lessons': len(indexed_lessons),
            'excluded_lessons': len(excluded_lessons),
            'syllable_bases': syllable_bases,
            'syllable_items': category_counts['syllables'],
            'two_tone_items': category_counts['two-tone'],
            'three_tone_items': category_counts['three-tone'],
            'total_items': total_items,
            'unresolved': len(unresolved),
        },
        'advertised_counts': ADVERTISED_COUNTS,
        'source_syllable_inventory': {
            'distinct_bases': len(source_inventory),
            'segmentation_inventory': 'data/hsk_words.json pinyin_syllables',
        },
        'count_discrepancies': discrepancies,
        'excluded_lessons': excluded_lessons,
        'unresolved_counts': unresolved_counts,
        'unresolved': unresolved,
    }


def summary_for(index, index_path, index_sha256):
    return {
        'version': 1,
        'method': index['method'],
        'method_version': index['method_version'],
        'book_sha256': index['book_sha256'],
        'catalog_sha256': index['catalog_sha256'],
        'pinyin_inventory_sha256': index['pinyin_inventory_sha256'],
        'pymupdf_version': index['pymupdf_version'],
        'index_path': index_path.as_posix(),
        'index_sha256': index_sha256,
        'extraction_complete': index['extraction_complete'],
        'annotation_complete': index['annotation_complete'],
        'counts': index['counts'],
        'advertised_counts': index['advertised_counts'],
        'source_syllable_inventory': index['source_syllable_inventory'],
        'count_discrepancies': index['count_discrepancies'],
        'unresolved_counts': index['unresolved_counts'],
        'per_lesson_counts': [
            {
                'lesson_id': lesson['lesson_id'],
                'category': lesson['category'],
                'item_count': len(lesson['items']),
            }
            for lesson in index['lessons']
        ],
        'excluded_lessons': [
            {
                'lesson_id': lesson['lesson_id'],
                'reason': lesson['reason'],
            }
            for lesson in index['excluded_lessons']
        ],
        'unresolved': [
            {
                key: value for key, value in item.items()
                if key not in {'source_text'}
            }
            for item in index['unresolved']
        ],
        'conventions': {
            'tone_values': '1..4, neutral=0; lexical_pattern uses N for neutral',
            'syllable_normalization': 'bare lowercase pinyin with ü normalized to v',
            'source_tone_notation': 'per syllable: diacritic, middle_dot, or unmarked',
            'strict_segmentation': 'source separators are mandatory; internal a/e/o onsets require a source separator',
            'source_corrections': 'none; source text is retained and inconsistencies are explicit',
            'word_authority': 'simplified/traditional/pinyin are transcribed from the source row; MC digits are preserved only as historical_category',
            'audio_order': index['order_guarantee'],
        },
    }


def write_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.part')
    temporary.write_bytes(payload)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, default=ROOT / 'imports/glossika/original.pdf')
    parser.add_argument('--catalog', type=Path, default=ROOT / 'data/glossika_recordings.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'audio/glossika/example_index.json')
    parser.add_argument('--summary', type=Path, default=ROOT / 'data/glossika_example_summary.json')
    parser.add_argument(
        '--pinyin-inventory',
        type=Path,
        default=ROOT / 'data/hsk_words.json',
        help='Repository vocabulary whose canonical syllables constrain source-pinyin segmentation',
    )
    parser.add_argument('--check', action='store_true', help='Verify existing outputs without rewriting them')
    args = parser.parse_args()

    try:
        import fitz
    except ImportError as error:
        raise SystemExit('Glossika indexing requires PyMuPDF 1.26.5') from error
    if fitz.VersionBind != PYMUPDF_VERSION:
        raise SystemExit(f'Glossika indexing requires PyMuPDF {PYMUPDF_VERSION}')
    if file_sha256(args.pdf) != BOOK_SHA256:
        raise SystemExit('Glossika PDF differs from the pinned original')

    catalog_bytes = args.catalog.read_bytes()
    catalog = json.loads(catalog_bytes)
    pinyin_inventory_bytes = args.pinyin_inventory.read_bytes()
    pinyin_inventory = load_pinyin_inventory(args.pinyin_inventory)
    document = fitz.open(args.pdf)
    try:
        if len(document) != 175:
            raise SystemExit('Glossika PDF must contain exactly 175 physical pages')
        index = build_index(
            document,
            catalog,
            BOOK_SHA256,
            hashlib.sha256(catalog_bytes).hexdigest(),
            pinyin_inventory,
            hashlib.sha256(pinyin_inventory_bytes).hexdigest(),
        )
    finally:
        document.close()

    index_payload = json_bytes(index)
    summary = summary_for(
        index,
        args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output,
        hashlib.sha256(index_payload).hexdigest(),
    )
    summary_payload = json_bytes(summary)
    if args.check:
        if args.output.read_bytes() != index_payload or args.summary.read_bytes() != summary_payload:
            raise SystemExit('Glossika example index outputs are stale; run without --check')
    else:
        write_atomic(args.output, index_payload)
        write_atomic(args.summary, summary_payload)

    print(json.dumps({
        'index_path': str(args.output),
        'index_sha256': summary['index_sha256'],
        'counts': index['counts'],
        'count_discrepancies': index['count_discrepancies'],
        'extraction_complete': index['extraction_complete'],
    }, ensure_ascii=False, sort_keys=True))
    if not index['extraction_complete']:
        raise SystemExit('Glossika extraction has unresolved structural errors')


if __name__ == '__main__':
    main()
