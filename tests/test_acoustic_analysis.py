import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in ('numpy', 'librosa', 'parselmouth', 'pyworld', 'scipy'))
if AVAILABLE:
    with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
        import acoustic_analysis as acoustic
    import numpy as np


@unittest.skipUnless(AVAILABLE, 'optional acoustic analysis dependencies unavailable')
class AcousticAnalysisTests(unittest.TestCase):
    def test_dc_and_low_frequency_contamination_are_removed_before_pitch_tracking(self):
        times = np.arange(16000) / 16000
        clean = .01 * np.sin(2 * np.pi * 240 * times)
        contaminated = clean + .1 + .02 * np.sin(2 * np.pi * 30 * times)
        prepared = acoustic.prepare_signal(contaminated)
        self.assertLess(abs(float(np.mean(prepared[1000:-1000]))), .0001)
        self.assertGreater(np.corrcoef(clean[1000:-1000], prepared[1000:-1000])[0, 1], .98)

    def test_second_and_third_tones_are_distinguished_without_reading_the_label(self):
        rising = np.geomspace(210, 330, 17)
        dipping = np.concatenate([np.geomspace(240, 140, 9), np.geomspace(145, 260, 8)])
        self.assertEqual(acoustic.classify_curve(rising, 340), '2')
        self.assertEqual(acoustic.classify_curve(dipping, 340), '3')
        measured = {'status': 'measured', 'curves': {m: rising.tolist() for m in acoustic.METHODS},
                    'start': 0, 'end': .5, 'voiced_seconds': .5}
        self.assertEqual(acoustic.decide(measured, '3', 340)['status'], 'review')
        self.assertEqual(acoustic.decide(measured, '2', 340)['status'], 'screened')

    def test_flat_pitch_without_register_evidence_is_not_certified(self):
        self.assertIsNone(acoustic.classify_curve([160] * 17))
        self.assertEqual(acoustic.classify_curve([310] * 17, 320), '1')
        self.assertEqual(acoustic.classify_curve([160] * 17, 320, connected=True), '3')

    def test_conflicting_trackers_do_not_vote_a_recording_into_practice(self):
        rising = np.geomspace(210, 330, 17).tolist()
        falling = rising[::-1]
        measured = {'status': 'measured', 'curves': {'pyin': rising, 'praat': rising, 'world': falling}}
        self.assertEqual(acoustic.decide(measured, '2', 340)['status'], 'review')

    def test_one_available_pitch_tracker_is_insufficient(self):
        profile = {'duration': .5, 'tracks': {
            'pyin': [200] * 51, 'praat': [None] * 51, 'world': [None] * 51,
        }}
        self.assertEqual(acoustic.segment(profile)['status'], 'review')
