import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in (
    'numpy', 'scipy', 'sklearn', 'librosa', 'parselmouth', 'pyworld', 'opencc', 'pypinyin',
))
if AVAILABLE:
    with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
        from refine_pronunciation_judge import fit_projection, pooled_representation, project
    import numpy as np


@unittest.skipUnless(AVAILABLE, 'optional judge dependencies unavailable')
class JudgeRefinementTests(unittest.TestCase):
    def test_encoder_states_are_pooled_at_the_original_reference_times(self):
        states = np.arange(20).reshape(10, 2)
        units = [{'start': 0, 'end': .3}, {'start': .3, 'end': .6}]
        result = pooled_representation(states, units, .06, .6)
        self.assertEqual(result.shape, (2, 4))
        np.testing.assert_allclose(result[0, :2], np.mean(states[:5], axis=0))
        np.testing.assert_allclose(result[1, :2], np.mean(states[5:], axis=0))

    def test_invalid_timestamps_and_rate_do_not_create_false_features(self):
        states = np.arange(20).reshape(10, 2)
        for units, rate, duration in [
            ([{'start': 0, 'end': .3}], .01, .6),
            ([{'start': -.1, 'end': .3}], .06, .6),
            ([{'start': .01, 'end': .02}], .06, .6),
            ([{'start': .5, 'end': .9}], .06, .6),
        ]:
            with self.assertRaises(ValueError):
                pooled_representation(states, units, rate, duration)

    def test_projection_is_fitted_on_training_rows_and_matches_saved_transform(self):
        random = np.random.default_rng(12)
        training = random.normal(size=(100, 12))
        mapping = fit_projection(training, 4)
        output = project(training, mapping)
        self.assertEqual(output.shape, (100, 4))
        self.assertTrue(np.isfinite(output).all())
        np.testing.assert_allclose(np.mean(output, axis=0), 0, atol=1e-8)
        expected = np.asarray(mapping['components'])
        # Orthonormal PCA directions, calculated without silently suppressing numerical warnings.
        np.testing.assert_allclose(np.einsum('ij,kj->ik', expected, expected), np.eye(4), atol=1e-8)
