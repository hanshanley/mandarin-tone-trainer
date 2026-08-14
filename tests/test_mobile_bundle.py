import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIO_ROOT = ROOT / 'audio'


@unittest.skipUnless(AUDIO_ROOT.exists(), 'downloaded audio is not available')
class MobileBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(
            ['node', 'scripts/build_mobile_assets.mjs'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.bundle = ROOT / 'www'

    def test_contains_app_and_runtime_data(self):
        for relative_path in [
            'index.html',
            'style.css',
            'app.js',
            'data/hsk_words.json',
            'data/definitions.json',
            'data/recordings.json',
            'data/pinyin_public_recordings.json',
            'data/audio_cmn_syllable_quality.json',
        ]:
            path = self.bundle / relative_path
            self.assertTrue(path.is_file(), relative_path)
            self.assertGreater(path.stat().st_size, 0, relative_path)

    def test_contains_every_indexed_word_recording(self):
        recordings = json.loads(
            (ROOT / 'data' / 'recordings.json').read_text(encoding='utf-8')
        )
        missing = [
            recording['audio_path']
            for recording in recordings
            if not (self.bundle / recording['audio_path']).is_file()
        ]
        self.assertEqual(missing, [])

    def test_copies_the_complete_audio_tree(self):
        source = sorted(
            path.relative_to(AUDIO_ROOT)
            for path in AUDIO_ROOT.rglob('*.mp3')
        )
        bundled = sorted(
            path.relative_to(self.bundle / 'audio')
            for path in (self.bundle / 'audio').rglob('*.mp3')
        )
        self.assertEqual(bundled, source)


if __name__ == '__main__':
    unittest.main()
