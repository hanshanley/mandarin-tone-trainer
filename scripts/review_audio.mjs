import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import AudioReview from '../app/audio_review.js';
import CorrectionAudio from '../app/correction_audio.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readJSON = relative => JSON.parse(fs.readFileSync(path.join(ROOT, relative), 'utf8'));

export function loadReviewData() {
  const imported = readJSON('data/mandarin_native_recordings.json');
  return {
    words: readJSON('data/hsk_words.json'),
    recordings: [
      ...readJSON('data/recordings.json'),
      ...imported.recordings,
    ],
    publicRecordings: readJSON('data/pinyin_public_recordings.json'),
    quality: readJSON('data/correction_audio_quality.json'),
    ledger: readJSON('data/audio_reviews.json'),
    acousticLedger: readJSON('data/acoustic_reviews.json'),
    snapshots: readJSON('config/source_snapshots.json'),
    exploreVocabularyCount: imported.explore_vocabulary?.length || 0,
  };
}

export function candidatesFor(data) {
  const candidates = new Map();
  const mapped = new Set();
  for (const {word, recording} of AudioReview.nativeCandidates(data.words, data.recordings)) {
    mapped.add(recording.audio_path);
    const descriptor = AudioReview.nativeDescriptor(word, recording);
    candidates.set(AudioReview.identity(descriptor), {
      ...descriptor,
      source_url: recording.source_url,
      license: recording.license,
      blocked_reason: AudioReview.nativeBlockReason(recording),
      reference_url: `https://mandarin-native.com/#${encodeURIComponent(`word/${word.word}`)}`,
    });
    if (recording.source === 'audio_cmn' && word.pinyin_syllables?.length === 1
        && /^[1-4]$/.test(descriptor.surface_pattern)) {
      const comparison = AudioReview.comparisonDescriptor(
        CorrectionAudio.correctionKey(word.pinyin_syllables[0], descriptor.surface_pattern), recording,
      );
      candidates.set(AudioReview.identity(comparison), {
        ...comparison,
        source_url: recording.source_url,
        license: recording.license,
        blocked_reason: AudioReview.nativeBlockReason(recording),
      });
    }
  }
  for (const recording of data.recordings) {
    if (recording.source !== 'mandarin_native' || mapped.has(recording.audio_path)) continue;
    const contextual = recording.recording_type === 'context_sentence';
    const descriptor = {
      kind: contextual ? 'context_native' : 'unmapped_native',
      audio_path: recording.audio_path, key: recording.source_audio_key,
    };
    candidates.set(AudioReview.identity(descriptor), {
      ...descriptor,
      source_url: recording.source_url,
      license: recording.license,
      blocked_reason: contextual
        ? 'Complete contextual sentence, not an isolated word; requires separate alignment, listening review, and verified reuse permission'
        : 'No vocabulary reading mapped; requires listening identification and verified reuse permission',
      ...(contextual ? { context_words: recording.context_words, source_entry_ids: recording.source_entry_ids } : {}),
    });
  }
  const keys = new Set(data.words.flatMap(word =>
    (word.pinyin_syllables || []).flatMap(base => ['1', '2', '3', '4'].map(tone =>
      CorrectionAudio.correctionKey(base, tone)))));
  for (const key of keys) {
    for (const source of ['pinyin_public', 'audio_cmn']) {
      const review = data.quality[source]?.[key];
      const audioCmnKey = key === 'ju4' ? 'jv4' : key;
      const recording = source === 'pinyin_public'
        ? data.publicRecordings[key]
        : {
            audio_path: `audio/audio_cmn/syllabs/cmn-${audioCmnKey}.mp3`,
            source_url: `${data.snapshots.audio_cmn.repository}/blob/${data.snapshots.audio_cmn.revision}/${data.snapshots.audio_cmn.syllable_quality}/syllabs/cmn-${audioCmnKey}.mp3`,
            license: 'CC-BY-SA',
          };
      if (recording && !['r1', 'r2', 'r3', 'r4'].includes(key)) {
        const descriptor = AudioReview.comparisonDescriptor(key, recording);
        candidates.set(AudioReview.identity(descriptor), {
          ...descriptor,
          source_url: recording.source_url,
          license: recording.license,
          blocked_reason: review?.status === 'bad' ? review.reason || 'Quarantined comparison recording' : null,
        });
      }
      if (review?.replacement_audio_path) {
        const replacement = data.recordings.find(item => item.audio_path === review.replacement_audio_path);
        if (!replacement) throw new Error(`Replacement lacks provenance: ${review.replacement_audio_path}`);
        const descriptor = AudioReview.comparisonDescriptor(key, { audio_path: review.replacement_audio_path });
        candidates.set(AudioReview.identity(descriptor), {
          ...descriptor,
          source_url: replacement.source_url,
          license: replacement.license,
          blocked_reason: replacement.source === 'mandarin_native'
            ? 'Imported word and sentence recordings are not isolated-tone comparison sources'
            : AudioReview.nativeBlockReason(replacement),
        });
      }
    }
  }
  return candidates;
}

export function audioHash(relativePath, root = ROOT) {
  const file = path.resolve(root, relativePath);
  if (!file.startsWith(`${root}${path.sep}`) || !fs.realpathSync(file).startsWith(`${fs.realpathSync(root)}${path.sep}`)) {
    throw new Error(`Audio path escapes repository: ${relativePath}`);
  }
  return createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

export function validateLedger(data, root = ROOT, { allowLocalOnly = true } = {}) {
  const index = AudioReview.createIndex(data.ledger, data.acousticLedger || null, { allowLocalOnly, sourceRecordings: data.recordings });
  const candidates = candidatesFor(data);
  const recordingsByPath = new Map(data.recordings.map(recording => [recording.audio_path, recording]));
  for (const approval of index.values()) {
    const candidate = candidates.get(AudioReview.identity(approval));
    if (!candidate) throw new Error(`Approval labels do not match the current corpus: ${approval.audio_path}`);
    const blocked = approval.kind === 'native'
      ? AudioReview.nativeBlockReason(recordingsByPath.get(approval.audio_path), approval)
      : candidate.blocked_reason;
    if (blocked) throw new Error(`Quarantined audio cannot be approved: ${approval.audio_path}`);
    if (approval.source_url !== candidate.source_url || approval.license !== candidate.license) {
      throw new Error(`Approval provenance differs from the corpus: ${approval.audio_path}`);
    }
    if (audioHash(approval.audio_path, root) !== approval.sha256) {
      throw new Error(`Audio changed since listening review: ${approval.audio_path}`);
    }
  }
  return index;
}

export function practiceInventory(data, index) {
  const recordingsByWord = new Map();
  for (const {word, recording} of AudioReview.nativeCandidates(data.words, data.recordings)) {
    if (!recordingsByWord.has(word.id)) recordingsByWord.set(word.id, []);
    recordingsByWord.get(word.id).push(recording);
  }
  const audio = new Set();
  const eligibleWords = [];
  const recordingLabelPairs = [];
  for (const word of data.words) {
    const natives = (recordingsByWord.get(word.id) || []).filter(recording =>
      AudioReview.nativeApproval(index, word, recording)
      && word.pinyin_syllables.every((base, position) => {
        const tone = (recording.surface_pattern || word.default_surface_pattern || word.lexical_pattern).split('-')[position];
        return tone === 'N' || ['pinyin_public', 'audio_cmn'].every(mode =>
          AudioReview.correctionSelection(CorrectionAudio, CorrectionAudio.correctionKey(base, tone),
            data.quality, data.publicRecordings, index, mode));
      }));
    if (!natives.length) continue;
    const comparisons = (word.pinyin_syllables || []).flatMap(base =>
      ['1', '2', '3', '4'].flatMap(tone => ['pinyin_public', 'audio_cmn'].map(mode =>
        AudioReview.correctionSelection(CorrectionAudio, CorrectionAudio.correctionKey(base, tone),
          data.quality, data.publicRecordings, index, mode))));
    eligibleWords.push(word.id);
    for (const recording of natives) recordingLabelPairs.push({ word_id: word.id, audio_path: recording.audio_path });
    for (const recording of [...natives, ...comparisons.filter(Boolean)]) audio.add(recording.audio_path);
  }
  return { eligibleWords, recordingLabelPairs, audio };
}

export function coverageReport(data, index, decisions = null) {
  if (decisions && decisions.pipeline_sha256 !== data.acousticLedger?.pipeline_sha256) {
    throw new Error('Exclusion report is stale: its pipeline does not match the acoustic ledger');
  }
  const inventory = practiceInventory(data, index);
  const pairsByWord = new Map();
  for (const pair of AudioReview.nativeCandidates(data.words, data.recordings)) {
    if (!pairsByWord.has(pair.word.id)) pairsByWord.set(pair.word.id, []);
    pairsByWord.get(pair.word.id).push(pair);
  }
  const eligible = new Set(inventory.eligibleWords);
  const findings = new Map((decisions?.findings || []).map(item => [item.label_identity, item.reason]));
  const stages = new Map([
    ['known native recording quarantine', 'quarantined'],
    ['missing or stale acoustic/recognition evidence', 'missing_evidence'],
    ['independent ASR did not confirm the expected syllables', 'identity_unresolved'],
    ['neither raw nor prepared ASR resolved the syllable sequence', 'identity_unresolved'],
    ['raw and prepared ASR disagree on syllable identity', 'identity_unresolved'],
    ['polyphonic single-character identity remains ambiguous', 'identity_unresolved'],
    ['neutral-tone reduction needs a separate prosodic confidence model', 'neutral_tone_unresolved'],
    ['no reliable syllable alignment or speaker-register reference', 'alignment_unresolved'],
    ['tone evidence is ambiguous or contradicts the label', 'tone_unresolved'],
    ['no distinct screened comparison for the expected tone', 'comparison_unresolved'],
  ]);
  const stageOrder = [
    'quarantined', 'missing_evidence', 'identity_unresolved', 'neutral_tone_unresolved',
    'alignment_unresolved', 'tone_unresolved', 'comparison_unresolved',
  ];
  const perWord = data.words.map(word => {
    const pairs = pairsByWord.get(word.id) || [];
    const reasons = new Set();
    let assessed = 0;
    for (const { recording } of pairs) {
      if (AudioReview.nativeApproval(index, word, recording)) assessed++;
      const descriptor = AudioReview.nativeDescriptor(word, recording);
      const reason = findings.get(AudioReview.identity(descriptor));
      if (reason) reasons.add(reason);
    }
    const status = eligible.has(word.id) ? 'eligible'
      : !pairs.length ? 'no_isolated_recording'
        : assessed ? 'missing_correct_tone_reference' : 'native_screening_unresolved';
    const candidateStages = [...reasons].map(reason => stages.get(reason)).filter(Boolean);
    const primaryBlocker = status === 'eligible' ? null
      : status === 'no_isolated_recording' ? 'no_isolated_recording'
        : assessed ? 'comparison_unresolved'
          : stageOrder.filter(stage => candidateStages.includes(stage)).at(-1) || 'screening_details_unavailable';
    return {
      word_id: word.id, word: word.word, pinyin: word.pinyin, status,
      candidate_recordings: pairs.length, assessed_recordings: assessed,
      primary_blocker: primaryBlocker,
      exclusion_reasons: [...reasons].sort(),
    };
  });
  const statuses = Object.fromEntries([
    'eligible', 'no_isolated_recording', 'missing_correct_tone_reference', 'native_screening_unresolved',
  ].map(status => [status, perWord.filter(row => row.status === status).length]));
  if (Object.values(statuses).reduce((sum, count) => sum + count, 0) !== data.words.length) {
    throw new Error('Coverage categories do not reconcile to vocabulary size');
  }
  const bySource = {};
  for (const recording of data.recordings) {
    const source = recording.source || 'unspecified';
    if (!bySource[source]) bySource[source] = new Set();
    bySource[source].add(recording.audio_path);
  }
  const imported = data.recordings.filter(recording => recording.source === 'mandarin_native');
  const reasons = {};
  const primaryBlockers = {};
  for (const row of perWord) {
    if (row.status === 'eligible') continue;
    primaryBlockers[row.primary_blocker] = (primaryBlockers[row.primary_blocker] || 0) + 1;
    for (const reason of row.exclusion_reasons) reasons[reason] = (reasons[reason] || 0) + 1;
  }
  return {
    version: 1,
    pipeline_sha256: data.acousticLedger?.pipeline_sha256 || null,
    counting_units: {
      practice_entries: 'distinct vocabulary IDs with at least one qualifying initial recording and correct-tone references',
      initial_recording_examples: 'qualifying vocabulary-ID/audio-path pairs; voices can create multiple examples of one entry',
      audio_files: 'distinct local audio paths; never multiplied by words or tone choices',
    },
    vocabulary: { total: data.words.length, ...statuses },
    initial_recording_examples: inventory.recordingLabelPairs.length,
    initial_audio_files: new Set(inventory.recordingLabelPairs.map(pair => pair.audio_path)).size,
    reachable_audio_files: inventory.audio.size,
    imported_audio_files_used: [...inventory.audio].filter(relative => relative.startsWith('audio/mandarin_native/')).length,
    source_recording_files: Object.fromEntries(Object.entries(bySource).map(([key, paths]) => [key, paths.size])),
    imported_source: {
      word_files: imported.filter(recording => recording.recording_type === 'word_candidate').length,
      context_files: imported.filter(recording => recording.recording_type === 'context_sentence').length,
      explore_vocabulary_entries: data.exploreVocabularyCount ?? null,
      context_is_not_isolated_practice: true,
    },
    exclusion_reason_word_counts: reasons,
    exclusion_reason_counts_overlap: true,
    primary_blocker_word_counts: primaryBlockers,
    primary_blocker_rule: 'furthest completed screening stage among candidate recordings; mutually exclusive',
    entries: perWord,
  };
}

export function attachToneScreen(candidates, report) {
  if (report.certifies_accuracy !== false
      || !['whole_comparison_corpus', 'selected_keys'].includes(report.scope)
      || !report.sources || typeof report.sources !== 'object' || Array.isArray(report.sources)) {
    throw new Error('Invalid tone-screen report; automated results must not certify accuracy');
  }
  const byClip = new Map();
  for (const items of Object.values(report.sources)) {
    if (!Array.isArray(items)) throw new Error('Invalid tone-screen source entries');
    for (const item of items) {
      if (!item.audio_path || !item.key || !/^[a-f0-9]{64}$/.test(item.sha256 || '')
          || !['review', 'screened_only'].includes(item.status) || !item.reason) {
        throw new Error('Invalid tone-screen finding');
      }
      const key = JSON.stringify([item.audio_path, item.key]);
      if (byClip.has(key)) throw new Error(`Duplicate tone-screen finding: ${item.audio_path}`);
      byClip.set(key, item);
    }
  }
  const output = candidates.map(candidate => {
    if (candidate.kind !== 'comparison') return candidate;
    const finding = byClip.get(JSON.stringify([candidate.audio_path, candidate.key]));
    if (!finding) return {
      ...candidate,
      tone_screen: { status: 'not_screened', reason: 'No matching comparison screen; listening review still required' },
    };
    if (finding.sha256 !== candidate.sha256) {
      throw new Error(`Tone screen is stale for ${candidate.audio_path}; rerun screening before exporting`);
    }
    return {
      ...candidate,
      tone_screen: { status: finding.status, reason: finding.reason },
    };
  });
  const priority = candidate => candidate.blocked_reason ? 0 : candidate.tone_screen?.status === 'review' ? 1 : 2;
  return output.sort((left, right) => priority(left) - priority(right));
}

function main() {
  const { values } = parseArgs({
    options: {
      export: { type: 'string' }, 'tone-screen': { type: 'string' },
      coverage: { type: 'string' }, decisions: { type: 'string' },
    },
  });
  if (values['tone-screen'] && !values.export) {
    throw new Error('--tone-screen requires --export; it never creates listening approvals');
  }
  if (values.decisions && !values.coverage) throw new Error('--decisions requires --coverage');
  const data = loadReviewData();
  const index = validateLedger(data);
  if (values.coverage) {
    const decisions = values.decisions ? JSON.parse(fs.readFileSync(values.decisions, 'utf8')) : null;
    const report = coverageReport(data, index, decisions);
    const output = path.resolve(values.coverage);
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
    console.log(`Coverage written to ${output}: ${JSON.stringify(report.vocabulary)}; ${report.initial_recording_examples} initial-recording examples.`);
  }
  if (values.export) {
    let candidates = [...candidatesFor(data).values()].map(candidate => {
      const sha256 = audioHash(candidate.audio_path);
      return {
        ...candidate,
        sha256,
        status: 'pending',
        reviews: [],
        review_target: { label_identity: AudioReview.identity(candidate), audio_sha256: sha256 },
      };
    });
    let screeningScope = null;
    if (values['tone-screen']) {
      const report = JSON.parse(fs.readFileSync(values['tone-screen'], 'utf8'));
      candidates = attachToneScreen(candidates, report);
      screeningScope = report.scope;
    }
    const output = path.resolve(values.export);
    if (output === path.join(ROOT, 'data/audio_reviews.json')) throw new Error('Cannot overwrite the approval ledger with a review queue');
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify({
      version: 1, screening_scope: screeningScope, candidates,
    }, null, 2) + '\n', { flag: 'wx' });
    console.log(`Exported ${candidates.length} recording/label candidates to ${output}. This is analysis input, not a mandatory human-listening queue.`);
  }
  const inventory = practiceInventory(data, index);
  console.log(`Audio assessment: ${index.size} qualifying checks; ${inventory.eligibleWords.length}/${data.words.length} practice entries eligible; ${inventory.audio.size} reachable audio files.`);
  if (!inventory.eligibleWords.length) console.log('No sufficiently screened practice items are available. Automated screening is not an accuracy certificate.');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(); } catch (error) {
    console.error(`Listening review failed: ${error.message}`);
    process.exitCode = 1;
  }
}
