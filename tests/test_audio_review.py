import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AudioReviewTests(unittest.TestCase):
    def test_runtime_listening_approval_gate(self):
        result = subprocess.run(
            ['node', '--test', 'tests/test_audio_review.cjs'],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
