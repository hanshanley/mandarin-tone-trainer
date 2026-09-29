import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('map_glossika_examples', ROOT / 'scripts/map_glossika_examples.py')
mapping = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapping)


def words(text):
    return [{'word': ' ' + word, 'start': number * .2, 'end': (number + 1) * .2}
            for number, word in enumerate(text.split())]


class GlossikaAudioMappingTests(unittest.TestCase):
    def test_introduction_numbers_are_read_without_an_expected_answer_prompt(self):
        prefix = 'Glossica Chinese Mandarin Tone Training'
        self.assertTrue(mapping.header_match('tone-123', words(prefix + ' Tone 1 plus Tone 2 plus Tone 3'))['label_matches'])
        wrong = mapping.header_match('tone-132', words(prefix + ' Tone 1 plus Tone 3 plus Tone 1'))
        self.assertFalse(wrong['label_matches'])
        self.assertEqual(wrong['numbers'], [1, 3, 1])
        self.assertEqual(wrong['expected_numbers'], [1, 3, 2])
        self.assertIsNone(mapping.header_match('tone-12', words('Tone 1 plus Tone 2')))
        self.assertIsNone(mapping.header_match('tone-123', words(prefix + ' Tone 1 plus Tone 2')))

    def test_brand_transcription_variants_do_not_change_the_spoken_part_number(self):
        result = mapping.header_match(
            'vowel-part-31',
            words('Gloss of the Chinese Mandarin Vowel and Rhyme Training Core Vowels Part thirty one'),
        )
        self.assertEqual(result['numbers'], [31])
        self.assertTrue(result['label_matches'])

    def test_alignment_finds_a_missing_book_item_instead_of_shifting_every_label(self):
        expected = [['ma'], ['ni'], ['hao'], ['shi']]
        self.assertEqual(mapping.align_items(expected, [['ma'], ['hao'], ['shi']]), [0, None, 1, 2])
        self.assertEqual(mapping.align_items([['ma'], ['hao']], [['ma'], ['ni'], ['hao']]), [0, 2])
        self.assertEqual(mapping.align_items([['ma'], ['ma']], [['ma']]), [None, None])

    def test_unknown_recognition_is_not_a_positive_phonetic_attestation(self):
        expected = [['ma'], ['ni'], ['hao']]
        positions = mapping.align_items(expected, [None, None, None])
        items = [{'pinyin_syllables': bases, 'kind': 'word_drill'} for bases in expected]
        self.assertEqual(mapping.anchored_positions(items, [None, None, None], positions), {})
        supported = mapping.anchored_positions(items, [['ma'], None, ['hao']], [0, 1, 2])
        self.assertEqual(supported[1], 'publisher_order_between_phonetic_anchors')

    def test_five_tone_cadence_excludes_the_unlisted_vowel_demonstration(self):
        regions = [[0, 3000], [3500, 3900]]
        regions += [[5000 + number * 700, 5300 + number * 700] for number in range(5)]
        row = {'lesson_id': 'vowel-part-1', 'sha256': 'a' * 64,
               'sample_rate': 1000, 'regions': {'40': regions}}
        intro = {'sha256': row['sha256'], 'version': mapping.INTRO_VERSION,
                 'header': {'header_end_seconds': 3}}
        _, offset = mapping.body_regions(row, intro)
        self.assertEqual(offset, 2)
        items = [{'kind': 'syllable_drill', 'pinyin_syllables': ['o'], 'lexical_tones': [tone]}
                 for tone in (1, 2, 3, 4, 0)]
        positions, support = mapping.align_syllable_groups(items, [['o']] * 5, regions, offset, 1000)
        self.assertEqual(positions, [0, 1, 2, 3, 4])
        self.assertEqual(len(support), 5)
        positions, support = mapping.align_syllable_groups(items, [None] * 5, regions, offset, 1000)
        self.assertEqual(positions, [0, 1, 2, 3, 4])
        self.assertEqual(set(support.values()), {'publisher_five_column_table_and_recorded_cadence'})

    def test_syllable_family_requires_all_five_ordered_positions_and_a_phonetic_anchor(self):
        items = [{'pinyin_syllables': ['zhi'], 'kind': 'syllable_drill'} for _ in range(5)]
        recognized = [['zhi'], None, None, None, None]
        result = mapping.anchored_positions(items, recognized, list(range(5)))
        self.assertEqual(len(result), 5)
        self.assertEqual(result[4], 'publisher_five_tone_family_order')
        self.assertEqual(mapping.anchored_positions(items, [None] * 5, list(range(5))), {})
        self.assertNotIn(4, mapping.anchored_positions(items, recognized, [0, 1, 2, None, 4]))

    def test_clip_intervals_preserve_conservative_speech_bounds_and_real_silence(self):
        regions = [[100, 400], [800, 1200], [1600, 2000]]
        row = {'sample_rate': 1000, 'source_frames': 2500, 'channels': 2,
               'regions': {'35': regions, '40': regions, '45': [[100, 420], [770, 1240], [1570, 2000]]}}
        segment = mapping.interval_for(row, regions, 1)
        self.assertEqual(segment['speech_start_sample'], 770)
        self.assertEqual(segment['speech_end_sample'], 1240)
        self.assertGreaterEqual(segment['speech_start_sample'] - segment['start_sample'], 80)
        self.assertGreaterEqual(segment['end_sample'] - segment['speech_end_sample'], 80)
        self.assertGreater(segment['start_sample'], 420)
        self.assertLess(segment['end_sample'], 1570)
        crowded = [[100, 790], [800, 1200], [1220, 1800]]
        row['regions'] = {'35': crowded, '40': crowded}
        with self.assertRaisesRegex(ValueError, 'silence'):
            mapping.interval_for(row, crowded, 1)


if __name__ == '__main__':
    unittest.main()
