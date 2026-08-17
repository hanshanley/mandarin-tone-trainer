import importlib.util
import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / 'scripts' / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


add_pinyin = load_script('add_pinyin_syllables.py')
build_hsk = load_script('build_hsk_data.py')
download_syllables = load_script('download_audio_cmn_syllables.py')
add_definitions = load_script('add_definitions.py')
check_syllables = load_script('check_audio_cmn_syllables.py')
download_public_pinyin = load_script('download_public_pinyin_syllables.py')


class PinyinSegmentationTests(unittest.TestCase):
    def test_avoids_unmarked_vowel_initial_boundary(self):
        vocab = ['neng', 'eng', 'ken', 'ke']
        item = {'pinyin': 'kěnéng', 'lexical_tones': [3, 2]}
        self.assertEqual(add_pinyin.split(item, vocab), ['ke', 'neng'])

    def test_respects_apostrophe_boundary(self):
        vocab = ['fang', 'gan', 'an', 'fan']
        item = {'pinyin': "fāng'àn", 'lexical_tones': [1, 4]}
        self.assertEqual(add_pinyin.split(item, vocab), ['fang', 'an'])

    def test_handles_erhua_and_annotations(self):
        vocab = ['wan', 'hao', 'xie', 'you', 'r']
        erhua = {'pinyin': 'hǎowánr', 'lexical_tones': [3, 2, 0]}
        annotated = {'pinyin': 'yǒu(yī)xiē', 'lexical_tones': [3, 1]}
        self.assertEqual(add_pinyin.split(erhua, vocab), ['hao', 'wan', 'r'])
        self.assertEqual(add_pinyin.split(annotated, vocab), ['you', 'xie'])


class PipelineValidationTests(unittest.TestCase):
    def test_unaligned_hanzi_and_tones_require_review(self):
        _, _, aligned = build_hsk.sandhi_surface('妈妈', [1])
        self.assertFalse(aligned)

    def test_mp3_header_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / 'valid.mp3'
            invalid = Path(directory) / 'invalid.mp3'
            valid.write_bytes(b'ID3audio')
            invalid.write_bytes(b'<html>')
            self.assertTrue(download_syllables.valid_mp3(valid))
            self.assertFalse(download_syllables.valid_mp3(invalid))

    def test_selects_matching_common_cedict_definition(self):
        item = {
            'word': '新',
            'traditional': '新',
            'pinyin_syllables': ['xin'],
            'lexical_tones': [1],
        }
        entries = add_definitions.parse_cedict(
            '新 新 [Xin1] /surname Xin/\n'
            '新 新 [xin1] /new/newly/CL:個|个[ge4]/\n'
        )
        self.assertEqual(
            add_definitions.choose_definition(item, entries['新']),
            'new; newly',
        )

    def test_rejects_definition_for_a_different_reading(self):
        item = {
            'word': '行',
            'traditional': '行',
            'pinyin_syllables': ['xing'],
            'lexical_tones': [2],
        }
        entries = add_definitions.parse_cedict('行 行 [hang2] /row/profession/\n')
        self.assertEqual(add_definitions.choose_definition(item, entries['行']), '')

    def test_measures_leading_and_trailing_audio_buffers(self):
        sample_rate = 16000
        samples = [0] * 1600
        samples += [
            round(12000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            for index in range(3200)
        ]
        samples += [0] * 2400
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'buffered.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(struct.pack(f'<{len(samples)}h', *samples))
            lead, tail, _ = check_syllables.edge_buffers(path)
        self.assertAlmostEqual(lead, 0.10, places=2)
        self.assertAlmostEqual(tail, 0.15, places=2)

    def test_duplicate_payload_detection_ignores_id3_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plain = root / 'plain.mp3'
            tagged = root / 'tagged.mp3'
            distinct = root / 'distinct.mp3'
            plain.write_bytes(b'audio-payload')
            tagged.write_bytes(
                b'ID3\x04\x00\x00\x00\x00\x00\x04'
                b'tag!'
                b'audio-payload'
            )
            distinct.write_bytes(b'different-payload')
            groups = check_syllables.duplicate_payload_groups(
                [plain, tagged, distinct]
            )
        self.assertEqual(groups, [[plain, tagged]])

    def test_correction_stops_existing_audio_before_decode(self):
        source = (ROOT / 'app' / 'app.js').read_text(encoding='utf-8')
        function = source.split('async function playPinyinSequence', 1)[1].split(
            'async function playCorrection', 1
        )[0]
        self.assertLess(function.index('stopNative();'), function.index('await pinyinSequenceBuffer'))
        self.assertLess(function.index('stopCorrection();'), function.index('await pinyinSequenceBuffer'))
        correction = source.split('async function playCorrection', 1)[1].split(
            "$('play').onclick", 1
        )[0]
        self.assertLess(correction.index("tone==='N'"), correction.index('playPinyinKey'))
        native = source.split('function playNative', 1)[1].split(
            'function correctionKey', 1
        )[0]
        self.assertLess(native.index('stopCorrection();'), native.index('new Audio'))
        self.assertIn('isPlaybackInterruption(error)', native)
        self.assertIn('isPlaybackInterruption(error)', source.split("$('playMine').onclick", 1)[1])
        next_word = source.split('function next', 1)[1].split('function grade', 1)[0]
        self.assertIn('clearPersonalRecording();', next_word)
        recording = source.split("$('record').onclick", 1)[1].split("$('playMine').onclick", 1)[0]
        self.assertIn('setPracticeControlsDisabled(true);', recording)
        self.assertIn('setPracticeControlsDisabled(false);', recording)
        self.assertIn('if(recordingStarting)return;', recording)
        self.assertLess(
            recording.index("recordingStarting=true;"),
            recording.index('await navigator.mediaDevices.getUserMedia'),
        )
        self.assertIn('const sessionChunks=[];', recording)
        self.assertNotIn('chunks=[]', recording)
        self.assertIn('RAW_BUFFER_CACHE_LIMIT=64', source)
        self.assertIn('CORRECTION_BUFFER_CACHE_LIMIT=32', source)
        correction_url = source.split('function correctionSelection', 1)[1].split(
            'function getCorrectionContext', 1
        )[0]
        self.assertIn('CorrectionAudio.correctionSelection', correction_url)
        self.assertIn("clarity.type='highshelf'", source)
        self.assertIn("presence.type='peaking'", source)
        native_selection = source.split('function nativePlayback', 1)[1].split(
            'function hasAlignedCorrections', 1
        )[0]
        self.assertNotIn('correctionRecordings', native_selection)
        self.assertIn('audioURL(r)', native_selection)

    def test_public_pinyin_archive_only_indexes_toned_mp3_files(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / 'corpus.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('repo/mp3/ma2.mp3', b'ID3audio')
                archive.writestr('repo/mp3/tian3.mp3', b'ID3audio')
                archive.writestr('repo/mp3/README.txt', b'ignored')
                archive.writestr('repo/other/pin1.mp3', b'ignored')
            with zipfile.ZipFile(archive_path) as archive:
                members=download_public_pinyin.corpus_members(archive)
        self.assertEqual(set(members), {'ma2','tian3'})

    def test_bad_human_syllables_have_explicit_fallback_policy(self):
        quality = json.loads(
            (ROOT / 'data' / 'correction_audio_quality.json').read_text(
                encoding='utf-8'
            )
        )
        audio_cmn_quality = quality['audio_cmn']
        public_fallbacks = {
            'ma2',
            'xian3',
            'ming4',
        }
        for key in public_fallbacks:
            self.assertEqual(
                audio_cmn_quality[key]['replacement'],
                'pinyin_public',
            )
        self.assertIsNone(audio_cmn_quality['mou3']['replacement'])
        self.assertEqual(
            quality['pinyin_public']['ao4']['replacement'],
            'audio_cmn',
        )
        self.assertEqual(
            quality['pinyin_public']['lian4']['replacement'],
            'audio_cmn',
        )
        self.assertEqual(
            set(quality['preferred_sources']),
            {
                'gai1',
                'gai2',
                'gai3',
                'gai4',
                'gei1',
                'gei2',
                'gei3',
                'gei4',
                'cang2',
                'zeng2',
            },
        )

    def test_shared_correction_policy_handles_corpus_quirks(self):
        script = """
const policy=require('./app/correction_audio.js');
const results={
  umlaut:policy.correctionKey('lü','4'),
  ju:policy.correctionSelection('ju4',{},{}),
  erhua:policy.correctionSelection('r2',{},{}),
  defaultSource:policy.correctionSelection(
    'gai1',
    {},
    {gai1:{audio_path:'audio/pinyin_public/gai1.mp3',source:'public'}}
  ),
  preferred:policy.correctionSelection(
    'gai1',
    {preferred_sources:{gai1:'pinyin_public'}},
    {gai1:{audio_path:'audio/pinyin_public/gai1.mp3',source:'public'}}
  ),
  reported:policy.correctionSelection(
    'ma2',
    {audio_cmn:{ma2:{status:'bad',replacement:'pinyin_public'}}},
    {ma2:{audio_path:'audio/pinyin_public/ma2.mp3',source:'public'}}
  ),
  blocked:policy.correctionSelection(
    'mou3',
    {audio_cmn:{mou3:{status:'bad',replacement:null}}},
    {mou3:{audio_path:'audio/pinyin_public/mou3.mp3',source:'public'}}
  ),
  badPublic:policy.correctionSelection(
    'ao4',
    {pinyin_public:{ao4:{status:'bad',replacement:'audio_cmn'}}},
    {ao4:{audio_path:'audio/pinyin_public/ao4.mp3',source:'public'}}
  ),
};
process.stdout.write(JSON.stringify(results));
"""
        output = subprocess.check_output(
            ['node', '-e', script],
            cwd=ROOT,
            text=True,
        )
        results = json.loads(output)
        self.assertEqual(results['umlaut'], 'lv4')
        self.assertTrue(results['ju']['audio_path'].endswith('cmn-jv4.mp3'))
        self.assertIsNone(results['erhua'])
        self.assertEqual(
            results['defaultSource']['audio_path'],
            'audio/audio_cmn/syllabs/cmn-gai1.mp3',
        )
        self.assertEqual(
            results['preferred']['audio_path'],
            'audio/pinyin_public/gai1.mp3',
        )
        self.assertEqual(
            results['reported']['audio_path'],
            'audio/pinyin_public/ma2.mp3',
        )
        self.assertIsNone(results['blocked'])
        self.assertEqual(
            results['badPublic']['audio_path'],
            'audio/audio_cmn/syllabs/cmn-ao4.mp3',
        )
        self.assertFalse(results['badPublic']['enhanced'])

    def test_every_required_correction_uses_primary_or_explicit_fallback(self):
        script = """
const policy=require('./app/correction_audio.js');
const words=require('./data/hsk_words.json');
const quality=require('./data/correction_audio_quality.json');
const recordings=require('./data/pinyin_public_recordings.json');
const selected={};
for(const word of words){
  const syllables=word.pinyin_syllables||[];
  for(const pinyin of syllables){
    for(const tone of ['1','2','3','4']){
      const key=policy.correctionKey(pinyin,tone);
      selected[key]=policy.correctionSelection(key,quality,recordings);
    }
  }
}
process.stdout.write(JSON.stringify(selected));
"""
        output = subprocess.check_output(
            ['node', '-e', script],
            cwd=ROOT,
            text=True,
        )
        selected = json.loads(output)
        quality = json.loads(
            (ROOT / 'data' / 'correction_audio_quality.json').read_text(
                encoding='utf-8'
            )
        )
        public = json.loads(
            (ROOT / 'data' / 'pinyin_public_recordings.json').read_text(
                encoding='utf-8'
            )
        )
        reported = {
            'gai1',
            'gai2',
            'gai3',
            'gai4',
            'gei1',
            'gei2',
            'gei3',
            'gei4',
            'cang2',
            'zeng2',
        }
        public_count = 0
        unavailable_audio_cmn = {'r1', 'r2', 'r3', 'r4'}
        for key, recording in selected.items():
            audio_cmn_review = quality['audio_cmn'].get(key, {})
            public_review = quality['pinyin_public'].get(key, {})
            preferred_source = quality['preferred_sources'].get(key)
            if (
                key in unavailable_audio_cmn
                and (
                    key not in public
                    or public_review.get('status') == 'bad'
                )
            ) or (
                audio_cmn_review.get('status') == 'bad'
                and (
                    audio_cmn_review.get('replacement') != 'pinyin_public'
                    or key not in public
                    or public_review.get('status') == 'bad'
                )
            ):
                self.assertIsNone(recording, key)
            elif (
                preferred_source == 'pinyin_public'
                or audio_cmn_review.get('replacement') == 'pinyin_public'
            ) and key in public and public_review.get('status') != 'bad':
                public_count += 1
                self.assertEqual(recording['audio_path'], public[key]['audio_path'])
                self.assertEqual(recording['source'], public[key]['source'])
                self.assertTrue(recording['enhanced'])
            else:
                self.assertEqual(
                    recording['audio_path'],
                    f"audio/audio_cmn/syllabs/cmn-{'jv4' if key == 'ju4' else key}.mp3",
                )
                self.assertFalse(recording['enhanced'])
        self.assertGreaterEqual(public_count, len(reported))
        for key in reported:
            self.assertEqual(
                selected[key]['audio_path'],
                public[key]['audio_path'],
            )
        audio_root = ROOT / 'audio'
        if audio_root.exists():
            missing = [
                recording['audio_path']
                for recording in selected.values()
                if recording and not (ROOT / recording['audio_path']).is_file()
            ]
            self.assertEqual(missing, [])

    def test_duplicate_audio_payloads_are_blocked_by_quality_policy(self):
        audio_root = ROOT / 'audio'
        if not audio_root.exists():
            self.skipTest('downloaded audio is not available')
        quality = json.loads(
            (ROOT / 'data' / 'correction_audio_quality.json').read_text(
                encoding='utf-8'
            )
        )

        corpora = {
            'audio_cmn': (
                audio_root / 'audio_cmn' / 'syllabs',
                'cmn-',
            ),
            'pinyin_public': (
                audio_root / 'pinyin_public',
                '',
            ),
        }
        for source, (directory, prefix) in corpora.items():
            for group in check_syllables.duplicate_payload_groups(
                directory.glob('*.mp3')
            ):
                keys = [path.stem.removeprefix(prefix) for path in group]
                healthy = [
                    key
                    for key in keys
                    if quality[source].get(key, {}).get('status') != 'bad'
                ]
                self.assertEqual(
                    len(healthy),
                    1,
                    f"{source} duplicate payload is not quarantined: {keys}",
                )

    def test_reported_syllables_use_fallback_at_the_correct_word_position(self):
        words = json.loads(
            (ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8')
        )
        quality = json.loads(
            (ROOT / 'data' / 'correction_audio_quality.json').read_text(
                encoding='utf-8'
            )
        )
        public = json.loads(
            (ROOT / 'data' / 'pinyin_public_recordings.json').read_text(
                encoding='utf-8'
            )
        )

        examples = {
            '保险': (1, 'xian3', 'audio/pinyin_public/xian3.mp3'),
            '命运': (0, 'ming4', 'audio/pinyin_public/ming4.mp3'),
        }
        by_word = {item['word']: item for item in words}
        for word, (position, key, expected_path) in examples.items():
            item = by_word[word]
            tones = (
                item.get('default_surface_pattern') or item['lexical_pattern']
            ).split('-')
            actual_key = (
                f"{item['pinyin_syllables'][position]}{tones[position]}"
            )
            self.assertEqual(actual_key, key)
            self.assertEqual(
                quality['audio_cmn'][key]['replacement'],
                'pinyin_public',
            )
            self.assertEqual(public[key]['audio_path'], expected_path)


if __name__ == '__main__':
    unittest.main()
