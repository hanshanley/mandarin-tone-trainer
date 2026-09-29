#!/usr/bin/env python3
"""Restore the publisher's complete personal-use book companion, never quiz cuts."""
import argparse
import csv
import hashlib
import io
import itertools
import json
import re
import stat
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_URL = 'https://glossika-saas.s3-ap-northeast-1.amazonaws.com/free-download/Glossika+Tone+Training.zip'
ARCHIVE_SHA256 = '3363ef0452006ad5422a6335906a8856c242c912d2c8c4b8cb31b738c14bd81b'
BOOK_URL = 'https://d310pm6npapqqb.cloudfront.net/free-download/Glossika%20Chinese%20Pronunciation%20%26%20Tone%20Training.pdf'
BOOK_SHA256 = '598fb985ed52628ec1a5d27e0a9e41347d49244f81abcd713d6aa233dacf49ba'
SOURCE_PAGE = 'https://ai.glossika.com/free-download/glossika-chinese-pronunciation-tones-training'
INDEX = ROOT / 'data/glossika_recordings.json'
MAX_BYTES = 200 * 1024 * 1024
RENDERER_VERSION = '1.26.5'


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def expected_members():
    return {
        *(f'Consonant {n}.mp3' for n in (1, 2)),
        *(f'Vowel Part {n}.mp3' for n in range(1, 36)),
        *(f'Tone {"".join(tones)}.mp3' for count in (2, 3)
          for tones in itertools.product('1234', repeat=count)),
    }


def fetch_pinned(url, target, expected):
    if target.exists():
        payload = target.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=90) as response:
            payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES or digest(payload) != expected:
        raise ValueError(f'Publisher file differs from pinned source: {url}')
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return payload


def archive_tracks(payload):
    if digest(payload) != ARCHIVE_SHA256:
        raise ValueError('Glossika archive differs from pinned source')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) != 117 or {item.filename for item in members} != expected_members():
            raise ValueError('Unexpected Glossika lesson inventory')
        if sum(item.file_size for item in members) > MAX_BYTES:
            raise ValueError('Glossika archive exceeds uncompressed size limit')
        tracks = {}
        for item in members:
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Symlink in Glossika source archive')
            data = archive.read(item)
            if len(data) < 128 or not data.startswith((b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')):
                raise ValueError(f'Invalid MP3 lesson: {item.filename}')
            tracks[item.filename] = data
        return tracks


def asset_target(root, relative):
    path = PurePosixPath(relative)
    if not relative.startswith('audio/glossika/') or '..' in path.parts or '\\' in relative:
        raise ValueError('Invalid Glossika destination')
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Glossika destination escapes repository')
    return target


def write_asset(root, relative, payload, expected):
    target = asset_target(root, relative)
    if digest(payload) != expected:
        raise ValueError('Invalid Glossika source hash')
    if target.exists():
        if digest(target.read_bytes()) != expected:
            raise ValueError(f'Existing Glossika asset changed: {relative}')
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.part')
    temporary.write_bytes(payload)
    temporary.replace(target)


def open_book(payload):
    if digest(payload) != BOOK_SHA256:
        raise ValueError('Glossika book differs from pinned original')
    try:
        import fitz
    except ImportError as error:
        raise SystemExit('Book rendering needs PyMuPDF; install requirements.txt') from error
    if fitz.VersionBind != RENDERER_VERSION:
        raise SystemExit(f'Exact book-page restoration requires PyMuPDF {RENDERER_VERSION}; install requirements.txt')
    document = fitz.open(stream=payload, filetype='pdf')
    if len(document) != 175:
        raise ValueError('Unexpected companion book page count')
    return document


def page_bytes(document, number):
    import fitz
    return document[number - 1].get_pixmap(matrix=fitz.Matrix(1.75, 1.75), alpha=False).tobytes('png')


def printed_pages(document):
    result = {}
    for number, page in enumerate(document, 1):
        footer = [word[4] for word in page.get_text('words')
                  if word[1] > page.rect.height - 60
                  and page.rect.width * .45 < (word[0] + word[2]) / 2 < page.rect.width * .55
                  and re.fullmatch(r'\d+', word[4])]
        if not footer:
            continue
        # The publisher numbers both the About page and the Introduction as 3.
        if footer == ['3'] and 'About Glossika' in page.get_text():
            continue
        if len(footer) != 1 or int(footer[0]) in result:
            raise ValueError('Ambiguous printed book page')
        result[int(footer[0])] = number
    return result


def build_index(tracks, document, mapping):
    if len(mapping) != 117 or {row['archive_member'] for row in mapping} != expected_members():
        raise ValueError('Incomplete companion track/page mapping')
    pages = printed_pages(document)
    used_pages = {3}
    lessons = []
    for row in mapping:
        span = [int(value) for value in row['pdf_printed_pages'].split('-')]
        if len(span) not in (1, 2) or not 4 <= span[0] <= span[-1] <= 162:
            raise ValueError('Invalid lesson page range')
        associated = list(range(span[0], span[-1] + 1))
        if any(number not in pages for number in associated):
            raise ValueError('Lesson page missing from original book')
        used_pages.update(associated)
        name = row['archive_member']
        key = Path(name).stem.lower().replace(' ', '-')
        tone_match = re.fullmatch(r'Tone ([1-4]{2,3})\.mp3', name)
        category = ('two-tone' if len(tone_match[1]) == 2 else 'three-tone') if tone_match else (
            'consonants' if name.startswith('Consonant') else 'syllables')
        lessons.append({
            'id': key, 'title': Path(name).stem, 'category': category,
            'recording_type': 'book_lesson', 'quiz_eligible': False,
            'archive_member': name, 'audio_path': f'audio/glossika/lessons/{key}.mp3',
            'sha256': digest(tracks[name]), 'byte_length': len(tracks[name]),
            'printed_pages': associated, 'book_heading': row['pdf_heading'],
            'page_mapping_basis': row['mapping_basis'],
        })
    assets = []
    for printed in sorted(used_pages):
        data = page_bytes(document, pages[printed])
        assets.append({
            'printed_page': printed, 'pdf_page': pages[printed],
            'audio_path': f'audio/glossika/pages/{printed:03d}.png',
            'sha256': digest(data), 'byte_length': len(data),
        })
    return {
        'version': 1, 'source': 'glossika', 'available': True,
        'title': 'Chinese Pronunciation & Tone Training', 'creator': 'Michael Campbell / Glossika',
        'copyright': 'Copyright 2018 Glossika. All rights reserved.',
        'license': 'All rights reserved', 'rights_status': 'personal_companion_only',
        'distribution_scope': 'local_only', 'source_page': SOURCE_PAGE,
        'archive_url': ARCHIVE_URL, 'archive_sha256': ARCHIVE_SHA256,
        'terms_url': 'https://ai.glossika.com/terms',
        'book': {'audio_path': 'audio/glossika/book.pdf', 'sha256': BOOK_SHA256,
                 'source_url': BOOK_URL, 'page_count': 175, 'byte_length': 8525325},
        'renderer': {'name': 'PyMuPDF', 'version': RENDERER_VERSION, 'scale': 1.75},
        'pages': assets, 'lessons': lessons,
        'notes': 'Publisher-linked personal companion. Complete recordings stay with the unchanged book and rendered book pages; never isolated quiz clips. No redistribution permission or independent pronunciation certification is asserted.',
    }


def restore(index, tracks, book, document, root=ROOT):
    if index.get('distribution_scope') != 'local_only' or index.get('rights_status') != 'personal_companion_only':
        raise ValueError('Glossika companion must remain personal/local-only')
    write_asset(root, index['book']['audio_path'], book, index['book']['sha256'])
    for row in index['lessons']:
        if row.get('quiz_eligible') is not False or row.get('recording_type') != 'book_lesson':
            raise ValueError('Complete book lessons cannot be quiz candidates')
        write_asset(root, row['audio_path'], tracks[row['archive_member']], row['sha256'])
    for row in index['pages']:
        target = asset_target(root, row['audio_path'])
        if target.is_file() and digest(target.read_bytes()) == row['sha256']:
            continue
        write_asset(root, row['audio_path'], page_bytes(document, row['pdf_page']), row['sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index-source', action='store_true')
    parser.add_argument('--mapping', type=Path, help='Explicit original-book track/page mapping CSV for initial indexing')
    args = parser.parse_args()
    cache = ROOT / 'imports/glossika'
    archive = fetch_pinned(ARCHIVE_URL, cache / 'Glossika Tone Training.zip', ARCHIVE_SHA256)
    book = fetch_pinned(BOOK_URL, cache / 'original.pdf', BOOK_SHA256)
    tracks, document = archive_tracks(archive), open_book(book)
    try:
        if args.index_source:
            if not args.mapping:
                parser.error('--index-source requires --mapping from the original book')
            with args.mapping.open(encoding='utf-8', newline='') as stream:
                index = build_index(tracks, document, list(csv.DictReader(stream)))
        else:
            index = json.loads(INDEX.read_text(encoding='utf-8'))
        restore(index, tracks, book, document)
        if args.index_source:
            INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    finally:
        document.close()
    print(json.dumps({'full_lessons': len(index['lessons']), 'book_pages': len(index['pages']),
                      'standalone_quiz_items_added': 0, 'distribution_scope': 'local_only'}))


if __name__ == '__main__':
    main()
