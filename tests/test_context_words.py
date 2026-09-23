import importlib.util
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
    import build_context_words as context_words


class ContextWordTests(unittest.TestCase):
    inventory = ['xue', 'xiao', 'sheng', 'yin', 'hang', 'xi', 'an', 'xian', 'ni', 'hao', 'kan', 'shu', 'bu']

    def test_word_and_character_readings_preserve_explicit_syllables_and_tones(self):
        token = {'hanzi': '小学生', 'pinyin': 'xiǎoxuéshēng'}
        parsed = context_words.parse_token(token, self.inventory)
        self.assertEqual(parsed['pinyin_syllables'], ['xiao', 'xue', 'sheng'])
        self.assertEqual(parsed['lexical_tones'], [3, 2, 1])
        rows = [{'id': 's1', 'tokens': [token]}]
        words, rejected = context_words.vocabulary(rows, [], self.inventory)
        self.assertEqual({word['word'] for word in words}, {'小学生', '小', '学', '生'})
        self.assertEqual(rejected, {})
        self.assertEqual(len({word['id'] for word in words}), 4)

    def test_hsk_readings_are_reused_not_duplicated(self):
        token = {'hanzi': '你好', 'pinyin': 'nǐ hǎo'}
        existing = {'id': 'known', 'word': '你好', 'pinyin_syllables': ['ni', 'hao'], 'lexical_tones': [3, 3]}
        words, _ = context_words.vocabulary([{'id': 's1', 'tokens': [token]}], [existing], self.inventory)
        self.assertNotIn('你好', [word['word'] for word in words])

    def test_context_spans_include_long_words_and_their_characters(self):
        row = {'tokens': [{'hanzi': '小学生', 'pinyin': 'xiǎoxuéshēng'}, {'hanzi': '看书', 'pinyin': 'kànshū'}]}
        words, _ = context_words.vocabulary([{'id': 's1', **row}], [], self.inventory)
        mapping = {context_words.reading_key(word): word['id'] for word in words}
        spans = context_words.spans_for(row, mapping, self.inventory)
        long = next(span for span in spans if span['word'] == '小学生')
        self.assertEqual((long['character_start'], long['character_end']), (0, 3))
        self.assertEqual(long['surface_pattern'], '3-2-1')
        char = next(span for span in spans if span['word'] == '学')
        self.assertEqual((char['character_start'], char['character_end'], char['surface_pattern']), (1, 2, '2'))

    def test_third_tone_sandhi_is_computed_in_the_source_context(self):
        row = {'tokens': [{'hanzi': '你', 'pinyin': 'nǐ'}, {'hanzi': '好', 'pinyin': 'hǎo'}]}
        words, _ = context_words.vocabulary([{'id': 's1', **row}], [], self.inventory)
        mapping = {context_words.reading_key(word): word['id'] for word in words}
        spans = context_words.spans_for(row, mapping, self.inventory)
        self.assertEqual(next(span for span in spans if span['word'] == '你')['surface_pattern'], '2')

    def test_punctuation_prevents_sandhi_from_crossing_a_phrase_boundary(self):
        row = {'tokens': [{'hanzi': '你', 'pinyin': 'nǐ'}, {'hanzi': '，', 'pinyin': ''},
                          {'hanzi': '好', 'pinyin': 'hǎo'}]}
        words, _ = context_words.vocabulary([{'id': 's1', **row}], [], self.inventory)
        mapping = {context_words.reading_key(word): word['id'] for word in words}
        spans = context_words.spans_for(row, mapping, self.inventory)
        self.assertEqual(next(span for span in spans if span['word'] == '你')['surface_pattern'], '3')
        self.assertEqual(next(span for span in spans if span['word'] == '好')['surface_pattern'], '3')

    def test_sentence_identity_and_timestamp_count_are_both_required(self):
        row = {'tokens': [{'hanzi': '你好', 'pinyin': 'nǐhǎo'}, {'hanzi': '。', 'pinyin': ''}]}
        evidence = {'source_text': '你好', 'recognized_text': '你好', 'transcript': '你好', 'exact_transcript': True,
                    'timestamps_ms': [[10, 200], [220, 450]]}
        self.assertEqual(context_words.validated_timestamps(row, evidence, .5), evidence['timestamps_ms'])
        for edit in [
            {'recognized_text': '你们'}, {'source_text': '你们'}, {'transcript': 'Hello 你好'},
            {'timestamps_ms': [[10, 200]]}, {'timestamps_ms': [[10, 300], [200, 450]]},
            {'timestamps_ms': [[10, 200], [220, 800]]},
        ]:
            self.assertIsNone(context_words.validated_timestamps(row, {**evidence, **edit}, .5))

    def test_cropping_is_exact_pcm_not_a_tone_change_or_whole_sentence(self):
        pcm = b''.join(value.to_bytes(2, 'little', signed=True) for value in range(1000))
        payload = context_words.crop_wave(pcm, 100, 700)
        with wave.open(io.BytesIO(payload), 'rb') as source:
            self.assertEqual(source.getframerate(), 16000)
            self.assertEqual(source.getnframes(), 600)
            self.assertEqual(source.readframes(600), pcm[200:1400])
        for first, last in [(-1, 10), (10, 5), (0, 1001)]:
            with self.assertRaises(ValueError):
                context_words.crop_wave(pcm, first, last)

    def test_decoder_resolves_a_symlinked_workspace_without_allowing_escape(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / 'real'
            (root / 'audio').mkdir(parents=True)
            (root / 'audio/source.mp3').write_bytes(b'ID3test')
            linked = parent / 'linked'
            linked.symlink_to(root, target_is_directory=True)
            with patch.object(context_words, 'ROOT', linked), patch.object(
                context_words.subprocess, 'run', return_value=SimpleNamespace(stdout=b'pcm')
            ) as run:
                self.assertEqual(context_words.decode_pcm('audio/source.mp3'), b'pcm')
                self.assertEqual(run.call_count, 1)
                with self.assertRaises(ValueError):
                    context_words.decode_pcm('audio/../../outside.mp3')

    def test_unalignable_mixed_scripts_are_not_guessed(self):
        self.assertIsNone(context_words.parse_token({'hanzi': 'T恤', 'pinyin': 'T-xù'}, self.inventory))
        self.assertIsNone(context_words.parse_token({'hanzi': '你好', 'pinyin': 'nǐ'}, self.inventory))

    def test_generated_vocabulary_has_consistent_readings(self):
        words = json.loads((ROOT / 'data/mandarin_native_words.json').read_text())['words']
        for word in words:
            self.assertEqual(len(word['word']), len(word['pinyin_syllables']))
            self.assertEqual(len(word['lexical_tones']), len(word['pinyin_syllables']))
            self.assertEqual(word['lexical_pattern'], context_words.pattern(word['lexical_tones']))
            self.assertEqual(word['default_surface_pattern'], context_words.pattern(word['default_surface_tones']))
