import importlib.util
import json
import math
import struct
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / 'scripts' / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


add_pinyin = load_script('add_pinyin_syllables.py')
build_hsk = load_script('build_hsk_data.py')
download_syllables = load_script('download_audio_cmn_syllables.py')
add_definitions = load_script('add_definitions.py')
check_syllables = load_script('check_audio_cmn_syllables.py')
download_public_pinyin = load_script('download_public_pinyin_syllables.py')


class PinyinSegmentationTests(unittest.TestCase):
    def test_avoids_unmarked_vowel_initial_boundary(self):
        vocab = ['neng', 'eng', 'ken', 'ke']
        item = {'pinyin': 'kěnéng', 'lexical_tones': [3, 2]}
        self.assertEqual(add_pinyin.split(item, vocab), ['ke', 'neng'])

    def test_respects_apostrophe_boundary(self):
        vocab = ['fang', 'gan', 'an', 'fan']
        item = {'pinyin': "fāng'àn", 'lexical_tones': [1, 4]}
        self.assertEqual(add_pinyin.split(item, vocab), ['fang', 'an'])

    def test_handles_erhua_and_annotations(self):
        vocab = ['wan', 'hao', 'xie', 'you', 'r']
        erhua = {'pinyin': 'hǎowánr', 'lexical_tones': [3, 2, 0]}
        annotated = {'pinyin': 'yǒu(yī)xiē', 'lexical_tones': [3, 1]}
        self.assertEqual(add_pinyin.split(erhua, vocab), ['hao', 'wan', 'r'])
        self.assertEqual(add_pinyin.split(annotated, vocab), ['you', 'xie'])


class PipelineValidationTests(unittest.TestCase):
    def test_unaligned_hanzi_and_tones_require_review(self):
        _, _, aligned = build_hsk.sandhi_surface('妈妈', [1])
        self.assertFalse(aligned)

    def test_mp3_header_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / 'valid.mp3'
            invalid = Path(directory) / 'invalid.mp3'
            valid.write_bytes(b'ID3audio')
            invalid.write_bytes(b'<html>')
            self.assertTrue(download_syllables.valid_mp3(valid))
            self.assertFalse(download_syllables.valid_mp3(invalid))

    def test_selects_matching_common_cedict_definition(self):
        item = {
            'word': '新',
            'traditional': '新',
            'pinyin_syllables': ['xin'],
            'lexical_tones': [1],
        }
        entries = add_definitions.parse_cedict(
            '新 新 [Xin1] /surname Xin/\n'
            '新 新 [xin1] /new/newly/CL:個|个[ge4]/\n'
        )
        self.assertEqual(
            add_definitions.choose_definition(item, entries['新']),
            'new; newly',
        )

    def test_rejects_definition_for_a_different_reading(self):
        item = {
            'word': '行',
            'traditional': '行',
            'pinyin_syllables': ['xing'],
            'lexical_tones': [2],
        }
        entries = add_definitions.parse_cedict('行 行 [hang2] /row/profession/\n')
        self.assertEqual(add_definitions.choose_definition(item, entries['行']), '')

    def test_measures_leading_and_trailing_audio_buffers(self):
        sample_rate = 16000
        samples = [0] * 1600
        samples += [
            round(12000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            for index in range(3200)
        ]
        samples += [0] * 2400
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'buffered.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(struct.pack(f'<{len(samples)}h', *samples))
            lead, tail, _ = check_syllables.edge_buffers(path)
        self.assertAlmostEqual(lead, 0.10, places=2)
        self.assertAlmostEqual(tail, 0.15, places=2)

    def test_correction_stops_existing_audio_before_decode(self):
        source = (ROOT / 'app' / 'app.js').read_text(encoding='utf-8')
        function = source.split('async function playPinyinSequence', 1)[1].split(
            'async function playCorrection', 1
        )[0]
        self.assertLess(function.index('stopNative();'), function.index('await pinyinSequenceBuffer'))
        self.assertLess(function.index('stopCorrection();'), function.index('await pinyinSequenceBuffer'))
        correction = source.split('async function playCorrection', 1)[1].split(
            "$('play').onclick", 1
        )[0]
        self.assertLess(correction.index("tone==='N'"), correction.index('playPinyinKey'))
        native = source.split('function playNative', 1)[1].split(
            'function correctionKey', 1
        )[0]
        self.assertLess(native.index('stopCorrection();'), native.index('new Audio'))
        correction_url = source.split('function correctionSelection', 1)[1].split(
            'function getCorrectionContext', 1
        )[0]
        self.assertIn('correctionRecordings[key]', correction_url)
        self.assertIn('audio/audio_cmn/syllabs', correction_url)
        self.assertIn("clarity.type='highshelf'", source)
        self.assertIn("presence.type='peaking'", source)
        native_selection = source.split('function nativePlayback', 1)[1].split(
            'function hasAlignedCorrections', 1
        )[0]
        self.assertNotIn('correctionRecordings', native_selection)
        self.assertIn('audioURL(r)', native_selection)

    def test_public_pinyin_archive_only_indexes_toned_mp3_files(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / 'corpus.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('repo/mp3/ma2.mp3', b'ID3audio')
                archive.writestr('repo/mp3/tian3.mp3', b'ID3audio')
                archive.writestr('repo/mp3/README.txt', b'ignored')
                archive.writestr('repo/other/pin1.mp3', b'ignored')
            with zipfile.ZipFile(archive_path) as archive:
                members=download_public_pinyin.corpus_members(archive)
        self.assertEqual(set(members), {'ma2','tian3'})

    def test_bad_human_syllables_have_explicit_fallback_policy(self):
        quality = json.loads(
            (ROOT / 'data' / 'audio_cmn_syllable_quality.json').read_text(
                encoding='utf-8'
            )
        )
        self.assertEqual(quality['ma2']['replacement'], 'pinyin_public')
        self.assertIsNone(quality['mou3']['replacement'])


if __name__ == '__main__':
    unittest.main()
