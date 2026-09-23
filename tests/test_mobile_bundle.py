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
process.stdout.write(JSON.stringify([...practiceInventory(data,validateLedger(data,undefined,{allowLocalOnly:false})).audio]));
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
            'data/acoustic_reviews.json',
            'data/mandarin_native_recordings.json',
            'data/mandarin_native_words.json',
            'data/context_word_recordings.json',
        ]:
            path = self.bundle / relative_path
            self.assertTrue(path.is_file(), relative_path)
            self.assertGreater(path.stat().st_size, 0, relative_path)
            if relative_path == 'data/acoustic_reviews.json':
                continue
            source = ROOT / relative_path if relative_path.startswith('data/') else ROOT / 'app' / relative_path
            self.assertEqual(path.read_bytes(), source.read_bytes(), f'stale bundled {relative_path}')

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
        acoustic = json.loads((ROOT / 'data/acoustic_reviews.json').read_text())
        if not ledger['approvals'] and not acoustic['approvals']:
            self.assertEqual(bundled, set())

    def test_real_corpus_covers_five_tones_and_new_word_lengths(self):
        output = subprocess.check_output(
            ['node', '--input-type=module', '-e', """
import {loadReviewData,validateLedger,practiceInventory,requireToneCoverage} from './scripts/review_audio.mjs';
const data=loadReviewData(), index=validateLedger(data), inventory=practiceInventory(data,index);
requireToneCoverage(inventory);
requireToneCoverage(practiceInventory(data,validateLedger(data,undefined,{allowLocalOnly:false})));
const words=new Map(data.words.map(word=>[word.id,word]));
const recordings=new Map(data.recordings.map(recording=>[recording.audio_path,recording]));
const pairs=inventory.recordingLabelPairs.map(pair=>({word:words.get(pair.word_id),recording:recordings.get(pair.audio_path)}));
process.stdout.write(JSON.stringify({
  neutral:inventory.toneCoverage.N,
  newReadings:pairs.filter(pair=>pair.word.id.startsWith('MN-')).length,
  characterExcerpts:pairs.filter(pair=>pair.recording.recording_type==='aligned_word'&&pair.word.word.length===1).length,
  longExcerpts:pairs.filter(pair=>pair.recording.recording_type==='aligned_word'&&pair.word.word.length>2).length,
}));
"""],
            cwd=ROOT, text=True,
        )
        coverage = json.loads(output)
        for name, count in coverage.items():
            self.assertGreater(count, 0, name)


if __name__ == '__main__':
    unittest.main()
