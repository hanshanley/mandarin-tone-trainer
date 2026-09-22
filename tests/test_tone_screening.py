import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / 'scripts/audit_correction_tones.py'
spec = importlib.util.spec_from_file_location('tone_screen', PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'optional audio audit dependencies unavailable')
class ToneScreeningTests(unittest.TestCase):
    def curves(self, values):
        return {method: values for method in audit.METHODS}

    def test_third_tone_rising_only_is_flagged_not_approved(self):
        result = audit.assess('zhai3', self.curves([-4, -3, -2, -1, 0, 1, 2, 3, 4]))
        self.assertEqual(result['status'], 'review')
        self.assertIn('tone 3', result['reason'])

    def test_dipping_and_low_thirds_are_not_forced_into_rising_labels(self):
        for values in ([2, 1, 0, -2, -4, -2, 0, 2, 3], [0] * 9):
            result = audit.assess('ma3', self.curves(values))
            self.assertEqual(result['status'], 'screened_only')
            self.assertIn('listening approval still required', result['reason'])

    def test_missing_nan_or_short_tracks_cannot_pass_silently(self):
        for invalid in (None, [], [0], [float('nan')] * 9):
            curves = self.curves([0] * 9)
            curves['pyin'] = invalid
            result = audit.assess('zhai3', curves)
            self.assertEqual(result['status'], 'review')
            self.assertIn('pyin', result['reason'])

    def test_disagreement_between_trackers_is_not_a_pass(self):
        curves = self.curves([-5, -4, -3, -2, 0, 2, 3, 4, 5])
        curves['praat'] = [5, 4, 3, 2, 0, -2, -3, -4, -5]
        result = audit.assess('ma3', curves)
        self.assertEqual(result['status'], 'review')
        self.assertIn('disagree', result['reason'])

    def test_other_tones_retain_strong_contradiction_checks(self):
        for key, curve in [
            ('ma1', [-5, -4, -3, -2, 0, 2, 3, 4, 5]),
            ('ma2', [0] * 9),
            ('ma2', [5, 4, 3, 2, 0, -2, -3, -4, -5]),
            ('ma4', [0] * 9),
            ('ma4', [-5, -4, -3, -2, 0, 2, 3, 4, 5]),
        ]:
            self.assertEqual(audit.assess(key, self.curves(curve))['status'], 'review')
