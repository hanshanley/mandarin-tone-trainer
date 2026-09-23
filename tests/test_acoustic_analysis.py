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
        from build_acoustic_reviews import evidence_matches, syllable_intervals
        from collect_acoustic_evidence import (
            decoded_bases, identity_encoding, prepare_recognition, resolved_recognition, unique_segmentation,
            ASR_VERSION, PREPARED_ASR_VERSION, PROFILE_VERSION,
        )
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

    def test_a_low_falling_third_is_not_automatically_called_fourth(self):
        low_fall = np.geomspace(210, 135, 17)
        high_fall = np.geomspace(340, 160, 17)
        self.assertIsNone(acoustic.classify_curve(low_fall, 340))
        self.assertEqual(acoustic.classify_curve(low_fall, 340, connected=True), '3')
        self.assertEqual(acoustic.classify_curve(high_fall, 340), '4')
        self.assertIsNone(acoustic.classify_curve(high_fall))

    def test_conflicting_trackers_do_not_vote_a_recording_into_practice(self):
        rising = np.geomspace(210, 330, 17).tolist()
        falling = rising[::-1]
        measured = {'status': 'measured', 'curves': {'pyin': rising, 'praat': rising, 'world': falling}}
        self.assertEqual(acoustic.decide(measured, '2', 340)['status'], 'review')

    def test_shallow_early_dips_are_not_called_third_tone(self):
        early_dip = np.concatenate([np.geomspace(240, 200, 5), np.geomspace(205, 310, 12)])
        self.assertIn(acoustic.classify_curve(early_dip, 340), (None, '2'))

    def test_one_available_pitch_tracker_is_insufficient(self):
        profile = {'duration': .5, 'tracks': {
            'pyin': [200] * 51, 'praat': [None] * 51, 'world': [None] * 51,
        }}
        self.assertEqual(acoustic.segment(profile)['status'], 'review')

    def test_octave_disagreement_is_not_disguised_as_an_abstaining_vote(self):
        profile = {'duration': .5, 'tracks': {
            'pyin': [150] * 51, 'praat': [300] * 51, 'world': [300] * 51,
        }}
        result = acoustic.segment(profile)
        self.assertEqual(result['status'], 'review')
        self.assertIn('disagree', result['reason'])

    def test_literal_pinyin_is_exact_not_an_approximate_spelling_guess(self):
        self.assertEqual(identity_encoding({'text': 'ZHAI', 'recognized_pinyin': []}, ['zhai']), 'literal_pinyin')
        self.assertEqual(identity_encoding({'text': 'ZHONGXING', 'recognized_pinyin': []}, ['zhong', 'xing']), 'literal_pinyin')
        self.assertEqual(identity_encoding({'text': '宅', 'recognized_pinyin': ['zhai2']}, ['zhai']), 'hanzi_pinyin')
        for text, expected in [('jai', 'zhai'), ('lee', 'li'), ('QI', 'ji'), ('how', 'hou')]:
            self.assertIsNone(identity_encoding({'text': text, 'recognized_pinyin': []}, [expected]))
        self.assertIsNone(identity_encoding({'text': 'ZHAI', 'recognized_pinyin': ['zai2']}, ['zhai']))
        self.assertIsNone(unique_segmentation('xian', {'xi', 'an', 'xian'}))
        self.assertIsNone(identity_encoding({'text': 'XIAN', 'recognized_pinyin': []}, ['xi', 'an']))
        self.assertEqual(identity_encoding({'text': "XI AN", 'recognized_pinyin': []}, ['xi', 'an']), 'literal_pinyin')

    def test_prepared_recognition_cleans_signal_without_pitch_or_duration_rescaling(self):
        times = np.arange(32000) / 16000
        samples = .08 + .001 * np.sin(2 * np.pi * 240 * times)
        prepared, details = prepare_recognition(samples, 16000)
        self.assertAlmostEqual(float(np.sqrt(np.mean(prepared ** 2))), .1, places=4)
        self.assertGreater(details['gain'], 1)
        self.assertEqual(details['sample_rate'], 16000)
        self.assertLess(np.max(np.abs(prepared)), .91)
        peak = np.fft.rfftfreq(len(prepared), 1 / 16000)[np.argmax(np.abs(np.fft.rfft(prepared)))]
        self.assertAlmostEqual(peak, 240, delta=1)

    def test_prepared_asr_resolves_incomplete_transcripts_not_phonetic_conflicts(self):
        raw = {'text': 'JINGI', 'recognized_pinyin': []}
        clean = {'text': '经 济', 'recognized_pinyin': ['jing1', 'ji4']}
        self.assertEqual(resolved_recognition(raw, clean), (clean, None))
        same = {'text': 'ZHONGXING', 'recognized_pinyin': []}
        hanzi = {'text': '中 型', 'recognized_pinyin': ['zhong1', 'xing2']}
        self.assertEqual(resolved_recognition(same, hanzi), (hanzi, None))
        opposite = {'text': '惊喜', 'recognized_pinyin': ['jing1', 'xi3']}
        selected, reason = resolved_recognition(opposite, clean)
        self.assertIsNone(selected)
        self.assertIn('disagree', reason)
        self.assertEqual(resolved_recognition(clean, raw), (clean, None))

    def test_mixed_transcript_is_not_silently_reduced_to_a_matching_suffix(self):
        mixed = {'text': 'DONG 人', 'recognized_pinyin': ['ren2']}
        self.assertIsNone(decoded_bases(mixed))
        self.assertIsNone(identity_encoding(mixed, ['ren']))

    def test_prepared_evidence_must_match_the_same_file_and_processing_version(self):
        digest='a'*64
        candidate={'sha256':digest}
        profile={'sha256':digest,'evidence_version':PROFILE_VERSION}
        raw={'sha256':digest,'evidence_version':ASR_VERSION}
        prepared={'sha256':digest,'evidence_version':PREPARED_ASR_VERSION}
        self.assertTrue(evidence_matches(candidate,profile,raw,prepared))
        self.assertFalse(evidence_matches(candidate,profile,raw,None))
        self.assertFalse(evidence_matches(candidate,profile,raw,{**prepared,'sha256':'b'*64}))
        self.assertFalse(evidence_matches(candidate,profile,raw,{**prepared,'evidence_version':'old'}))

    def test_multi_syllable_alignment_must_be_complete_ordered_and_in_bounds(self):
        intervals = syllable_intervals({'timestamps_ms': [[410, 650], [710, 950]]}, 2, 1.3)
        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0][0], 0)
        self.assertAlmostEqual(intervals[0][1], .68)
        self.assertEqual(intervals[0][1], intervals[1][0])
        self.assertEqual(intervals[1][1], 1.3)
        for timestamps in [[], [[200, 900]], [[400, 700], [300, 900]], [[100, 500], [600, 1800]]]:
            self.assertIsNone(syllable_intervals({'timestamps_ms': timestamps}, 2, 1.3))
        self.assertEqual(syllable_intervals({}, 1, 1.3), [(0, 1.3)])

    def test_neutral_requires_reduction_and_the_correct_preceding_tone_context(self):
        preceding = {'status': 'measured', 'start': 0, 'end': .4, 'voiced_seconds': .4}
        neutral = {'status': 'measured', 'start': .5, 'end': .65, 'voiced_seconds': .15,
                   'curves': {method: [180] * 17 for method in acoustic.METHODS}}
        rms = np.array([.1] * 45 + [.03] * 30)
        result = acoustic.decide_neutral(neutral, preceding, '1', 300, rms)
        self.assertEqual(result['status'], 'screened')
        self.assertEqual(result['expected'], 'N')
        self.assertEqual(acoustic.decide_neutral(neutral, preceding, '3', 300, rms)['status'], 'review')
        self.assertEqual(acoustic.decide_neutral({**neutral, 'voiced_seconds': .4}, preceding, '1', 300, rms)['status'], 'review')
        self.assertEqual(acoustic.decide_neutral(neutral, preceding, '1', 300, np.ones(75) * .1)['status'], 'review')
        full_fall = {**neutral, 'curves': {method: np.geomspace(320, 130, 17).tolist() for method in acoustic.METHODS}}
        self.assertEqual(acoustic.decide_neutral(full_fall, preceding, '1', 300, rms)['status'], 'review')

    def test_neutral_cannot_hide_one_trackers_full_tone_as_an_abstention(self):
        preceding = {'status': 'measured', 'start': 0, 'end': .4, 'voiced_seconds': .4}
        neutral = {'status': 'measured', 'start': .5, 'end': .65, 'voiced_seconds': .15,
                   'curves': {method: [180] * 17 for method in acoustic.METHODS}}
        rms = np.array([.1] * 45 + [.03] * 30)
        with patch.object(acoustic, 'classify_curve', side_effect=[None, None, '2']):
            self.assertEqual(acoustic.decide_neutral(neutral, preceding, '1', 300, rms)['status'], 'review')
