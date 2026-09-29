import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'build_glossika_example_index',
    ROOT / 'scripts/build_glossika_example_index.py',
)
indexer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indexer)


def positioned_chars(text, start=0.0, width=5.0, y=100.0):
    return [
        {'c': char, 'bbox': (start + offset * width, y, start + (offset + 1) * width, y + 10)}
        for offset, char in enumerate(text)
    ]


def span(text, x0, x1, y=100.0, font='Calibri'):
    return {
        'text': text,
        'bbox': (x0, y, x1, y + 20),
        'origin': (x0, y + 15),
        'font': font,
        'size': 16.8,
        'chars': positioned_chars(text, x0, max((x1 - x0) / max(len(text), 1), 0.1), y),
    }


class GlossikaExampleIndexTests(unittest.TestCase):
    def test_source_syllable_preserves_tones_neutral_and_v_normalization(self):
        self.assertEqual(indexer.source_syllable('lüè'), ('lve', 4))
        self.assertEqual(indexer.source_syllable('zhi·'), ('zhi', 0))
        self.assertEqual(indexer.source_syllable('nǐ'), ('ni', 3))
        self.assertIsNone(indexer.source_syllable('t͡ʂ⁼ʅ̄'))

    def test_syllable_row_uses_column_order_and_trims_ipa_bleed(self):
        source = 'chuān chuán chuǎn chuàn chuan·t͡ʂʰʷān'
        row = indexer.syllable_row({
            'origin_y': 100.0,
            'chars': positioned_chars(source),
            'spans': [],
        })
        self.assertEqual(row['base'], 'chuan')
        self.assertEqual(
            [cell['text'] for cell in row['cells']],
            ['chuān', 'chuán', 'chuǎn', 'chuàn', 'chuan·'],
        )
        self.assertEqual(
            [indexer.source_syllable(cell['text'])[1] for cell in row['cells']],
            [1, 2, 3, 4, 0],
        )

    def test_word_row_keeps_historical_markers_separate_from_hanzi(self):
        spans = [
            span('MC17 ', 100, 145),
            span('書桌', 170, 205, font='MicrosoftJhengHeiRegular'),
            span('ᵏ ', 205, 215),
            span('书桌 ', 250, 290, font='MicrosoftJhengHeiRegular'),
            span("jīn'é", 340, 390),
        ]
        row, error = indexer.parse_word_row(spans, spans[0])
        self.assertIsNone(error)
        self.assertEqual(row['historical_category'], 'MC17')
        self.assertEqual(row['traditional'], '書桌')
        self.assertEqual(row['traditional_source'], '書桌ᵏ')
        self.assertEqual(row['historical_coda_markers'], 'ᵏ')
        self.assertEqual(row['word'], '书桌')
        self.assertEqual(row['pinyin'], "jīn'é")

    def test_pinyin_segmentation_uses_strict_source_boundaries_and_tone_notation(self):
        inventory = {
            'jin', 'e', 'shou', 'yin', 'ji', 'xin', 'xing', 'gan', 'an',
            'shen', 'me',
        }
        parsed, error = indexer.split_pinyin("jīn'é", 2, inventory)
        self.assertIsNone(error)
        self.assertEqual(parsed, {
            'syllables': ['jin', 'e'],
            'tones': [1, 2],
            'source_tone_notation': ['diacritic', 'diacritic'],
        })
        parsed, error = indexer.split_pinyin('shōuyīnjī', 3, inventory)
        self.assertIsNone(error)
        self.assertEqual(parsed, {
            'syllables': ['shou', 'yin', 'ji'],
            'tones': [1, 1, 1],
            'source_tone_notation': ['diacritic', 'diacritic', 'diacritic'],
        })
        parsed, error = indexer.split_pinyin('xīngān', 2, inventory)
        self.assertIsNone(error)
        self.assertEqual(parsed, {
            'syllables': ['xin', 'gan'],
            'tones': [1, 1],
            'source_tone_notation': ['diacritic', 'diacritic'],
        })
        parsed, error = indexer.split_pinyin("xīng'ān", 2, inventory)
        self.assertIsNone(error)
        self.assertEqual(parsed['syllables'], ['xing', 'an'])
        parsed, error = indexer.split_pinyin('bóài', 2, inventory | {'bo', 'ai'})
        self.assertEqual(error, 'source_missing_pinyin_separator')
        self.assertEqual(parsed['syllables'], ['bo', 'ai'])
        parsed, error = indexer.split_pinyin('shénme', 2, inventory)
        self.assertIsNone(error)
        self.assertEqual(parsed['tones'], [2, 0])
        self.assertEqual(parsed['source_tone_notation'], ['diacritic', 'unmarked'])

    def test_summary_contains_metadata_not_the_extracted_corpus(self):
        summary = json.loads((ROOT / 'data/glossika_example_summary.json').read_text())
        self.assertEqual(summary['version'], 1)
        self.assertEqual(summary['book_sha256'], indexer.BOOK_SHA256)
        self.assertEqual(summary['method'], indexer.METHOD)
        self.assertTrue(summary['extraction_complete'])
        self.assertFalse(summary['annotation_complete'])
        self.assertEqual(len(summary['per_lesson_counts']), 115)
        self.assertNotIn('items', summary)
        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 25_000)

    @unittest.skipUnless(
        (ROOT / 'imports/glossika/original.pdf').is_file(),
        'Pinned local Glossika PDF is unavailable',
    )
    def test_pinned_pdf_build_is_complete_deterministic_and_ordered(self):
        import fitz

        pdf = ROOT / 'imports/glossika/original.pdf'
        catalog_path = ROOT / 'data/glossika_recordings.json'
        inventory_path = ROOT / 'data/hsk_words.json'
        catalog_bytes = catalog_path.read_bytes()
        inventory_bytes = inventory_path.read_bytes()
        catalog = json.loads(catalog_bytes)
        inventory = indexer.load_pinyin_inventory(inventory_path)
        document = fitz.open(pdf)
        try:
            built = indexer.build_index(
                document,
                catalog,
                indexer.file_sha256(pdf),
                hashlib.sha256(catalog_bytes).hexdigest(),
                inventory,
                hashlib.sha256(inventory_bytes).hexdigest(),
            )
        finally:
            document.close()

        self.assertTrue(built['extraction_complete'])
        self.assertFalse(built['annotation_complete'])
        self.assertEqual(built['counts'], {
            'indexed_lessons': 115,
            'excluded_lessons': 2,
            'syllable_bases': 383,
            'syllable_items': 1915,
            'two_tone_items': 977,
            'three_tone_items': 1678,
            'total_items': 4570,
            'unresolved': 9,
        })
        self.assertEqual(
            {item['reason'] for item in built['unresolved']},
            {'source_heading_inconsistency', 'source_missing_pinyin_separator'},
        )
        self.assertEqual(len(built['count_discrepancies']), 5)
        self.assertEqual(
            {item['lesson_id'] for item in built['excluded_lessons']},
            {'consonant-1', 'consonant-2'},
        )

        lessons = {lesson['lesson_id']: lesson for lesson in built['lessons']}
        part1 = lessons['vowel-part-1']['items']
        self.assertEqual(len(part1), 35)
        self.assertEqual(
            [(item['pinyin'], item['lexical_pattern']) for item in part1[:5]],
            [('zhī', '1'), ('zhí', '2'), ('zhǐ', '3'), ('zhì', '4'), ('zhi·', 'N')],
        )
        self.assertEqual(
            [item['word'] for item in lessons['tone-11']['items'][:3]],
            ['山边', '医生', '飞机'],
        )
        self.assertEqual(len(lessons['tone-11']['items']), 24)
        self.assertEqual(lessons['tone-11']['items'][0]['pdf_page'], 36)
        self.assertEqual(lessons['tone-111']['items'][0]['word'], '收音机')
        self.assertEqual(len(lessons['tone-111']['items']), 12)
        self.assertEqual(lessons['tone-111']['items'][0]['pdf_page'], 81)
        for lesson in built['lessons']:
            for item in lesson['items']:
                self.assertEqual(len(item['lexical_tones']), len(indexer.cjk_text(item['word']))
                                 if item['kind'] == 'word_drill' else 1)
                if item['lexical_pattern']:
                    self.assertEqual(
                        item['lexical_pattern'],
                        '-'.join('N' if tone == 0 else str(tone) for tone in item['lexical_tones']),
                    )

        payload = indexer.json_bytes(built)
        self.assertEqual(payload, indexer.json_bytes(built))
        local_index = ROOT / 'audio/glossika/example_index.json'
        if local_index.is_file():
            self.assertEqual(local_index.read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
