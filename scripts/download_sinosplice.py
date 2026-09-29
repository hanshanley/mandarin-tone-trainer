#!/usr/bin/env python3
"""Restore licensed, intact Sinosplice Tone Pair Drills with exact source labels."""
import argparse
import hashlib
import io
import json
import re
import stat
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from build_hsk_data import sandhi_surface, pattern

ROOT = Path(__file__).resolve().parents[1]
PAGE = 'https://www.sinosplice.com/learn-chinese/tone-pair-drills'
ARCHIVE_URL = 'https://www.sinosplice.com/wp-content/uploads/files/sinosplice-tpd-10.zip'
ARCHIVE_SHA256 = '012db77929affde66f5c22edbeca3d1d2e19e8c4dec913f20afe0892a63c1af5'
LICENSE = 'CC-BY-NC-SA-2.5'
LICENSE_URL = 'https://creativecommons.org/licenses/by-nc-sa/2.5/'
INDEX = ROOT / 'data/sinosplice_recordings.json'
GROUPS = {'1-Char Adj': 'adjectives-1', '2-Char Adj': 'adjectives-2',
          'Modifiers': 'modifiers', 'Pronouns': 'pronouns'}
MAX_BYTES = 30 * 1024 * 1024


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'MandarinToneTrainer/1.0 (licensed educational audio)'})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('Sinosplice download exceeds its size limit')
    return payload


class ChartParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.cells = []
        self.text = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == 'td':
            self.stack.append({'text': [], 'audio': []})
        if tag == 'a' and attributes.get('href', '').lower().endswith('.mp3'):
            for cell in self.stack:
                cell['audio'].append(attributes['href'])

    def handle_data(self, data):
        self.text.append(data)
        for cell in self.stack:
            cell['text'].append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.stack:
            self.cells.append(self.stack.pop())
            for cell in self.stack:
                cell['text'].append(' ')


def chart_labels(characters, pinyin):
    parser = ChartParser()
    parser.feed(characters)
    labels = {}
    for cell in parser.cells:
        words = re.findall(r'[\u3400-\u9fff]+', ''.join(cell['text']))
        if not words or not cell['audio']:
            continue
        if len(words) != len(cell['audio']):
            raise ValueError('Source character/audio columns no longer align')
        for word, url in zip(words, cell['audio']):
            parsed = urllib.parse.urlparse(url)
            if parsed.hostname not in ('www.sinosplice.com', 'sinosplice.com') or not parsed.path.startswith('/wp-content/uploads/tone-pair-drills/'):
                raise ValueError('Unexpected audio source in Sinosplice chart')
            key = PurePosixPath(parsed.path).stem
            if word not in labels.setdefault(key, []):
                labels[key].append(word)
    pinyin_parser = ChartParser()
    pinyin_parser.feed(pinyin)
    # This alternative is explicitly named in the source pinyin chart.
    if 'nuan huo' in ' '.join(pinyin_parser.text) and 'nuan3he0' in labels:
        labels['nuan3huo0'] = list(labels['nuan3he0'])
    if not labels:
        raise ValueError('Sinosplice chart yielded no labeled recordings')
    return labels


def numbered_reading(key):
    tokens = re.findall(r'([a-zv]+)([0-4])', key)
    if not tokens or ''.join(base + tone for base, tone in tokens) != key:
        raise ValueError(f'Invalid numbered source pinyin: {key}')
    return [base for base, _ in tokens], [int(tone) for _, tone in tokens]


def read_archive(payload):
    if digest(payload) != ARCHIVE_SHA256:
        raise ValueError('Sinosplice archive differs from the pinned source')
    recordings = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if sum(info.file_size for info in archive.infolist()) > MAX_BYTES:
            raise ValueError('Uncompressed archive exceeds its size limit')
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Unsafe archive member')
            if name.suffix.lower() != '.mp3':
                continue
            if len(name.parts) != 4 or name.parts[:2] != ('Sinosplice Tone Pair Drills', 'Audio') or name.parts[2] not in GROUPS:
                raise ValueError('Unexpected MP3 archive location')
            numbered_reading(name.stem)
            data = archive.read(info)
            if len(data) < 128 or not data.startswith((b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')):
                raise ValueError('Invalid archive MP3')
            recordings.append((info.filename, GROUPS[name.parts[2]], name.stem, data))
        notes = {name: archive.read('Sinosplice Tone Pair Drills/' + name)
                 for name in ('README.TXT', 'Audio/NOTES.TXT')}
    if len(recordings) != 90:
        raise ValueError('Pinned archive must contain exactly 90 distinct MP3 tracks')
    return recordings, notes


def mapping_for(key, source_words, words):
    bases, tones = numbered_reading(key)
    mapped = []
    source_pattern = pattern(tones)
    # The chart marks these as sandhi demonstrations, not dictionary-tone prompts.
    comparison_only = key in ('bu2', 'hen2', 'ting2')
    surfaces = {pattern(sandhi_surface(word, tones)[0]) for word in source_words}
    if len(surfaces) != 1:
        raise ValueError('Ambiguous spoken pattern across source homophones')
    surface = surfaces.pop()
    for word in words:
        if comparison_only or re.sub(r'\d+$', '', word['word']) not in source_words:
            continue
        if [base.replace('ü', 'v') for base in word['pinyin_syllables']] != bases:
            continue
        expected = (word.get('default_surface_pattern') or word['lexical_pattern']).split('-')
        if len(expected) == len(tones) and all(
            supplied == actual or supplied == 'N'
            for supplied, actual in zip(surface.split('-'), expected)
        ):
            mapped.append(word['id'])
    return bases, source_pattern, surface, sorted(mapped), comparison_only


def restore(index, archive, root=ROOT):
    members = {name: payload for name, _, _, payload in read_archive(archive)[0]}
    for record in index['recordings']:
        payload = members[record['archive_member']]
        if digest(payload) != record['sha256']:
            raise ValueError('Pinned Sinosplice recording hash changed')
        relative = PurePosixPath(record['audio_path'])
        if not relative.as_posix().startswith('audio/sinosplice/') or '..' in relative.parts:
            raise ValueError('Invalid Sinosplice destination')
        target = root / relative
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError('Sinosplice audio destination escapes the repository')
        if target.exists():
            if digest(target.read_bytes()) != record['sha256']:
                raise ValueError(f'Existing Sinosplice file changed: {target}')
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--index-source', action='store_true', help='explicitly index the pinned source and current chart labels')
    args = parser.parse_args()
    imports = ROOT / 'imports/sinosplice'
    imports.mkdir(parents=True, exist_ok=True)
    archive_path = imports / 'sinosplice-tpd-10.zip'
    archive = archive_path.read_bytes() if archive_path.is_file() else fetch(ARCHIVE_URL)
    members, notes = read_archive(archive)
    if not archive_path.exists():
        archive_path.write_bytes(archive)
    if args.index_source:
        charts = {}
        for page in (3, 5):
            path = imports / f'chart-{page}.html'
            if not path.exists():
                path.write_bytes(fetch(f'{PAGE}/{page}/'))
            charts[page] = path.read_text(encoding='utf-8')
        labels = chart_labels(charts[3], charts[5])
        words = json.loads((ROOT / 'data/hsk_words.json').read_text())
        words += json.loads((ROOT / 'data/mandarin_native_words.json').read_text())['words']
        previous = json.loads(INDEX.read_text()) if INDEX.exists() else {'recordings': []}
        saved = {record['audio_path']: record for record in previous['recordings']}
        records = []
        for member, group, key, payload in members:
            if key not in labels:
                raise ValueError(f'Archive recording lacks a source chart label: {key}')
            bases, original, surface, ids, comparison_only = mapping_for(key, labels[key], words)
            relative = f'audio/sinosplice/{group}/{key}.mp3'
            record = {
                'source': 'sinosplice', 'word': None, 'source_words': labels[key],
                'source_audio_key': key, 'source_tone_pattern': original,
                'source_syllables': bases, 'surface_pattern': surface, 'candidate_hsk_ids': ids,
                'comparison_key': bases[0] + surface if len(bases) == 1 else None,
                'recording_type': 'isolated_tone' if comparison_only else 'word_candidate',
                'audio_path': relative, 'filename': key + '.mp3', 'archive_member': member,
                'sha256': digest(payload), 'byte_length': len(payload),
                'source_url': ARCHIVE_URL, 'source_page': PAGE,
                'source_recording_id': 'sinosplice/tpd-1.0/' + member.split('/Audio/', 1)[1],
                'creator': 'John Pasden', 'speaker': 'unknown', 'language_code': 'zh',
                'license': LICENSE, 'license_url': LICENSE_URL, 'rights_status': 'noncommercial_permitted',
                'distribution_scope': 'local_only', 'review_status': 'pending', 'quiz_eligible': False,
                'notes': 'Intact licensed recording; original source tone labels retained. Spoken sandhi is a hypothesis until screened. Noncommercial use only; attribution and ShareAlike terms apply.',
            }
            old = saved.get(relative)
            if old:
                if any(old.get(field) != record[field] for field in ('sha256', 'source_words', 'source_tone_pattern', 'surface_pattern')):
                    raise ValueError('Reindexing changed a previously imported recording or reading')
                for field in ('review_status', 'quiz_eligible', 'notes'):
                    record[field] = old[field]
            records.append(record)
        index = {
            'version': 1, 'source': 'sinosplice', 'title': 'Mandarin Chinese Tone Pair Drills',
            'creator': 'John Pasden', 'source_page': PAGE, 'archive_url': ARCHIVE_URL,
            'archive_sha256': ARCHIVE_SHA256, 'license': LICENSE, 'license_url': LICENSE_URL,
            'attribution': 'Mandarin Chinese Tone Pair Drills by John Pasden, Sinosplice.com. CC BY-NC-SA 2.5.',
            'chart_sha256': {str(page): digest(text.encode('utf-8')) for page, text in charts.items()},
            'recordings': records,
        }
        restore(index, archive)
        INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    else:
        if not INDEX.exists():
            parser.error('Create the source index explicitly with --index-source first')
        index = json.loads(INDEX.read_text())
        restore(index, archive)
    for name, payload in notes.items():
        target = imports / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    print(json.dumps({
        'files': len(index['recordings']),
        'single_syllable_files': sum(len(row['source_syllables']) == 1 for row in index['recordings']),
        'two_syllable_files': sum(len(row['source_syllables']) == 2 for row in index['recordings']),
        'mapped_files': sum(bool(row['candidate_hsk_ids']) for row in index['recordings']),
        'license': LICENSE, 'automatic_approvals_granted': 0,
    }))


if __name__ == '__main__':
    main()
