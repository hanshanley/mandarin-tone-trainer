import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in (
    'numpy', 'scipy', 'sklearn', 'librosa', 'parselmouth', 'pyworld', 'pypinyin',
))
if AVAILABLE:
    with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
        import native_tone_labels as labels
        import native_tone_validator as validator
    import numpy as np


@unittest.skipUnless(AVAILABLE, 'optional native-tone model dependencies unavailable')
class NativeToneValidatorTests(unittest.TestCase):
    def profile(self):
        values = np.linspace(180, 290, 100).tolist()
        return {'duration': 1., 'rms': [.1] * 100,
                'tracks': {method: values for method in validator.METHODS}}

    def test_original_lexical_and_spoken_hypotheses_stay_separate(self):
        word = {'word': '你好', 'pinyin_syllables': ['ni', 'hao'], 'lexical_pattern': '3-3',
                'default_surface_pattern': '2-3', 'surface_label_needs_clip_review': False}
        before = copy.deepcopy(word)
        options = labels.hypotheses(word, {'surface_pattern': '3-3'}, {})
        self.assertEqual(word, before)
        self.assertIn({'pattern': '3-3', 'origin': 'original_vocabulary', 'role': 'lexical', 'ambiguous': False}, options)
        self.assertIn({'pattern': '3-3', 'origin': 'recording_annotation', 'role': 'spoken', 'ambiguous': False}, options)
        self.assertIn({'pattern': '2-3', 'origin': 'original_lexical_sandhi', 'role': 'spoken', 'ambiguous': False}, options)

    def test_dictionary_other_readings_cannot_change_the_base_syllable(self):
        word = {'word': '行', 'pinyin_syllables': ['xing'], 'lexical_pattern': '2', 'default_surface_pattern': '2'}
        dictionary = {'行': [{'reading': ['hang2']}, {'reading': ['xing2']}, {'reading': ['xing5']}]}
        options = labels.hypotheses(word, {}, dictionary)
        self.assertEqual({item['pattern'] for item in options}, {'2', 'N'})
        self.assertNotIn('1', {item['pattern'] for item in options})

    def test_family_and_duplicate_audio_never_cross_family_test_splits(self):
        rows = [
            {'id': 'a', 'syllables': ['ma'], 'sha256': '1', 'decoded_audio_sha256': 'p1', 'source': 'audio_cmn'},
            {'id': 'b', 'syllables': ['ma'], 'sha256': '2', 'decoded_audio_sha256': 'p2', 'source': 'pinyin_public'},
            {'id': 'c', 'syllables': ['ba'], 'sha256': '3', 'decoded_audio_sha256': 'p2', 'source': 'pinyin_public'},
            {'id': 'd', 'syllables': ['ni'], 'sha256': '4', 'decoded_audio_sha256': 'p4', 'source': 'mandarin_native'},
            {'id': 'e', 'syllables': ['ni'], 'sha256': '5', 'decoded_audio_sha256': 'p4', 'source': 'audio_cmn'},
        ]
        splits = validator.group_assignments(rows, validator.settings())
        self.assertEqual(splits['a'], splits['b'])
        self.assertEqual(splits['b'], splits['c'])
        self.assertEqual(splits['d'], 'external_source')
        self.assertEqual(splits['e'], 'external_source')
        self.assertEqual(splits, validator.group_assignments(list(reversed(rows)), validator.settings()))

    def test_features_take_audio_and_times_not_the_proposed_answer(self):
        profile = self.profile()
        a, _ = validator.audio_features(profile, [(0, .5), (.5, 1.)])
        profile['proposed_tones'] = '4-4'
        profile['word'] = '你好'
        profile['source'] = 'some-other-source'
        b, _ = validator.audio_features(profile, [(0, .5), (.5, 1.)])
        np.testing.assert_array_equal(a, b)
        c, _ = validator.audio_features(profile, [(0, .3), (.3, 1.)])
        self.assertFalse(np.array_equal(a, c))

    def test_bad_or_missing_audio_cannot_become_supported(self):
        for profile in [
            {**self.profile(), 'rms': [0] * 100},
            {**self.profile(), 'tracks': {method: [None] * 100 for method in validator.METHODS}},
            {**self.profile(), 'tracks': {method: [-1] * 100 for method in validator.METHODS}},
        ]:
            with self.assertRaises(ValueError):
                validator.audio_features(profile, [(0, 1)])
        with self.assertRaises(ValueError):
            validator.audio_features(self.profile(), [(0, .01)])

    def test_boundary_perturbations_preserve_the_whole_word(self):
        variants = validator.perturbations([(0, .4), (.4, 1.)], .02)
        self.assertEqual(len(variants), 3)
        for intervals in variants:
            self.assertEqual(intervals[0][0], 0)
            self.assertEqual(intervals[-1][-1], 1)
            self.assertEqual(intervals[0][1], intervals[1][0])

    def test_swapped_labels_change_compatibility_not_the_blind_prediction(self):
        prediction = {
            'pattern': '2', 'probabilities': {'1': .01, '2': .97, '3': .01, '4': .01},
            'quality_ok': True, 'boundary_stable': True, 'minimum_top_probability': .97, 'minimum_margin': .96,
        }
        original = copy.deepcopy(prediction)
        for target in ('1', '2', '3', '4'):
            result = validator.compatibility(prediction, target, [{'pattern': target, 'role': 'spoken', 'ambiguous': False}])
            self.assertEqual(result['assessment'], 'reference_supported' if target == '2' else 'likely_mismatch')
            self.assertFalse(result['production_admission'])
        self.assertEqual(prediction, original)
        with self.assertRaises(ValueError):
            validator.compatibility(prediction, '1', [], {'reliability': 1.0})
        self.assertEqual(validator.compatibility({**prediction, 'boundary_stable': False}, '2', [])['assessment'], 'unresolved')
        prior = {'sampling': 'uniform_source_holdout', 'role': 'calibration',
                 'source_label_accuracy': 1., 'correct': 40, 'n': 40}
        with_prior = validator.compatibility(prediction, '1', [], prior)
        self.assertEqual(with_prior['assessment'], 'likely_mismatch')
        self.assertEqual(with_prior['blind_pattern'], '2')
        self.assertAlmostEqual(sum(with_prior['label_informed_probabilities'].values()), 1)
        self.assertFalse(with_prior['production_admission'])

    def test_legitimate_variant_is_not_silently_rewritten_as_original(self):
        prediction = {'pattern': '2-3', 'probabilities': {'2-3': .98, '3-3': .02},
                      'quality_ok': True, 'boundary_stable': True, 'minimum_top_probability': .98, 'minimum_margin': .96}
        result = validator.compatibility(prediction, '3-3', [{'pattern': '2-3', 'role': 'spoken', 'ambiguous': False}])
        self.assertEqual(result['assessment'], 'possible_spoken_variant')
        self.assertEqual(result['supplied_pattern'], '3-3')
        self.assertFalse(result['independent_accuracy_verified'])
        unsupported = validator.compatibility(prediction, '4-4', [])
        self.assertEqual(unsupported['assessment'], 'unresolved')
        self.assertFalse(unsupported['original_pattern_represented_in_model'])

    def test_temperature_changes_confidence_not_label_order(self):
        probability = np.array([[.7, .2, .1], [.1, .3, .6]])
        for temperature in (.3, 1, 3):
            adjusted = validator.temperature_scale(probability, temperature)
            np.testing.assert_allclose(adjusted.sum(axis=1), 1)
            np.testing.assert_array_equal(adjusted.argmax(axis=1), probability.argmax(axis=1))

    def test_portable_multiclass_forest_matches_sklearn(self):
        from sklearn.ensemble import ExtraTreesClassifier
        random = np.random.default_rng(21)
        x = random.normal(size=(60, 4))
        y = np.array(['1', '2', '3'] * 20)
        model = ExtraTreesClassifier(n_estimators=5, max_depth=3, random_state=11).fit(x, y)
        np.testing.assert_allclose(validator.forest_predict(validator.export_forest(model), x),
                                   model.predict_proba(x), atol=1e-12)

    def test_pending_gold_cannot_pass_a_release_gate(self):
        item = {'id': 'one', 'sha256': 'a' * 64, 'decoded_audio_sha256': 'b' * 64,
                'label_identity': 'c' * 64, 'role': 'test', 'stratum': ['source', 1, '2'],
                'audio_path': 'audio/example.mp3'}
        plan = {'version': 1, 'inventory_sha256': 'inventory', 'model_sha256': 'model', 'items': [item]}
        plan['plan_sha256'] = labels.digest(plan)
        pending = {'version': 1, 'plan_sha256': plan['plan_sha256'],
                   'items': [{**item, 'annotated_pattern': None, 'reviews': []}]}
        prediction = {'inventory_sha256': 'inventory', 'model_sha256': 'model', 'records': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, value in [('plan.json', plan), ('labels.json', pending), ('predictions.json', prediction)]:
                (root / filename).write_text(json.dumps(value))
            validator.evaluate_gold(root / 'plan.json', root / 'labels.json', root / 'predictions.json', root / 'result.json')
            result = json.loads((root / 'result.json').read_text())
            self.assertFalse(result['zero_observed_error_gate_passed'])
            self.assertFalse(result['production_admission'])
            self.assertEqual(result['pending_reviews'], 1)
            pending['items'][0]['role'] = 'calibration'
            (root / 'labels.json').write_text(json.dumps(pending))
            with self.assertRaises(ValueError):
                validator.evaluate_gold(root / 'plan.json', root / 'labels.json', root / 'predictions.json', root / 'result.json')

    def test_priors_use_only_independent_calibration_not_test_labels(self):
        items, annotations, predictions = [], [], []
        for index in range(40):
            item = {'id': str(index), 'sha256': labels.digest(['audio', index]),
                    'decoded_audio_sha256': labels.digest(['pcm', index]), 'label_identity': labels.digest(['label', index]),
                    'role': 'calibration' if index < 30 else 'test',
                    'stratum': ['source', 1, '2'], 'audio_path': f'audio/{index}.mp3'}
            items.append(item)
            reviews = [{
                'reviewer': reviewer, 'method': 'independent_listening',
                'sha256': item['sha256'], 'label_identity': item['label_identity'],
                'heard_pattern': '2', 'identity_correct': True, 'clear_for_practice': True,
            } for reviewer in ('fixture-reviewer-a', 'fixture-reviewer-b')]
            annotations.append({**item, 'annotated_pattern': '2' if index < 30 else 'invalid',
                                'reviews': reviews if index < 30 else []})
            predictions.append({**item, 'reference_overlap': False, 'supplied_pattern': '2',
                                'blind': {'pattern': '2'}, 'assessment': 'reference_supported'})
        plan = {'version': 1, 'inventory_sha256': 'inventory', 'model_sha256': 'model',
                'sampling': 'uniform_source_holdout', 'items': items}
        plan['plan_sha256'] = labels.digest(plan)
        annotation = {'plan_sha256': plan['plan_sha256'], 'items': annotations}
        predicted = {'inventory_sha256': 'inventory', 'model_sha256': 'model', 'records': predictions}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('plan', plan), ('labels', annotation), ('predictions', predicted)]:
                (root / name).write_text(json.dumps(value))
            validator.calibrate_gold(root / 'plan', root / 'labels', root / 'predictions', root / 'result')
            result = json.loads((root / 'result').read_text())
            self.assertFalse(result['test_labels_used'])
            self.assertEqual(result['source_priors']['source']['source_label_accuracy'], 1)
            self.assertEqual(result['source_priors']['source']['n'], 30)
            self.assertFalse(result['production_admission'])
            annotations[0]['reviews'][1]['reviewer'] = 'fixture-reviewer-a'
            (root / 'labels').write_text(json.dumps(annotation))
            with self.assertRaises(ValueError):
                validator.calibrate_gold(root / 'plan', root / 'labels', root / 'predictions', root / 'result')

    def test_sentence_cuts_are_not_native_training_data(self):
        for path in ('audio/mandarin_native/excerpts/clip.wav', 'audio/mandarin_native/context/sentence.mp3',
                     'audio/../private.mp3', '/tmp/file.mp3'):
            self.assertFalse(labels.is_direct(path))

    def test_unclear_gold_is_counted_as_a_false_accept_not_dropped(self):
        item = {'id': 'bad', 'sha256': 'a' * 64, 'decoded_audio_sha256': 'b' * 64,
                'label_identity': 'c' * 64, 'role': 'test', 'stratum': ['source', 1, '2'],
                'audio_path': 'audio/bad.mp3'}
        reviews = [{'reviewer': name, 'method': 'independent_listening',
                    'sha256': item['sha256'], 'label_identity': item['label_identity'],
                    'heard_pattern': None, 'identity_correct': False, 'clear_for_practice': False}
                   for name in ('fixture-a', 'fixture-b')]
        plan = {'version': 1, 'inventory_sha256': 'inventory', 'model_sha256': 'model', 'items': [item]}
        plan['plan_sha256'] = labels.digest(plan)
        annotation = {'plan_sha256': plan['plan_sha256'], 'items': [{**item, 'annotated_pattern': None, 'reviews': reviews}]}
        prediction = {'inventory_sha256': 'inventory', 'model_sha256': 'model', 'records': [
            {**item, 'reference_overlap': False, 'supplied_pattern': '2', 'blind': {'pattern': '2'},
             'assessment': 'reference_supported'}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, value in [('plan', plan), ('labels', annotation), ('predictions', prediction)]:
                (root / filename).write_text(json.dumps(value))
            validator.evaluate_gold(root / 'plan', root / 'labels', root / 'predictions', root / 'result')
            result = json.loads((root / 'result').read_text())
            self.assertEqual(result['test_items'], 1)
            self.assertEqual(result['per_source_length']['source:1']['wrong_accepted'], 1)
            self.assertFalse(result['zero_observed_error_gate_passed'])

    def test_saved_native_models_have_source_and_duplicate_disjoint_holdouts(self):
        if not validator.MODEL.exists():
            self.skipTest('native models not trained')
        model = validator.load_model()
        self.assertIs(model['production_admission'], False)
        self.assertIs(model['source_labels_are_gold'], False)
        report = json.loads(validator.REPORT.read_text())
        if 'split_manifest' not in report:
            self.skipTest('native split provenance not yet regenerated')
        for name in ('sha256', 'decoded_audio_sha256'):
            partitions = {}
            for row in report['split_manifest']:
                partitions.setdefault(row[name], set()).add(row['partition'])
            self.assertTrue(all(len(values) == 1 for values in partitions.values()))
        family_partitions = {}
        for row in report['split_manifest']:
            if row['partition'] == 'external_source':
                continue
            family_partitions.setdefault(row['family'], set()).add(row['partition'])
            self.assertNotEqual(row['source'], validator.settings()['held_out_source'])
        self.assertTrue(all(len(values) == 1 for values in family_partitions.values()))
