import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
OPTIONAL = all(importlib.util.find_spec(name) for name in (
    'numpy', 'scipy', 'sklearn', 'librosa', 'parselmouth', 'pyworld', 'opencc', 'pypinyin',
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
        self.assertEqual(metrics['auroc_correctness'], 1)
        self.assertEqual(judge.probability_metrics([0, 1], [.5, .5])['auroc_correctness'], .5)
        for labels, probabilities in [([0], [1, 0]), ([2], [.5]), ([1], [float('nan')]), ([0], [1.1])]:
            with self.assertRaises(ValueError):
                judge.probability_metrics(labels, probabilities)

    def test_high_confidence_threshold_requires_enough_correct_decisions(self):
        policy = judge.config()['decision']
        self.assertIsNone(judge.choose_threshold(np.ones(10), np.ones(10), policy, 'accept'))
        self.assertIsNone(judge.choose_threshold(np.zeros(200), np.ones(200), policy, 'accept'))
        self.assertIsNone(judge.choose_threshold(np.ones(400), np.ones(400), policy, 'accept'))
        self.assertIsNotNone(judge.choose_threshold(
            np.array([1] * 400 + [0] * 400), np.array([.99] * 400 + [.01] * 400), policy, 'accept',
        ))
        self.assertIsNone(judge.choose_threshold(np.ones(400), np.ones(400), policy, 'reject'))
        self.assertEqual(judge.binomial_upper(0, 0), 1)
        self.assertGreater(judge.binomial_upper(0, 10), .05)

    def test_threshold_selection_cannot_hide_rare_errors_in_overall_accuracy(self):
        policy = judge.config()['decision']
        labels = np.array([1] * 980 + [0] * 20)
        probabilities = np.full(1000, .99)
        self.assertIsNone(judge.choose_threshold(labels, probabilities, policy, 'accept'))
        support = judge.threshold_feasibility(labels, policy)
        self.assertEqual(support['incorrect_examples'], 20)
        self.assertFalse(support['enough_incorrect_examples_for_acceptance'])
        self.assertGreater(support['best_possible_false_accept_upper_bound'], policy['maximum_error_rate'])
        self.assertGreater(support['minimum_class_examples_even_with_zero_errors'], 20)
        self.assertTrue(support['enough_correct_examples_for_rejection'])

    def test_accuracy_on_mostly_correct_speech_cannot_pass_the_error_detection_gate(self):
        policy = judge.config()['decision']
        misleading = {'accepted': 1000, 'accepted_error_upper_95': .03, 'false_accepts': 20}
        bootstrap = {'accepted_error_upper_95': .04, 'false_accept_rate_upper_95': .99}
        self.assertFalse(judge.acceptance_gate(misleading, bootstrap, 22, policy))
        all_abstain = {'accepted': 0, 'accepted_error_upper_95': 1, 'false_accepts': 0}
        self.assertFalse(judge.acceptance_gate(all_abstain, {
            'accepted_error_upper_95': None, 'false_accept_rate_upper_95': None,
        }, 100, policy))
        supported = {'accepted': 1000, 'accepted_error_upper_95': .01, 'false_accepts': 0}
        self.assertTrue(judge.acceptance_gate(supported, {
            'accepted_error_upper_95': .01, 'false_accept_rate_upper_95': .01,
        }, 100, policy))

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

    def test_calibration_fit_is_finite_and_learns_miscalibrated_probabilities(self):
        random = np.random.default_rng(41)
        logits = random.normal(size=3000)
        truth = judge.expit(.8 * logits + .6)
        labels = (random.random(3000) < truth).astype(int)
        raw = judge.expit(2 * logits - .3)
        with np.errstate(all='raise'):
            parameters = judge.fit_calibration(raw, labels)
        self.assertGreater(parameters['coefficient'], 0)
        self.assertLess(parameters['coefficient'], 1)
        corrected = judge.calibrated(raw, parameters)
        self.assertLess(judge.probability_metrics(labels, corrected)['brier'],
                        judge.probability_metrics(labels, raw)['brier'])
        with self.assertRaises(ValueError):
            judge.fit_calibration(raw, np.ones(3000))

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

    def test_high_probability_and_familiar_text_do_not_fake_domain_certification(self):
        forest = [{'left': [-1], 'right': [-1], 'feature': [-2], 'threshold': [-2], 'probability': [.999]}]
        artifact = {
            'heads': {target: {'forest': forest, 'calibration': {'coefficient': 1, 'intercept': 0},
                              'held_out_acceptance_gate_passed': True}
                      for target in judge.TARGETS},
            'feature_support': {'lower': [-1] * len(features.FEATURE_NAMES), 'upper': [1] * len(features.FEATURE_NAMES)},
            'reference_sentences': ['他'], 'engine_provenance': {'checked': True},
        }
        evidence = {
            'audio_sha256': 'a' * 64, 'reference_text': '他', 'recognition_text': '他',
            'units': [{'features': [0] * len(features.FEATURE_NAMES), 'voiced_frames': 30,
                       'character_start': 0, 'character_end': 1, 'expected_pinyin': ['ta1'],
                       'start': 0, 'end': .5}],
        }
        with patch.object(judge, 'load_model', return_value=artifact), \
                patch.object(judge, 'load_engines', return_value=(None, None)), \
                patch.object(judge, 'engine_provenance', return_value={'checked': True}), \
                patch.object(judge, 'analyze', return_value=evidence), \
                patch.object(Path, 'read_bytes', return_value=b'model'):
            output = judge.judge(Path('irrelevant.wav'), '他')
        self.assertFalse(output['production_admission'])
        self.assertEqual(output['domain'], 'unvalidated_target_domain')
        self.assertTrue(output['reference_text_seen_in_corpus'])
        for result in output['units'][0]['scores'].values():
            self.assertEqual(result['status'], 'uncertain')
            self.assertGreater(result['reference_calibrated_correctness_estimate'], .99)
            self.assertFalse(result['calibration_transfer_verified'])

    def test_saved_model_binds_reference_splits_and_evaluation(self):
        if not judge.MODEL.exists():
            self.skipTest('model training has not completed')
        artifact = judge.load_model()
        self.assertEqual(set(artifact['heads']), set(judge.TARGETS))
        self.assertFalse(artifact['certifies_accuracy'])
        self.assertEqual(artifact['scope'], 'diagnostic_only_until_independent_target_corpus_validation')
        report = json.loads(judge.REPORT.read_text())
        self.assertFalse(report['automatic_practice_admission_enabled'])
        if artifact.get('representation'):
            self.assertEqual(report['test_status'], 'previously_examined_benchmark_not_a_fresh_test')
            development = json.loads((ROOT / 'data/pronunciation_judge_development.json').read_text())
            self.assertIs(development['test_examined'], False)
            self.assertIn('threshold_data_support', report['results']['tone'])
        groups = {name: set(speakers) for name, speakers in report['speaker_splits'].items()}
        self.assertEqual(sum(map(len, groups.values())), 49)
        for name, group in groups.items():
            self.assertTrue(all(not group & other for key, other in groups.items() if name != key))
        for target, result in report['results'].items():
            self.assertEqual(result['test_calibrated']['n'], report['coverage']['test']['measured_units'])
            self.assertGreater(result['test_calibrated']['human_incorrect'], 0)
            self.assertEqual(result['test_end_to_end_coverage']['unscorable_policy'], 'abstain, not pass')
