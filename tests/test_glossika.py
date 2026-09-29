import importlib.util
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('download_glossika', ROOT / 'scripts/download_glossika.py')
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


class GlossikaTests(unittest.TestCase):
    def test_catalog_keeps_full_lessons_and_original_book_together(self):
        index = json.loads((ROOT / 'data/glossika_recordings.json').read_text())
        self.assertEqual(index['archive_sha256'], downloader.ARCHIVE_SHA256)
        self.assertEqual(index['book']['sha256'], downloader.BOOK_SHA256)
        self.assertEqual(index['distribution_scope'], 'local_only')
        self.assertEqual(index['license'], 'All rights reserved')
        self.assertEqual(index['rights_status'], 'personal_companion_only')
        self.assertEqual({row['archive_member'] for row in index['lessons']}, downloader.expected_members())
        self.assertEqual(len(index['lessons']), 117)
        pages = {row['printed_page']: row for row in index['pages']}
        self.assertEqual(pages[4]['pdf_page'], 5)
        self.assertEqual(pages[34]['pdf_page'], 36)
        self.assertEqual(pages[78]['pdf_page'], 81)
        for row in index['lessons']:
            self.assertIs(row['quiz_eligible'], False)
            self.assertEqual(row['recording_type'], 'book_lesson')
            self.assertTrue(row['printed_pages'])
            self.assertTrue(all(number in pages for number in row['printed_pages']))
            self.assertTrue(row['page_mapping_basis'])

    def test_pinned_archive_and_exact_member_set_are_required(self):
        with self.assertRaisesRegex(ValueError, 'pinned source'):
            downloader.archive_tracks(b'changed archive')
        for bad in ('../Tone 11.mp3', 'duplicate', 'symlink', 'bad-payload'):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                for name in sorted(downloader.expected_members()):
                    member = name
                    payload = b'ID3' + b'\0' * 200
                    if name == 'Tone 11.mp3':
                        if bad == 'duplicate':
                            continue
                        if bad == 'symlink':
                            member = zipfile.ZipInfo(name)
                            member.create_system = 3
                            member.external_attr = (stat.S_IFLNK | 0o777) << 16
                        elif bad == 'bad-payload':
                            payload = b'<html>' + b'x' * 200
                        else:
                            member = bad
                    archive.writestr(member, payload)
            payload = stream.getvalue()
            with self.subTest(case=bad), patch.object(
                downloader, 'ARCHIVE_SHA256', downloader.digest(payload)
            ), self.assertRaises(ValueError):
                downloader.archive_tracks(payload)

    def test_asset_restoration_is_idempotent_and_never_overwrites_changed_files(self):
        payload = b'ID3' + b'\0' * 200
        name = 'audio/glossika/lessons/tone-11.mp3'
        digest = downloader.digest(payload)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloader.write_asset(root, name, payload, digest)
            downloader.write_asset(root, name, payload, digest)
            self.assertEqual((root / name).read_bytes(), payload)
            (root / name).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Existing Glossika asset changed'):
                downloader.write_asset(root, name, payload, digest)
            for unsafe in ['../outside.mp3', 'audio/glossika/../../outside.mp3', '/audio/glossika/book.pdf']:
                with self.subTest(path=unsafe), self.assertRaises(ValueError):
                    downloader.write_asset(root, unsafe, payload, digest)
            with self.assertRaisesRegex(ValueError, 'source hash'):
                downloader.write_asset(root, 'audio/glossika/lessons/tone-12.mp3', payload, '0' * 64)
            with tempfile.TemporaryDirectory() as other:
                (root / 'audio/glossika/escape').symlink_to(other, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'escapes repository'):
                    downloader.asset_target(root, 'audio/glossika/escape/book.pdf')

    def test_duplicate_publisher_frontmatter_page_does_not_shift_lesson_pages(self):
        class Page:
            rect = SimpleNamespace(height=840, width=600)

            def __init__(self, number, text):
                self.number, self.text = number, text

            def get_text(self, kind='text'):
                return [(294, 790, 306, 805, str(self.number))] if kind == 'words' else self.text

        book = [Page(3, 'About Glossika'), Page(3, 'Introduction ISBN'), Page(4, 'Consonants')]
        self.assertEqual(downloader.printed_pages(book), {3: 2, 4: 3})
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            downloader.printed_pages([Page(4, 'Consonants'), Page(4, 'Other')])

    def test_lesson_restore_rejects_quiz_admission_and_distribution_scope_changes(self):
        row = {'archive_member': 'Tone 11.mp3', 'audio_path': 'audio/glossika/lessons/tone-11.mp3',
               'recording_type': 'book_lesson', 'quiz_eligible': True, 'sha256': '0' * 64}
        book = b'original book'
        index = {'distribution_scope': 'local_only', 'rights_status': 'personal_companion_only',
                 'book': {'audio_path': 'audio/glossika/book.pdf', 'sha256': downloader.digest(book)},
                 'lessons': [row], 'pages': []}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'cannot be quiz'):
                downloader.restore(index, {}, book, None, Path(directory))
            with self.assertRaisesRegex(ValueError, 'personal/local-only'):
                downloader.restore({**index, 'distribution_scope': 'redistributable'}, {}, book, None, Path(directory))


if __name__ == '__main__':
    unittest.main()
