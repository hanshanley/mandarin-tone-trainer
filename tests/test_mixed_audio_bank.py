import importlib.util
import gzip
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AVAILABLE = all(importlib.util.find_spec(name) for name in (
    'numpy', 'scipy', 'librosa', 'parselmouth', 'pyworld', 'pypinyin', 'sklearn',
))
if AVAILABLE:
    with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
        import build_mixed_audio_bank as mixed
        from verify_comparison_identity import no_primary_contradiction, StableWhisperFeatures
    import numpy as np


@unittest.skipUnless(AVAILABLE, 'optional audio audit dependencies unavailable')
class MixedAudioBankTests(unittest.TestCase):
    def test_whole_word_admission_requires_stable_non_neutral_unseen_audio(self):
        row = {'kind': 'word', 'source': 'mandarin_native', 'syllables': ['ma', 'ma'],
               'quarantined': False, 'weak_training_label': '1-2', 'sha256': 'a', 'label_identity': 'reading'}
        diagnostic = {'assessment': 'reference_supported', 'reference_overlap': False,
                      'identity_supported': True, 'sha256': 'a', 'label_identity': 'reading',
                      'blind': {'pattern': '1-2', 'boundary_stable': True, 'quality_ok': True,
                                'minimum_top_probability': .99, 'minimum_margin': .9, 'boundary_variants': 3}}
        self.assertTrue(mixed.whole_word_supported(row, diagnostic))
        for update in [{'boundary_variants': 0}, {'boundary_variants': 2}, {'pattern': '1-3'},
                       {'boundary_stable': False}, {'quality_ok': False}, {'minimum_top_probability': .9},
                       {'minimum_margin': .2}]:
            self.assertFalse(mixed.whole_word_supported(row, {**diagnostic, 'blind': {**diagnostic['blind'], **update}}))
        for update in [{'reference_overlap': True}, {'sha256': 'stale'}, {'label_identity': 'wrong'},
                       {'identity_supported': False}, {'assessment': 'unresolved'}]:
            self.assertFalse(mixed.whole_word_supported(row, {**diagnostic, **update}))
        for update in [{'weak_training_label': '1-N'}, {'quarantined': True}, {'source': 'audio_cmn'},
                       {'syllables': ['ma']}, {'kind': 'comparison'}]:
            self.assertFalse(mixed.whole_word_supported({**row, **update}, diagnostic))

    def test_families_and_reencoded_duplicates_stay_in_one_fold_group(self):
        rows = [
            {'id': 'a', 'syllables': ['ma'], 'decoded_audio_sha256': 'x'},
            {'id': 'b', 'syllables': ['ma'], 'decoded_audio_sha256': 'y'},
            {'id': 'c', 'syllables': ['ba'], 'decoded_audio_sha256': 'y'},
            {'id': 'd', 'syllables': ['ni'], 'decoded_audio_sha256': 'z'},
        ]
        groups = mixed.candidate_groups(rows)
        self.assertEqual(groups['a'], groups['b'])
        self.assertEqual(groups['b'], groups['c'])
        self.assertNotEqual(groups['c'], groups['d'])

    def test_quality_identity_and_original_tone_are_all_required(self):
        options = {'minimum_probability': .95, 'minimum_margin': .25,
                   'minimum_voiced_frames': 12, 'maximum_tracker_difference_semitones': 2}
        row = {'weak_training_label': '3', 'quarantined': False}
        result = {'predicted_tone': '3', 'expected_probability': .99, 'margin': .9,
                  'quality': {'jointly_voiced_frames': 20, 'usable_trackers': 3,
                              'tracker_difference': .1, 'clipped_fraction': 0},
                  'identity_supported': True, 'training_overlap': False}
        self.assertTrue(mixed.accepted_prediction(row, result, options))
        for update in [{'predicted_tone': '2'}, {'expected_probability': .6},
                       {'identity_supported': False}, {'training_overlap': True}]:
            self.assertFalse(mixed.accepted_prediction(row, {**result, **update}, options))
        self.assertFalse(mixed.accepted_prediction({**row, 'quarantined': True}, result, options))
        for update in [{'usable_trackers': 1}, {'jointly_voiced_frames': 2},
                       {'tracker_difference': 5}, {'clipped_fraction': .1}]:
            self.assertFalse(mixed.accepted_prediction(row, {**result, 'quality': {**result['quality'], **update}}, options))

    def test_same_source_and_identical_payloads_do_not_corroborate_each_other(self):
        options = {'maximum_duplicate_similarity': .995}
        first = {'audio_path': 'audio/a.mp3', 'source': 'audio_cmn', 'decoded_audio_sha256': 'first', 'sha256': 'a'}
        same = {**first, 'audio_path': 'audio/b.mp3', 'source': 'audio_cmn_syllables', 'decoded_audio_sha256': 'second'}
        duplicate = {**first, 'audio_path': 'audio/c.mp3', 'source': 'pinyin_public'}
        groups = {'ma3': {'a': (first, {}), 'b': (same, {}), 'c': (duplicate, {})}}
        with patch.object(mixed, 'safe_audio', return_value=Path('irrelevant')), \
                patch.object(mixed, 'normalized_waveform', return_value=np.ones(10)), \
                patch.object(mixed, 'similarity', return_value=1):
            self.assertEqual(mixed.cross_source_pairs(groups, options), {})

    def test_independent_fallback_never_overrides_a_clear_primary_base_contradiction(self):
        same = {'text': '北', 'recognized_pinyin': ['bei3']}
        unknown = {'text': 'bar', 'recognized_pinyin': []}
        wrong = {'text': '配', 'recognized_pinyin': ['pei4']}
        self.assertTrue(no_primary_contradiction(same, unknown, 'bei'))
        self.assertFalse(no_primary_contradiction(same, wrong, 'bei'))

    def test_stable_whisper_features_are_finite_and_preserve_shape(self):
        if not importlib.util.find_spec('faster_whisper'):
            self.skipTest('optional Whisper unavailable')
        from faster_whisper.feature_extractor import FeatureExtractor
        extractor = StableWhisperFeatures(FeatureExtractor())
        signal = (.02 * np.sin(2 * np.pi * 220 * np.arange(16000) / 16000)).astype(np.float32)
        features = extractor(signal)
        self.assertEqual(features.shape[0], 80)
        self.assertTrue(np.isfinite(features).all())

    def test_published_support_proves_each_recording_and_base_were_held_out(self):
        model_path=ROOT/'data/mixed_audio_reference_models.json.gz'
        if not model_path.is_file():
            self.skipTest('mixed-source models not generated')
        payload=model_path.read_bytes()
        bundle=json.loads(gzip.decompress(payload))
        models={model['fold']:model for model in bundle['models']}
        hashes={fold:mixed.digest(model) for fold,model in models.items()}
        ledger=json.loads((ROOT/'data/acoustic_reviews.json').read_text())
        entries=[entry for entry in ledger['approvals'] if entry['evidence']['method']==mixed.METHOD]
        self.assertTrue(entries)
        for entry in entries:
            evidence=entry['evidence']
            self.assertEqual(evidence['model_bundle_sha256'],hashlib.sha256(payload).hexdigest())
            self.assertEqual(evidence['pipeline_sha256'],bundle['pipeline_sha256'])
            for result in [evidence['cross_fit'],evidence['corroboration']['cross_fit']]:
                model=models[result['fold']]
                self.assertEqual(result['model_sha256'],hashes[result['fold']])
                self.assertNotIn(result['family'],model['training_groups'])
                self.assertNotIn(result['family'],model['calibration_groups'])
                self.assertIn(result['family'],model['test_groups'])
                self.assertNotIn(result['sha256'],model['training_audio_sha256s'])
                self.assertNotIn(result['decoded_sha256'],model['training_pcm_sha256s'])
                self.assertEqual(hashlib.sha256((ROOT/result['audio_path']).read_bytes()).hexdigest(),result['sha256'])
