import importlib.util
import csv
import json
import math
import os
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
bootstrap_script = load_script('bootstrap.py')
configure_signing = load_script('configure_android_signing.py')


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

    def test_known_source_tone_corrections_survive_regeneration(self):
        self.assertEqual(build_hsk.TONE_OVERRIDES['L2-0044'], [4, 1, 4, 0])
        self.assertEqual(build_hsk.TONE_OVERRIDES['L4-0788'], [4, 4])
        self.assertEqual(build_hsk.TONE_OVERRIDES['L4-0853'], [3, 3])
        self.assertEqual(build_hsk.TONE_OVERRIDES['L7-2378'], [2])
        self.assertEqual(build_hsk.PINYIN_OVERRIDES['L3-0798'], 'xuè')
        self.assertEqual(build_hsk.PINYIN_OVERRIDES['L7-4885'], 'yīhuǎng')
        self.assertEqual(build_hsk.SURFACE_OVERRIDES['L4-0656'], [3, 0])
        self.assertEqual(build_hsk.SURFACE_OVERRIDES['L7-0161'], [3, 0, 4])
        surface, tags, aligned = build_hsk.sandhi_surface(
            '不一会儿',
            build_hsk.TONE_OVERRIDES['L2-0044'],
        )
        self.assertEqual(surface, [4, 2, 4, 0])
        self.assertIn('yi_before_t4', tags)
        self.assertTrue(aligned)

    def test_hsk_pinyin_and_tone_arrays_are_internally_consistent(self):
        words = json.loads(
            (ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8')
        )
        for word in words:
            syllables = word.get('pinyin_syllables') or []
            lexical = word.get('lexical_tones') or []
            self.assertEqual(len(syllables), len(lexical), word['id'])
            displayed = add_pinyin.syllable_tones(word)
            self.assertIsNotNone(displayed, word['id'])
            if all(displayed) and all(lexical):
                self.assertEqual(displayed, lexical, word['id'])

    def test_tone_csv_matches_runtime_json(self):
        words = json.loads(
            (ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8')
        )
        with (ROOT / 'data' / 'hsk_words_tones.csv').open(
            encoding='utf-8-sig',
            newline='',
        ) as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), len(words))
        for row, word in zip(rows, words):
            self.assertEqual(row['id'], word['id'])
            self.assertEqual(row['pinyin'], word['pinyin'])
            self.assertEqual(row['lexical_pattern'], word['lexical_pattern'])
            self.assertEqual(
                row['default_surface_pattern'],
                word['default_surface_pattern'],
            )
            self.assertEqual(
                row['sandhi_tags'],
                ';'.join(word['sandhi_tags']),
            )
            self.assertEqual(
                row['surface_label_needs_clip_review'],
                str(word['surface_label_needs_clip_review']),
            )

    def test_mp3_header_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / 'valid.mp3'
            invalid = Path(directory) / 'invalid.mp3'
            valid.write_bytes(b'ID3audio')
            invalid.write_bytes(b'<html>')
            self.assertTrue(download_syllables.valid_mp3(valid))
            self.assertFalse(download_syllables.valid_mp3(invalid))

    def test_downloaders_retry_transient_failures(self):
        word_downloader = (
            ROOT / 'scripts' / 'download_audio_cmn.py'
        ).read_text(encoding='utf-8')
        syllable_downloader = (
            ROOT / 'scripts' / 'download_audio_cmn_syllables.py'
        ).read_text(encoding='utf-8')
        bootstrap = (ROOT / 'scripts' / 'bootstrap.py').read_text(
            encoding='utf-8'
        )
        self.assertIn('Retry-After', word_downloader)
        self.assertIn('default=6', word_downloader)
        self.assertIn('Retry-After', syllable_downloader)
        self.assertIn("add_argument('--retries',type=int,default=6)", syllable_downloader)
        self.assertIn('def run_retry(', bootstrap)
        self.assertIn('Download batch failed; retrying resumably', bootstrap)

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

    def test_selected_tones_do_not_share_audio_payloads(self):
        script = """
const policy=require('./app/correction_audio.js');
const quality=require('./data/correction_audio_quality.json');
const recordings=require('./data/pinyin_public_recordings.json');
const words=require('./data/hsk_words.json');
const keys=new Set();
for(const word of words){
  for(const pinyin of (word.pinyin_syllables||[])){
    for(const tone of ['1','2','3','4'])keys.add(policy.correctionKey(pinyin,tone));
  }
}
const result={};
for(const mode of ['pinyin_public','audio_cmn']){
  result[mode]={};
  for(const key of keys){
    const selected=policy.correctionSelection(key,quality,recordings,mode);
    if(selected)result[mode][key]=selected.audio_path;
  }
}
process.stdout.write(JSON.stringify(result));
"""
        selections = json.loads(
            subprocess.check_output(
                ['node', '-e', script],
                cwd=ROOT,
                text=True,
            )
        )
        for mode, selected in selections.items():
            by_syllable = {}
            for key, relative_path in selected.items():
                if key[-1] not in '1234':
                    continue
                path = ROOT / relative_path
                if not path.is_file():
                    continue
                payload = check_syllables.payload_hash(path)
                syllable = key[:-1]
                self.assertNotIn(
                    payload,
                    by_syllable.setdefault(syllable, {}),
                    (
                        f'{mode} selects the same audio payload for '
                        f'{by_syllable[syllable].get(payload)} and {key}'
                    ),
                )
                by_syllable[syllable][payload] = key

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
        self.assertIn('CorrectionAudio.normalizationParameters', source)
        self.assertIn('createDynamicsCompressor()', source)
        self.assertIn('limiter.threshold.value=-3', source)
        self.assertIn("clarity.type='highshelf'", source)
        self.assertIn("presence.type='peaking'", source)
        native_selection = source.split('function nativePlayback', 1)[1].split(
            'function hasAlignedCorrections', 1
        )[0]
        self.assertNotIn('correctionRecordings', native_selection)
        self.assertIn('audioURL(r)', native_selection)

    def test_back_button_restores_previous_quiz_state(self):
        index = (ROOT / 'app' / 'index.html').read_text(encoding='utf-8')
        source = (ROOT / 'app' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="back"', index)
        self.assertIn('id="back" class="back-button" type="button" disabled', index)
        card = index.split('<section class="card"', 1)[1].split('</section>', 1)[0]
        self.assertIn('aria-label="Word navigation"', card)
        self.assertGreater(card.index('id="next"'), card.index('id="reveal"'))
        self.assertGreater(card.index('class="controls"'), card.index('id="prompt"'))
        self.assertLess(card.index('class="controls"'), card.index('class="audio-row"'))
        controls = card.split('<div class="controls"', 1)[1].split('</div>', 1)[0]
        self.assertIn('id="syllables"', controls)
        self.assertNotIn('id="next"', controls)
        self.assertIn('QUIZ_HISTORY_LIMIT=50', source)
        self.assertIn('function currentSnapshot()', source)
        self.assertIn('selectedTones:[...selectedTones]', source)
        self.assertIn('recording:currentRec', source)
        self.assertIn('function restoreToneState(state)', source)
        self.assertIn('function back(play=false)', source)
        self.assertIn('quizHistory.pop()', source)
        self.assertIn("function scrollToPractice()", source)
        self.assertIn("$('reveal').scrollIntoView", source)
        self.assertIn("document.querySelector('.card').scrollIntoView", source)
        self.assertIn(
            "$('back').onclick=()=>{back(true);scrollToPractice()}",
            source,
        )
        self.assertIn(
            "$('next').onclick=()=>{next(true);scrollToPractice()}",
            source,
        )
        self.assertIn("load().then(()=>next(true,false))", source)
        self.assertIn("quizHistory=[];next(true,false)", source)

    def test_comparison_voice_switches_all_tone_buttons(self):
        index = (ROOT / 'app' / 'index.html').read_text(encoding='utf-8')
        source = (ROOT / 'app' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="correctionSource"', index)
        self.assertIn('value="pinyin_public" selected', index)
        self.assertIn('value="audio_cmn"', index)
        self.assertIn("$('correctionSource')?.value||'pinyin_public'", source)
        self.assertIn("$('correctionSource').onchange=()=>", source)
        self.assertIn('rawPinyinBuffers.clear();', source)
        self.assertIn('correctionBuffers.clear();', source)

    def test_homograph_recordings_require_a_unique_reading(self):
        source = (ROOT / 'app' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('function readingKey(w)', source)
        self.assertIn('readingsByWord.get(w.word)', source)
        self.assertIn('r.quiz_eligible===false', source)
        self.assertNotIn('patternsByWord.get(w.word)', source)
        words = json.loads(
            (ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8')
        )
        by_word = {}
        for word in words:
            by_word.setdefault(word['word'], set()).add(
                (
                    word.get('pinyin'),
                    tuple(word.get('pinyin_syllables') or []),
                    word.get('lexical_pattern'),
                )
            )
        for word in ['还', '行', '卡', '系', '落', '露', '实在', '编辑']:
            self.assertGreater(len(by_word[word]), 1, word)

    def test_mismatched_native_reading_is_excluded(self):
        recordings = json.loads(
            (ROOT / 'data' / 'recordings.json').read_text(encoding='utf-8')
        )
        cough = next(
            recording
            for recording in recordings
            if recording['audio_path'] == 'audio/audio_cmn/咳/cmn-咳.mp3'
        )
        self.assertIs(cough['quiz_eligible'], False)
        self.assertIn('hai', cough['notes'])
        self.assertIn('ke2', cough['notes'])
        replacement = next(
            recording
            for recording in recordings
            if recording.get('hsk_id') == 'L5-0436'
            and recording.get('quiz_eligible') is not False
        )
        self.assertEqual(
            replacement['audio_path'],
            'audio/audio_cmn/syllabs/cmn-ke2.mp3',
        )
        self.assertEqual(replacement['surface_pattern'], '2')
        sleep = next(
            recording
            for recording in recordings
            if recording['audio_path'] == 'audio/audio_cmn/觉/cmn-觉.mp3'
        )
        self.assertIs(sleep['quiz_eligible'], False)
        self.assertIn('jue2', sleep['notes'])
        self.assertIn('jiao4', sleep['notes'])
        sleep_replacement = next(
            recording
            for recording in recordings
            if recording.get('hsk_id') == 'L6-0437'
            and recording.get('quiz_eligible') is not False
        )
        self.assertEqual(
            sleep_replacement['audio_path'],
            'audio/audio_cmn/syllabs/cmn-jiao4.mp3',
        )
        self.assertEqual(sleep_replacement['surface_pattern'], '4')
        blood = next(
            recording
            for recording in recordings
            if recording['audio_path'] == 'audio/audio_cmn/血/cmn-血.mp3'
        )
        self.assertIs(blood['quiz_eligible'], False)
        self.assertIn('xie3', blood['notes'])
        self.assertIn('xue4', blood['notes'])
        blood_replacement = next(
            recording
            for recording in recordings
            if recording.get('hsk_id') == 'L3-0798'
            and recording.get('quiz_eligible') is not False
        )
        self.assertEqual(
            blood_replacement['audio_path'],
            'audio/audio_cmn/syllabs/cmn-xue4.mp3',
        )

    def test_native_reading_audit_classifies_base_mismatches(self):
        audit = load_script('audit_native_readings.py')
        expected = [['ke2']]
        self.assertEqual(audit.classify(['ke2'], expected), 'exact')
        self.assertEqual(audit.classify(['ke3'], expected), 'base_match')
        self.assertEqual(audit.classify(['hai1'], expected), 'review')
        self.assertEqual(audit.classify([], expected), 'unrecognized')
        self.assertEqual(
            audit.classify(['zhuo2', 'mo5'], [['zuo2', 'mo5']], '琢磨', '琢磨'),
            'text_match',
        )
        self.assertIn('hai', audit.polyphonic_bases('咳'))
        self.assertIn('ke', audit.polyphonic_bases('咳'))
        self.assertEqual(
            audit.classify(['ke2'], expected, '咳', '咳'),
            'text_match',
        )

    def test_dual_tracker_tone_contradictions_are_quarantined(self):
        quality = json.loads(
            (ROOT / 'data' / 'correction_audio_quality.json').read_text(
                encoding='utf-8'
            )
        )
        public_fallbacks = {
            'ran4',
            're1',
            'ren4',
            'rong2',
            'rou1',
            'ruan1',
            'wa1',
            'wang1',
            'zhen2',
            'niu2',
        }
        for key in public_fallbacks:
            self.assertEqual(quality['audio_cmn'][key]['status'], 'bad')
            self.assertEqual(
                quality['audio_cmn'][key]['replacement'],
                'pinyin_public',
            )
        for key in {'rang1', 'rui1'}:
            self.assertEqual(quality['audio_cmn'][key]['status'], 'bad')
            self.assertIsNone(quality['audio_cmn'][key]['replacement'])
        self.assertEqual(quality['pinyin_public']['rang1']['status'], 'bad')
        self.assertIsNone(quality['pinyin_public']['rang1']['replacement'])
        self.assertEqual(
            quality['audio_cmn']['nin2']['replacement'],
            'pinyin_public',
        )
        for key in {'tou3', 'cuo3', 'miao1'}:
            self.assertEqual(
                quality['pinyin_public'][key]['replacement'],
                'audio_cmn',
            )

    def test_correction_identity_audit_detects_wrong_bases(self):
        identity = load_script('audit_correction_identity.py')
        self.assertEqual(identity.classify('zhen', ['zhen']), 'match')
        self.assertEqual(identity.classify('zhen', ['zheng']), 'review')
        self.assertEqual(identity.classify('zhen', []), 'unrecognized')

    def test_confirmed_multiword_reading_mismatches_are_excluded(self):
        recordings = json.loads(
            (ROOT / 'data' / 'recordings.json').read_text(encoding='utf-8')
        )
        by_word = {
            recording['word']: recording
            for recording in recordings
            if recording.get('recording_type') == 'isolated_word'
        }
        expected_notes = {
            '一技之长': ('zhang3', 'chang2'),
            '降落': ('xiang', 'jiang4'),
            '着手': ('zhao', 'zhuo2'),
            '南方': ('南风', '南方'),
            '大都': ('dou1', 'du1'),
            '追究': ('追求', '追究'),
            '缺陷': ('曲线', '缺陷'),
            '一身': ('一生', '一身'),
            '羡慕': ('谢幕', '羡慕'),
            '凑巧': ('chou4-jiao3', 'cou4-qiao3'),
            '师范': ('吃饭', '师范'),
            '轿车': ('校车', '轿车'),
            '恳求': ('kan-qiu2', 'ken3-qiu2'),
            '本着': ('ban3-zhe', 'ben3-zhe'),
            '凳子': ('dong4-zi', 'deng4-zi'),
            '登记': ('冬季', '登记'),
            '前方': ('前锋', '前方'),
            '陈旧': ('成就', '陈旧'),
            '工地': ('土地', '工地'),
        }
        for word, markers in expected_notes.items():
            self.assertIs(by_word[word]['quiz_eligible'], False)
            for marker in markers:
                self.assertIn(marker, by_word[word]['notes'])

    def test_ambiguous_surface_groupings_require_clip_labels(self):
        source = (ROOT / 'app' / 'app.js').read_text(encoding='utf-8')
        self.assertIn(
            'w.surface_label_needs_clip_review&&!r.surface_pattern',
            source,
        )
        words = json.loads(
            (ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8')
        )
        ambiguous = {
            word['word']
            for word in words
            if word.get('surface_label_needs_clip_review')
        }
        self.assertEqual(
            ambiguous,
            {'水产品', '此起彼伏', '导火索', '岂有此理'},
        )

    def test_tone_labels_and_neutral_feedback_are_explicit(self):
        source = (ROOT / 'app' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("1:'1st tone'", source)
        self.assertIn("2:'2nd tone'", source)
        self.assertIn("3:'3rd tone'", source)
        self.assertIn("4:'4th tone'", source)
        self.assertIn(
            'Neutral tone is context-dependent and has no standalone comparison clip.',
            source,
        )

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

    def test_bootstrap_uses_pinned_portable_sources(self):
        snapshots = json.loads(
            (ROOT / 'config' / 'source_snapshots.json').read_text(
                encoding='utf-8'
            )
        )
        for source in snapshots.values():
            self.assertRegex(source['revision'], r'^[0-9a-f]{40}$')
            self.assertTrue(source['samples'])
        toolchain = json.loads(
            (ROOT / 'config' / 'toolchain.json').read_text(encoding='utf-8')
        )
        self.assertRegex(toolchain['node']['version'], r'^22\.\d+\.\d+$')
        self.assertEqual(len(toolchain['node']['sha256']), 4)
        self.assertRegex(toolchain['jdk']['version'], r'^21\.\d+\.\d+\+\d+$')
        self.assertEqual(len(toolchain['jdk']['sha256']), 4)
        lockfile = (ROOT / 'package-lock.json').read_text(encoding='utf-8')
        self.assertNotIn('pkgs.visualstudio.com', lockfile)
        self.assertNotIn('ms-feed-', lockfile)
        self.assertEqual((ROOT / '.nvmrc').read_text().strip(), '22')
        requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
        self.assertIn('imageio-ffmpeg==0.6.0', requirements)
        bootstrap = (ROOT / 'scripts' / 'bootstrap.py').read_text(
            encoding='utf-8'
        )
        self.assertIn("'config' / 'source_snapshots.json'", bootstrap)
        self.assertIn("'--revision'", bootstrap)
        self.assertIn("'scripts/validate_setup.py'", bootstrap)
        self.assertIn("'npm', 'run', 'build:mobile'", bootstrap)
        self.assertIn('def ensure_node()', bootstrap)
        self.assertIn('def ensure_ffmpeg()', bootstrap)
        self.assertIn('def ensure_jdk()', bootstrap)
        self.assertIn('failed SHA-256 verification', bootstrap)

    def test_bootstrap_installs_persistent_node_shims_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            toolchain = root / 'toolchain'
            toolchain_bin = toolchain / 'bin'
            toolchain_bin.mkdir(parents=True)
            for name in ('node', 'npm', 'npx'):
                executable = toolchain_bin / name
                executable.write_text(f'#!/bin/sh\necho {name}\n')
                executable.chmod(0o755)
            home = root / 'home'
            home.mkdir()
            profile = home / '.zprofile'
            profile.write_text('export EXISTING=value\n')
            original_path = os.environ.get('PATH', '')
            try:
                bootstrap_script.install_user_node_shims(
                    toolchain,
                    home=home,
                    shell='/bin/zsh',
                )
                bootstrap_script.install_user_node_shims(
                    toolchain,
                    home=home,
                    shell='/bin/zsh',
                )
            finally:
                os.environ['PATH'] = original_path
            for name in ('node', 'npm', 'npx'):
                self.assertEqual(
                    (home / '.local' / 'bin' / name).resolve(),
                    (toolchain_bin / name).resolve(),
                )
            profile_text = profile.read_text()
            self.assertEqual(
                profile_text.count('export PATH="$HOME/.local/bin:$PATH"'),
                1,
            )

    def test_bootstrap_configures_an_existing_android_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = root / 'sdk'
            platform_tools = sdk / 'platform-tools'
            platform_tools.mkdir(parents=True)
            adb = platform_tools / 'adb'
            adb.write_text('#!/bin/sh\n')
            adb.chmod(0o755)
            home = root / 'home'
            home.mkdir()
            project = root / 'project'
            original_path = os.environ.get('PATH', '')
            try:
                selected = bootstrap_script.configure_android_sdk(
                    sdk_root=sdk,
                    home=home,
                    shell='/bin/zsh',
                    project_root=project,
                )
            finally:
                os.environ['PATH'] = original_path
            self.assertEqual(selected, sdk.resolve())
            self.assertEqual(
                (home / '.local' / 'bin' / 'adb').resolve(),
                adb.resolve(),
            )
            self.assertEqual(
                (project / 'android' / 'local.properties').read_text(),
                f'sdk.dir={sdk.resolve()}\n',
            )

    def test_sensitive_local_files_are_ignored(self):
        sensitive = [
            'keystore.properties',
            'release.jks',
            'signing.keystore',
            'certificate.p12',
            'certificate.pfx',
            'private.pem',
            'private.key',
            '.env',
            '.env.local',
            'android/local.properties',
            'android/app/google-services.json',
            'ios/App/GoogleService-Info.plist',
        ]
        for path in sensitive:
            result = subprocess.run(
                ['git', 'check-ignore', '--quiet', path],
                cwd=ROOT,
            )
            self.assertEqual(result.returncode, 0, path)
        example = subprocess.run(
            ['git', 'check-ignore', '--quiet', '.env.example'],
            cwd=ROOT,
        )
        self.assertNotEqual(example.returncode, 0)

    def test_signing_configuration_is_private_and_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            properties = root / 'keystore.properties'
            keystore = root / 'release.jks'
            configure_signing.write_signing_properties(
                properties,
                keystore,
                'mandarin-tone-trainer',
                r'store\password',
                r'key\password',
            )
            self.assertEqual(properties.stat().st_mode & 0o777, 0o600)
            content = properties.read_text()
            self.assertIn(f'storeFile={keystore.resolve()}', content)
            self.assertIn(r'storePassword=store\\password', content)
            self.assertIn(r'keyPassword=key\\password', content)

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
            'pao1',
            'jie4',
            'zhen4',
            'zan1',
        }
        for key in public_fallbacks:
            self.assertEqual(
                audio_cmn_quality[key]['replacement'],
                'pinyin_public',
            )
        self.assertEqual(audio_cmn_quality['mou3']['replacement'], 'isolated_word')
        self.assertEqual(
            audio_cmn_quality['mou3']['replacement_audio_path'],
            'audio/audio_cmn/某/cmn-某.mp3',
        )
        self.assertEqual(
            quality['pinyin_public']['ao4']['replacement'],
            'audio_cmn',
        )
        self.assertEqual(
            quality['pinyin_public']['lian4']['replacement'],
            'audio_cmn',
        )
        self.assertEqual(
            quality['pinyin_public']['xiang1']['replacement'],
            'audio_cmn',
        )

    def test_shared_correction_policy_handles_corpus_quirks(self):
        script = """
const policy=require('./app/correction_audio.js');
const results={
  umlaut:policy.correctionKey('lü','4'),
  ju:policy.correctionSelection('ju4',{},{}),
  erhua:policy.correctionSelection('r2',{},{}),
  primary:policy.correctionSelection(
    'gai1',
    {},
    {gai1:{audio_path:'audio/pinyin_public/gai1.mp3',source:'public'}}
  ),
  human:policy.correctionSelection(
    'gai1',
    {},
    {gai1:{audio_path:'audio/pinyin_public/gai1.mp3',source:'public'}},
    'audio_cmn'
  ),
  reported:policy.correctionSelection(
    'ma2',
    {audio_cmn:{ma2:{status:'bad',replacement:'pinyin_public'}}},
    {ma2:{audio_path:'audio/pinyin_public/ma2.mp3',source:'public'}}
  ),
  blocked:policy.correctionSelection(
    'test3',
    {audio_cmn:{test3:{status:'bad',replacement:null}}},
    {test3:{audio_path:'audio/pinyin_public/test3.mp3',source:'public'}}
  ),
  humanBlocked:policy.correctionSelection(
    'test3',
    {audio_cmn:{test3:{status:'bad',replacement:null}}},
    {test3:{audio_path:'audio/pinyin_public/test3.mp3',source:'public'}},
    'audio_cmn'
  ),
  isolated:policy.correctionSelection(
    'mou3',
    {audio_cmn:{mou3:{
      status:'bad',
      replacement:'isolated_word',
      replacement_audio_path:'audio/audio_cmn/某/cmn-某.mp3',
      replacement_source:'audio_cmn',
    }}},
    {}
  ),
  badPublic:policy.correctionSelection(
    'ao4',
    {pinyin_public:{ao4:{status:'bad',replacement:'audio_cmn'}}},
    {ao4:{audio_path:'audio/pinyin_public/ao4.mp3',source:'public'}}
  ),
  normalization:{
    quiet:policy.normalizationGain([Float32Array.from([.02,-.02,.02,-.02])]),
    loud:policy.normalizationGain([Float32Array.from([.4,-.4])]),
    peakLimited:policy.normalizationGain([
      Float32Array.from([
        .8,-.8,...Array(49).fill(.011),...Array(49).fill(-.011)
      ])
    ]),
    silent:policy.normalizationGain([Float32Array.from([0,0,0])]),
    dc:policy.normalizationParameters([Float32Array.from([.08,.1,.12])]),
  },
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
            results['primary']['audio_path'],
            'audio/pinyin_public/gai1.mp3',
        )
        self.assertEqual(
            results['human']['audio_path'],
            'audio/audio_cmn/syllabs/cmn-gai1.mp3',
        )
        self.assertEqual(
            results['reported']['audio_path'],
            'audio/pinyin_public/ma2.mp3',
        )
        self.assertEqual(
            results['blocked']['audio_path'],
            'audio/pinyin_public/test3.mp3',
        )
        self.assertIsNone(results['humanBlocked'])
        self.assertEqual(
            results['isolated']['audio_path'],
            'audio/audio_cmn/某/cmn-某.mp3',
        )
        self.assertEqual(
            results['badPublic']['audio_path'],
            'audio/audio_cmn/syllabs/cmn-ao4.mp3',
        )
        self.assertFalse(results['badPublic']['enhanced'])
        self.assertAlmostEqual(results['normalization']['quiet'], 8, places=6)
        self.assertAlmostEqual(results['normalization']['loud'], 0.4, places=6)
        self.assertAlmostEqual(
            results['normalization']['peakLimited'],
            1.125,
            places=6,
        )
        self.assertEqual(results['normalization']['silent'], 1)
        self.assertAlmostEqual(
            results['normalization']['dc']['offsets'][0],
            0.1,
            places=6,
        )

    def test_every_required_correction_uses_primary_or_explicit_fallback(self):
        script = """
const policy=require('./app/correction_audio.js');
const words=require('./data/hsk_words.json');
const quality=require('./data/correction_audio_quality.json');
const recordings=require('./data/pinyin_public_recordings.json');
const selected={},human={};
for(const word of words){
  const syllables=word.pinyin_syllables||[];
  for(const pinyin of syllables){
    for(const tone of ['1','2','3','4']){
      const key=policy.correctionKey(pinyin,tone);
      selected[key]=policy.correctionSelection(key,quality,recordings);
      human[key]=policy.correctionSelection(
        key,quality,recordings,'audio_cmn'
      );
    }
  }
}
process.stdout.write(JSON.stringify({selected,human}));
"""
        output = subprocess.check_output(
            ['node', '-e', script],
            cwd=ROOT,
            text=True,
        )
        selections = json.loads(output)
        selected = selections['selected']
        human = selections['human']
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
        words = json.loads(
            (ROOT / 'data' / 'hsk_words.json').read_text(encoding='utf-8')
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
            'pao1',
            'jie4',
            'zhen4',
            'zan1',
        }
        public_count = 0
        unavailable_audio_cmn = {'r1', 'r2', 'r3', 'r4'}
        for key, recording in selected.items():
            audio_cmn_review = quality['audio_cmn'].get(key, {})
            public_review = quality['pinyin_public'].get(key, {})
            if audio_cmn_review.get('replacement_audio_path'):
                self.assertEqual(
                    recording['audio_path'],
                    audio_cmn_review['replacement_audio_path'],
                )
                self.assertFalse(recording['enhanced'])
            elif (
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
            elif key in public and public_review.get('status') != 'bad':
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
        self.assertGreater(public_count, 1000)
        for key, recording in human.items():
            audio_cmn_review = quality['audio_cmn'].get(key, {})
            public_review = quality['pinyin_public'].get(key, {})
            public_available = (
                key in public
                and public_review.get('status') != 'bad'
            )
            if audio_cmn_review.get('replacement_audio_path'):
                self.assertEqual(
                    recording['audio_path'],
                    audio_cmn_review['replacement_audio_path'],
                )
            elif audio_cmn_review.get('status') == 'bad':
                if (
                    audio_cmn_review.get('replacement') == 'pinyin_public'
                    and public_available
                ):
                    self.assertEqual(
                        recording['audio_path'],
                        public[key]['audio_path'],
                    )
                else:
                    self.assertIsNone(recording, key)
            elif key in unavailable_audio_cmn:
                if public_available:
                    self.assertEqual(
                        recording['audio_path'],
                        public[key]['audio_path'],
                    )
                else:
                    self.assertIsNone(recording, key)
            else:
                self.assertEqual(
                    recording['audio_path'],
                    f"audio/audio_cmn/syllabs/cmn-{'jv4' if key == 'ju4' else key}.mp3",
                )
        for key in reported:
            self.assertEqual(
                selected[key]['audio_path'],
                public[key]['audio_path'],
            )
            self.assertEqual(
                human[key]['audio_path'],
                public[key]['audio_path'],
            )
        for word in words:
            tones = (
                word.get('default_surface_pattern')
                or word.get('lexical_pattern')
                or ''
            ).split('-')
            syllables = word.get('pinyin_syllables') or []
            if len(tones) != len(syllables):
                continue
            for pinyin, tone in zip(syllables, tones):
                if tone == 'N':
                    continue
                key = f"{pinyin.replace('ü', 'v')}{tone}"
                self.assertIsNotNone(selected[key], f"{word['word']} requires {key}")
                self.assertIsNotNone(human[key], f"{word['word']} requires {key}")
        audio_root = ROOT / 'audio'
        if audio_root.exists():
            missing = [
                recording['audio_path']
                for recording in [*selected.values(), *human.values()]
                if recording and not (ROOT / recording['audio_path']).is_file()
            ]
            self.assertEqual(missing, [])

    def test_correction_manifest_keys_match_paths_and_quality_entries(self):
        public = json.loads(
            (ROOT / 'data' / 'pinyin_public_recordings.json').read_text(
                encoding='utf-8'
            )
        )
        quality = json.loads(
            (ROOT / 'data' / 'correction_audio_quality.json').read_text(
                encoding='utf-8'
            )
        )
        for key, recording in public.items():
            self.assertEqual(Path(recording['audio_path']).stem, key)
            self.assertTrue(recording['source_url'].endswith(f'/{key}.mp3'))
        self.assertLessEqual(set(quality['pinyin_public']), set(public))

        audio_root = ROOT / 'audio'
        if not audio_root.exists():
            return
        manifest_paths = {
            recording['audio_path']
            for recording in public.values()
        }
        downloaded_public = {
            path.relative_to(ROOT).as_posix()
            for path in (audio_root / 'pinyin_public').glob('*.mp3')
        }
        self.assertEqual(downloaded_public, manifest_paths)
        for key, recording in public.items():
            self.assertTrue((ROOT / recording['audio_path']).is_file(), key)
        audio_cmn_keys = set()
        for path in (audio_root / 'audio_cmn' / 'syllabs').glob('cmn-*.mp3'):
            key = path.stem.removeprefix('cmn-')
            self.assertIn(key[-1], '12345', path.name)
            self.assertTrue(key[:-1].replace('_', '').isalpha(), path.name)
            audio_cmn_keys.add(key)
        self.assertEqual(len(audio_cmn_keys), 1707)
        for key, review in quality['audio_cmn'].items():
            self.assertIn(key, audio_cmn_keys)
            replacement_path = review.get('replacement_audio_path')
            if replacement_path:
                self.assertTrue((ROOT / replacement_path).is_file(), key)

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
