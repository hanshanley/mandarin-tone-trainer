import importlib.util
import tempfile
import unittest
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


if __name__ == '__main__':
    unittest.main()
