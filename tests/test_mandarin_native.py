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

    def test_explore_vocabulary_uses_the_same_filter_as_the_site(self):
        rows = [
            {'id': 'first', 'audio': 'audios/你好.mp3', 'tokens': [
                {'hanzi': '你', 'pinyin': 'nǐ'}, {'hanzi': '好', 'pinyin': 'hǎo'},
                {'hanzi': '！', 'pinyin': ''}, {'hanzi': '你', 'pinyin': 'nǐ'},
            ]},
            {'id': 'second', 'audio': 'audios/你好.mp3', 'tokens': [
                {'hanzi': '好', 'pinyin': 'hào'},
            ]},
            {'id': 'third', 'audio': 'audios/我是.m4a', 'tokens': [
                {'hanzi': '我', 'pinyin': 'wǒ'}, {'hanzi': '是', 'pinyin': 'shì'},
            ]},
        ]
        recordings, vocabulary = downloader.explore_inventory(rows)
        self.assertEqual(len(recordings), 2)
        self.assertEqual(len(vocabulary), 4)
        self.assertEqual(recordings[0]['source_entry_ids'], ['first', 'second'])
        self.assertEqual(recordings[0]['candidate_hsk_ids'], [])
        self.assertEqual(recordings[0]['recording_type'], 'context_sentence')
        self.assertEqual(recordings[0]['context_words'], ['你', '好'])
        self.assertFalse(recordings[0]['quiz_eligible'])
        hao = next(entry for entry in vocabulary if entry['word'] == '好')
        self.assertEqual(set(hao['source_pinyin_labels']), {'hǎo', 'hào'})
        self.assertEqual(len(hao['context_recording_ids']), 1)
        self.assertTrue(recordings[1]['audio_path'].endswith('.m4a'))

    def test_context_paths_are_bounded_and_preserve_the_audio_container(self):
        paths = downloader.context_paths('audios/一个句子.m4a')
        self.assertTrue(paths['source_url'].endswith('.m4a'))
        self.assertRegex(paths['audio_path'], r'^audio/mandarin_native/context/[a-f0-9]{64}\.m4a$')
        for bad in ['https://example.com/file.mp3', 'audios/../secret.mp3', 'audios/folder/file.mp3', 'audios/file.wav', 'audios/file.mp3?query=1']:
            with self.subTest(reference=bad), self.assertRaises(ValueError):
                downloader.context_paths(bad)

    def test_m4a_download_is_not_mislabeled_as_mp3(self):
        recordings, _ = downloader.explore_inventory([{
            'id': 'one', 'audio': 'audios/一句话.m4a', 'tokens': [{'hanzi': '话', 'pinyin': 'huà'}],
        }])
        payload = b'\x00\x00\x00\x1cftypisom' + b'\0' * 200
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(downloader, 'fetch_bytes', return_value=payload):
                saved, downloaded = downloader.download_recording(recordings[0], Path(directory))
            self.assertTrue(downloaded)
            self.assertTrue(saved['audio_path'].endswith('.m4a'))
            self.assertEqual((Path(directory) / saved['audio_path']).read_bytes(), payload)
            self.assertFalse(saved['quiz_eligible'])
        self.assertFalse(downloader.valid_audio_payload(payload, '.mp3'))
        self.assertFalse(downloader.valid_audio_payload(b'ID3' + b'\0' * 200, '.m4a'))

    def test_malformed_explore_entries_are_not_silently_dropped(self):
        valid = {'id': 'one', 'audio': 'audios/one.mp3', 'tokens': [{'hanzi': '一', 'pinyin': 'yī'}]}
        for rows in [[], [valid, valid], [{**valid, 'tokens': None}], [{**valid, 'audio': ''}], [{**valid, 'tokens': [{'hanzi': '一', 'pinyin': 123}]}]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                downloader.explore_inventory(rows)

    def test_index_preserves_every_listed_clip_and_records_content_hashes(self):
        index = json.loads((ROOT / 'data/mandarin_native_recordings.json').read_text())
        identifiers = [record['source_recording_id'] for record in index['recordings']]
        keys = [record['source_audio_key'] for record in index['recordings'] if record['recording_type'] == 'word_candidate']
        self.assertTrue(identifiers)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertLessEqual(set(index['upstream_manifest_keys']), set(keys))
        contexts = [record for record in index['recordings'] if record['recording_type'] == 'context_sentence']
        self.assertLessEqual(set(index['upstream_context_references']), {record['source_audio_reference'] for record in contexts})
        context_ids = {record['source_recording_id'] for record in contexts}
        self.assertGreater(len(index['explore_vocabulary']), 4000)
        expected_links = {}
        for record in contexts:
            if record['source_audio_reference'] not in index['upstream_context_references']:
                continue
            for word in record['context_words']:
                expected_links.setdefault(word, set()).add(record['source_recording_id'])
        vocabulary_words = [entry['word'] for entry in index['explore_vocabulary']]
        self.assertEqual(len(vocabulary_words), len(set(vocabulary_words)))
        self.assertEqual(set(vocabulary_words), set(expected_links))
        for word in index['explore_vocabulary']:
            self.assertTrue(word['source_pinyin_labels'])
            self.assertTrue(word['context_recording_ids'])
            self.assertLessEqual(set(word['context_recording_ids']), context_ids)
            self.assertEqual(set(word['context_recording_ids']), expected_links[word['word']])
        for record in index['recordings']:
            self.assertEqual(record['source'], 'mandarin_native')
            self.assertRegex(record['sha256'], r'^[a-f0-9]{64}$')
            self.assertGreater(record['byte_length'], 128)
            self.assertIsInstance(record['candidate_hsk_ids'], list)
