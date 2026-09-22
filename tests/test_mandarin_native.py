import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
    spec = importlib.util.spec_from_file_location(
        'download_mandarin_native', ROOT / 'scripts/download_mandarin_native.py'
    )
    downloader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(downloader)


class MandarinNativeTests(unittest.TestCase):
    def test_source_encoding_does_not_move_tone_numbers_to_syllable_ends(self):
        self.assertEqual(downloader.encoded_pinyin_key('bái'), 'ba2i')
        self.assertEqual(downloader.encoded_pinyin_key('nǚ ér'), 'nv3_e2r')
        self.assertEqual(downloader.encoded_pinyin_key('dǒnɡ'), 'do3ng')
        self.assertEqual(downloader.normalized_source_key("nǚ'ér"), 'nv3e2r')
        self.assertEqual(downloader.normalized_source_key('bu4_ke4qi'), 'bu4ke4qi')

    def test_untrusted_manifest_paths_and_duplicates_are_rejected(self):
        for payload in [[], {}, ['../bad'], ['a/b'], ['a\\b'], ['a?b'], ['a#b'], ['a\nb'], ['a', 'a']]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                downloader.validate_keys(payload)

    def test_filename_matches_are_unapproved_candidates_not_verified_words(self):
        words = [{'id': 'white', 'pinyin': 'bái'}, {'id': 'hundred', 'pinyin': 'bǎi'}]
        record = downloader.pending_recording('ba2i', words)
        self.assertEqual(record['candidate_hsk_ids'], ['white'])
        self.assertIsNone(record['word'])
        self.assertIsNone(record['surface_pattern'])
        self.assertIsNone(record['license'])
        self.assertIs(record['quiz_eligible'], False)
        self.assertEqual(record['rights_status'], 'unverified')
        self.assertEqual(record['review_status'], 'pending')
        self.assertEqual(downloader.pending_recording('unknown', words)['candidate_hsk_ids'], [])

    def test_downloads_resume_without_changing_metadata_or_approval_flags(self):
        payload = b'ID3' + b'\0' * 200
        record = downloader.pending_recording('ma1', [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(downloader, 'fetch_bytes', return_value=payload) as fetch:
                saved, downloaded = downloader.download_recording(record, root)
                self.assertTrue(downloaded)
                self.assertEqual(fetch.call_count, 1)
            with patch.object(downloader, 'fetch_bytes', side_effect=AssertionError('must use pinned cache')):
                restored, downloaded = downloader.download_recording(saved, root)
            self.assertFalse(downloaded)
            self.assertEqual(restored, saved)
            self.assertEqual(saved['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertFalse(saved['quiz_eligible'])

    def test_changed_upstream_audio_and_html_are_not_accepted(self):
        record = downloader.pending_recording('ma1', [])
        with tempfile.TemporaryDirectory() as directory:
            for payload in [b'<html>' + b'x' * 200, b'ID3' + b'x' * 200]:
                pinned = {**record, 'sha256': '0' * 64}
                with patch.object(downloader, 'fetch_bytes', return_value=payload), self.assertRaises(ValueError):
                    downloader.download_recording(pinned, Path(directory))
                self.assertFalse((Path(directory) / record['audio_path']).exists())

    def test_only_the_sites_normalization_can_resolve_a_stale_unicode_filename(self):
        record = downloader.pending_recording('dǒnɡ', [])
        failure = urllib.error.HTTPError(record['source_url'], 404, 'not found', {}, None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(downloader, 'fetch_bytes', side_effect=[failure, b'ID3' + b'\0' * 200]) as fetch:
                saved, _ = downloader.download_recording(record, Path(directory))
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(saved['resolved_audio_key'], 'do3ng')
            self.assertTrue(saved['source_url'].endswith('/do3ng.mp3'))
            self.assertEqual(saved['source_audio_key'], 'dǒnɡ')
            self.assertFalse(saved['quiz_eligible'])

    def test_forbidden_responses_are_not_bypassed_with_aliases(self):
        record = downloader.pending_recording('dǒnɡ', [])
        failure = urllib.error.HTTPError(record['source_url'], 403, 'forbidden', {}, None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(downloader, 'fetch_bytes', side_effect=failure) as fetch:
                with self.assertRaisesRegex(RuntimeError, '403'):
                    downloader.download_recording(record, Path(directory))
            self.assertEqual(fetch.call_count, 1)

    def test_index_preserves_every_listed_clip_and_records_content_hashes(self):
        index = json.loads((ROOT / 'data/mandarin_native_recordings.json').read_text())
        keys = [record['source_audio_key'] for record in index['recordings']]
        self.assertTrue(keys)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertLessEqual(set(index['upstream_manifest_keys']), set(keys))
        for record in index['recordings']:
            self.assertEqual(record['source'], 'mandarin_native')
            self.assertRegex(record['sha256'], r'^[a-f0-9]{64}$')
            self.assertGreater(record['byte_length'], 128)
            self.assertIsInstance(record['candidate_hsk_ids'], list)
