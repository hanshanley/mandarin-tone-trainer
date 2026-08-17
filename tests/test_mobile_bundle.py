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
            'correction_audio.js',
            'app.js',
            'data/hsk_words.json',
            'data/definitions.json',
            'data/recordings.json',
            'data/pinyin_public_recordings.json',
            'data/correction_audio_quality.json',
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

    def test_copies_only_runtime_audio(self):
        recordings = json.loads(
            (ROOT / 'data' / 'recordings.json').read_text(encoding='utf-8')
        )
        corrections = json.loads(
            (ROOT / 'data' / 'pinyin_public_recordings.json').read_text(
                encoding='utf-8'
            )
        )
        quality = json.loads(
            (ROOT / 'data' / 'correction_audio_quality.json').read_text(
                encoding='utf-8'
            )
        )
        words = json.loads(
            (ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8')
        )
        required_corrections = set()
        for word in words:
            syllables = word.get('pinyin_syllables') or []
            required_corrections.update(
                f"{pinyin.replace('ü', 'v')}{tone}"
                for pinyin in syllables
                for tone in range(1, 5)
            )
        selected_public = {
            Path(corrections[key]['audio_path'])
            for key in required_corrections
            if key in corrections
            and quality['pinyin_public'].get(key, {}).get('status') != 'bad'
            and not (
                quality['audio_cmn'].get(key, {}).get('status') == 'bad'
                and quality['audio_cmn'][key].get('replacement') is None
            )
        }
        expected = {Path(recording['audio_path']) for recording in recordings}
        expected.update(
            path.relative_to(ROOT)
            for path in (AUDIO_ROOT / 'audio_cmn' / 'syllabs').glob('*.mp3')
        )
        expected.update(selected_public)
        bundled = {
            path.relative_to(self.bundle)
            for path in (self.bundle / 'audio').rglob('*.mp3')
        }
        self.assertEqual(bundled, expected)

        unreachable_fallbacks = {
            Path(recording['audio_path'])
            for key, recording in corrections.items()
            if Path(recording['audio_path']) not in selected_public
        }
        self.assertTrue(unreachable_fallbacks.isdisjoint(bundled))


if __name__ == '__main__':
    unittest.main()
