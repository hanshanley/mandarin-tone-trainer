import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
OPTIONAL = all(importlib.util.find_spec(name) for name in (
    'numpy', 'scipy', 'sklearn', 'librosa', 'parselmouth', 'opencc', 'pypinyin',
))
if OPTIONAL:
    with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
        import calibrated_pronunciation_judge as judge
        import pronunciation_features as features
    import numpy as np


@unittest.skipUnless(OPTIONAL, 'optional calibration dependencies are not installed')
class PronunciationJudgeTests(unittest.TestCase):
    def test_speaker_splits_are_disjoint_complete_and_order_independent(self):
        speakers = [f'SPEAKER01{i:03d}' for i in range(3)]
        speakers += [f'SPEAKER02{i:03d}' for i in range(46)]
        settings = judge.config()['split']
        assignments = judge.split_speakers(speakers, settings)
        self.assertEqual(assignments, judge.split_speakers(list(reversed(speakers)), settings))
        self.assertEqual(set(assignments), set(speakers))
        groups = {part: {speaker for speaker, partition in assignments.items() if partition == part}
                  for part in ('train', 'select', 'calibrate', 'threshold', 'test')}
        for left, group in groups.items():
            self.assertTrue(group)
            for right, other in groups.items():
                if left != right:
                    self.assertFalse(group & other)
        self.assertEqual(len(groups['train']), 25)
        self.assertEqual(len(groups['test']), 6)
        with self.assertRaises(ValueError):
            judge.split_speakers(speakers[:-1], settings)

    def test_binary_metrics_reveal_the_always_correct_baseline_failure(self):
        labels = np.array([1] * 90 + [0] * 10)
        result = judge.probability_metrics(labels, np.full(100, .9))
        self.assertAlmostEqual(result['brier'], .09)
        self.assertAlmostEqual(result['ece_10_bins'], 0)
        self.assertAlmostEqual(result['balanced_accuracy_at_0_5'], .5)
        self.assertEqual(result['confusion_at_0_5']['false_accept'], 10)
        selective = judge.threshold_metrics(labels, np.ones(100), {'confidence': .99}, None)
        self.assertEqual(selective['false_accept_rate_among_incorrect'], 1)
        self.assertEqual(selective['false_rejects'], 0)

    def test_reliability_bins_include_exact_zero_and_one(self):
        metrics = judge.probability_metrics([0, 1], [0, 1])
        self.assertEqual(metrics['brier'], 0)
        self.assertEqual(sum(bin['n'] for bin in metrics['reliability_bins']), 2)
        self.assertEqual(metrics['ece_10_bins'], 0)

    def test_high_confidence_threshold_requires_enough_correct_decisions(self):
        policy = judge.config()['decision']
        self.assertIsNone(judge.choose_threshold(np.ones(10), np.ones(10), policy, 'accept'))
        self.assertIsNone(judge.choose_threshold(np.zeros(200), np.ones(200), policy, 'accept'))
        self.assertIsNotNone(judge.choose_threshold(np.ones(400), np.ones(400), policy, 'accept'))
        self.assertIsNone(judge.choose_threshold(np.ones(400), np.ones(400), policy, 'reject'))
        self.assertEqual(judge.binomial_upper(0, 0), 1)
        self.assertGreater(judge.binomial_upper(0, 10), .05)

    def test_portable_forest_is_not_executable_pickle_and_matches_sklearn(self):
        from sklearn.ensemble import ExtraTreesClassifier

        generator = np.random.default_rng(0)
        matrix = generator.normal(size=(120, 5)).astype(np.float32)
        labels = (matrix[:, 0] > matrix[:, 1]).astype(int)
        classifier = ExtraTreesClassifier(n_estimators=5, max_depth=4, random_state=10).fit(matrix, labels)
        serialized = json.loads(json.dumps(judge.forest_export(classifier)))
        np.testing.assert_allclose(judge.forest_predict(serialized, matrix), classifier.predict_proba(matrix)[:, 1], atol=1e-12)

    def test_calibrator_is_monotonic_and_handles_probability_boundaries(self):
        result = judge.calibrated(np.array([0, .2, .5, .8, 1]), {'coefficient': 1.2, 'intercept': -.3})
        self.assertTrue(np.isfinite(result).all())
        self.assertTrue(np.all(np.diff(result) > 0))
        self.assertTrue(np.all((result > 0) & (result < 1)))

    def test_forced_alignment_is_not_allowed_to_drop_reference_units(self):
        with self.assertRaises(ValueError):
            features.validate_alignment([[0, 100]], 2, 1)
        with self.assertRaises(ValueError):
            features.validate_alignment([[0, 500], [200, 700]], 2, 1)
        with self.assertRaises(ValueError):
            features.validate_alignment([[0, 1500]], 1, 1)
        with self.assertRaises(ValueError):
            features.validate_alignment([[0, float('nan')]], 1, 1)

    def test_asr_edit_alignment_preserves_mismatches_and_missing_tokens(self):
        aligned, distance = features.edit_alignment(['ma', 'shi', 'hao'], ['ma', 'si'])
        self.assertEqual(len(aligned), 3)
        self.assertNotEqual(aligned, ['ma', 'shi', 'hao'])
        self.assertGreater(distance, 0)
        self.assertEqual(features.edit_alignment(['ma'], [])[0], [None])

    def test_all_five_reference_tones_are_supported_without_gold_in_features(self):
        self.assertEqual([features.pinyin_parts(f'ma{tone}')[3] for tone in range(1, 6)], [1, 2, 3, 4, 5])
        self.assertIn('expected_tone_5', features.FEATURE_NAMES)
        for name in features.FEATURE_NAMES:
            self.assertNotIn('label', name)
            self.assertNotIn('reviewer', name)
            self.assertNotIn('speaker_id', name)
        self.assertEqual(len(features.pitch_features(np.full(20, np.nan), 200)), 30)

    def test_complete_pinned_corpus_uses_human_targets_not_practice_approvals(self):
        if not (ROOT / 'imports/pronunciation_judge/ompal/non-native_scores.json').exists():
            self.skipTest('reference corpus not downloaded')
        rows, _ = judge.dataset()
        self.assertEqual(len(rows), 1850)
        self.assertEqual(len({row['speaker'] for row in rows}), 49)
        self.assertTrue(any(row['annotation_issue'] for row in rows))
        for row in rows:
            for unit in row['units']:
                self.assertEqual(set(unit['labels']), set(judge.TARGETS))
                self.assertLessEqual(set(unit['labels'].values()), {0, 1})
