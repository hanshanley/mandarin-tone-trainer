import copy
import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
    spec = importlib.util.spec_from_file_location(
        'download_sinosplice', ROOT / 'scripts/download_sinosplice.py'
    )
    downloader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(downloader)


def word(identifier, text, bases, tones):
    return {'id': identifier, 'word': text, 'pinyin_syllables': bases,
            'lexical_pattern': tones}


def archive_with(member, payload=b'ID3' + b'\0' * 200):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(member, payload)
        archive.writestr('Sinosplice Tone Pair Drills/README.TXT', 'Attribution')
        archive.writestr('Sinosplice Tone Pair Drills/Audio/NOTES.TXT', 'Notes')
    return output.getvalue()


class SinospliceTests(unittest.TestCase):
    def test_nested_chart_cells_preserve_homophone_labels_and_explicit_alternative(self):
        prefix = 'https://www.sinosplice.com/wp-content/uploads/tone-pair-drills/'
        chart = (
            '<table><tr><td><table><tr><td>他</td><td>她</td><td>它</td></tr>'
            '<tr>' + ''.join(f'<td><a href="{prefix}ta1.mp3">audio</a></td>' for _ in range(3))
            + '</tr></table></td><td><table><tr><td>暖和</td></tr>'
            f'<tr><td><a href="{prefix}nuan3he0.mp3">audio</a></td></tr>'
            '</table></td></tr></table>'
        )
        labels = downloader.chart_labels(chart, '<td>nuan he (nuan huo)</td>')
        self.assertEqual(labels['ta1'], ['他', '她', '它'])
        self.assertEqual(labels['nuan3huo0'], ['暖和'])
        self.assertNotIn('nuan3huo0', downloader.chart_labels(chart, 'nuan he'))

    def test_misaligned_or_foreign_chart_links_are_rejected(self):
        prefix = 'https://www.sinosplice.com/wp-content/uploads/tone-pair-drills/'
        for chart in [
            f'<td>他 她<a href="{prefix}ta1.mp3">audio</a></td>',
            '<td>他<a href="https://example.com/ta1.mp3">audio</a></td>',
            '<td>No source labels</td>',
        ]:
            with self.subTest(chart=chart), self.assertRaises(ValueError):
                downloader.chart_labels(chart, '')

    def test_numbered_readings_require_the_entire_source_key(self):
        self.assertEqual(downloader.numbered_reading('nuan3huo0'), (['nuan', 'huo'], [3, 0]))
        for key in ['', 'hao', 'hao5', 'hao3.mp3', '../hao3', 'hao3x', 'hao3 dong3']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                downloader.numbered_reading(key)

    def test_original_third_tones_are_retained_separately_from_spoken_sandhi(self):
        row = word('easy', '好懂', ['hao', 'dong'], '3-3')
        row['default_surface_pattern'] = '2-3'
        self.assertEqual(
            downloader.mapping_for('hao3dong3', ['好懂'], [row]),
            (['hao', 'dong'], '3-3', '2-3', ['easy'], False),
        )

    def test_explicit_neutral_does_not_map_unrelated_words_or_readings(self):
        words = [
            word('warm', '暖和', ['nuan', 'huo'], '3-2'),
            word('other-reading', '暖和', ['nuan', 'he'], '3-2'),
            word('unrelated', '同音', ['nuan', 'huo'], '3-2'),
            word('malformed', '暖和', ['nuan', 'huo'], '3'),
        ]
        self.assertEqual(
            downloader.mapping_for('nuan3huo0', ['暖和'], words),
            (['nuan', 'huo'], '3-N', '3-N', ['warm'], False),
        )

    def test_sandhi_modifiers_are_comparison_only(self):
        for key, text in [('bu2', '不'), ('hen2', '很'), ('ting2', '挺')]:
            with self.subTest(key=key):
                result = downloader.mapping_for(key, [text], [word('match', text, [key[:-1]], '2')])
                self.assertEqual(result[3], [])
                self.assertIs(result[4], True)

    def test_unpinned_archive_is_rejected_before_extraction(self):
        with self.assertRaisesRegex(ValueError, 'pinned source'):
            downloader.read_archive(b'not the source archive')

    def test_unsafe_members_and_unexpected_audio_are_rejected_even_with_matching_hash(self):
        symlink = zipfile.ZipInfo('Sinosplice Tone Pair Drills/Audio/1-Char Adj/hao3.mp3')
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for member in [
            '../hao3.mp3', '/hao3.mp3', 'Audio\\hao3.mp3', symlink,
            'Sinosplice Tone Pair Drills/Audio/Other/hao3.mp3',
        ]:
            payload = archive_with(member)
            with self.subTest(member=member), patch.object(
                downloader, 'ARCHIVE_SHA256', downloader.digest(payload)
            ), self.assertRaises(ValueError):
                downloader.read_archive(payload)

    def test_archive_size_payload_and_track_count_are_checked(self):
        name = 'Sinosplice Tone Pair Drills/Audio/1-Char Adj/hao3.mp3'
        for content, limit, message in [
            (b'ID3' + b'\0' * 200, 20, 'size limit'),
            (b'<html>' + b'x' * 200, downloader.MAX_BYTES, 'Invalid archive MP3'),
            (b'ID3' + b'\0' * 200, downloader.MAX_BYTES, 'exactly 90'),
        ]:
            payload = archive_with(name, content)
            with self.subTest(message=message), patch.object(
                downloader, 'ARCHIVE_SHA256', downloader.digest(payload)
            ), patch.object(downloader, 'MAX_BYTES', limit), self.assertRaisesRegex(ValueError, message):
                downloader.read_archive(payload)

    def test_restore_is_idempotent_and_preserves_approval_state(self):
        payload = b'ID3' + b'\0' * 200
        member = 'Sinosplice Tone Pair Drills/Audio/1-Char Adj/hao3.mp3'
        record = {'archive_member': member, 'audio_path': 'audio/sinosplice/adjectives-1/hao3.mp3',
                  'sha256': downloader.digest(payload), 'review_status': 'pending', 'quiz_eligible': False}
        index = {'recordings': [record]}
        original = copy.deepcopy(index)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            downloader, 'read_archive', return_value=([(member, 'adjectives-1', 'hao3', payload)], {})
        ):
            root = Path(directory)
            downloader.restore(index, b'fixture', root)
            downloader.restore(index, b'fixture', root)
            self.assertEqual((root / record['audio_path']).read_bytes(), payload)
            self.assertEqual(index, original)
            (root / record['audio_path']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Existing Sinosplice file changed'):
                downloader.restore(index, b'fixture', root)
            for bad_path in ['audio/sinosplice/../../escape.mp3', '/audio/sinosplice/hao3.mp3']:
                with self.subTest(path=bad_path), self.assertRaises(ValueError):
                    downloader.restore({'recordings': [{**record, 'audio_path': bad_path}]}, b'fixture', root)
            with self.assertRaisesRegex(ValueError, 'hash changed'):
                downloader.restore({'recordings': [{**record, 'sha256': '0' * 64}]}, b'fixture', root)

    def test_committed_source_inventory_retains_rights_and_distinct_takes(self):
        index = json.loads((ROOT / 'data/sinosplice_recordings.json').read_text(encoding='utf-8'))
        rows = index['recordings']
        self.assertEqual(index['archive_sha256'], downloader.ARCHIVE_SHA256)
        self.assertEqual(len(rows), 90)
        self.assertEqual(len({row['audio_path'] for row in rows}), 90)
        self.assertEqual(len({row['sha256'] for row in rows}), 90)
        self.assertEqual(sum(len(row['source_syllables']) == 1 for row in rows), 36)
        self.assertEqual(sum(len(row['source_syllables']) == 2 for row in rows), 54)
        takes = [row for row in rows if row['source_audio_key'] == 'te4bie2']
        self.assertEqual(len(takes), 2)
        self.assertNotEqual(takes[0]['sha256'], takes[1]['sha256'])
        for row in rows:
            self.assertEqual(row['source'], 'sinosplice')
            self.assertEqual(row['license'], 'CC-BY-NC-SA-2.5')
            self.assertEqual(row['creator'], 'John Pasden')
            self.assertEqual(row['speaker'], 'unknown')
            self.assertEqual(row['distribution_scope'], 'local_only')
            self.assertEqual(row['rights_status'], 'noncommercial_permitted')


if __name__ == '__main__':
    unittest.main()
