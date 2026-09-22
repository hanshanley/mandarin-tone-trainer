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
        cls.expected_audio = {
            Path(relative)
            for relative in json.loads(subprocess.check_output(
                ['node', '--input-type=module', '-e', """
import {loadReviewData,validateLedger,practiceInventory} from './scripts/review_audio.mjs';
const data=loadReviewData();
process.stdout.write(JSON.stringify([...practiceInventory(data,validateLedger(data)).audio]));
"""],
                cwd=ROOT,
                text=True,
            ))
        }

    def test_contains_app_and_runtime_data(self):
        for relative_path in [
            'index.html',
            'style.css',
            'correction_audio.js',
            'audio_review.js',
            'app.js',
            'data/hsk_words.json',
            'data/definitions.json',
            'data/recordings.json',
            'data/pinyin_public_recordings.json',
            'data/correction_audio_quality.json',
            'data/audio_reviews.json',
            'data/mandarin_native_recordings.json',
        ]:
            path = self.bundle / relative_path
            self.assertTrue(path.is_file(), relative_path)
            self.assertGreater(path.stat().st_size, 0, relative_path)

    def test_contains_every_reviewed_runtime_recording(self):
        for relative in self.expected_audio:
            self.assertEqual(
                (self.bundle / relative).read_bytes(),
                (ROOT / relative).read_bytes(),
            )

    def test_copies_only_runtime_audio(self):
        bundled = {
            path.relative_to(self.bundle)
            for path in (self.bundle / 'audio').rglob('*')
            if path.is_file()
        }
        self.assertEqual(bundled, self.expected_audio)
        ledger = json.loads((ROOT / 'data/audio_reviews.json').read_text())
        if not ledger['approvals']:
            self.assertEqual(bundled, set())


if __name__ == '__main__':
    unittest.main()
