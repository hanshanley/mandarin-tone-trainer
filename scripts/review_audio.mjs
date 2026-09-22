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
  return {
    words: readJSON('data/hsk_words.json'),
    recordings: [
      ...readJSON('data/recordings.json'),
      ...readJSON('data/mandarin_native_recordings.json').recordings,
    ],
    publicRecordings: readJSON('data/pinyin_public_recordings.json'),
    quality: readJSON('data/correction_audio_quality.json'),
    ledger: readJSON('data/audio_reviews.json'),
    snapshots: readJSON('config/source_snapshots.json'),
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
  }
  for (const recording of data.recordings) {
    if (recording.source !== 'mandarin_native' || mapped.has(recording.audio_path)) continue;
    const descriptor = {
      kind: 'unmapped_native', audio_path: recording.audio_path, key: recording.source_audio_key,
    };
    candidates.set(AudioReview.identity(descriptor), {
      ...descriptor,
      source_url: recording.source_url,
      license: recording.license,
      blocked_reason: 'No vocabulary reading mapped; requires listening identification and verified reuse permission',
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
          blocked_reason: replacement.quiz_eligible === false ? replacement.notes || 'Excluded replacement recording' : null,
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

export function validateLedger(data, root = ROOT) {
  const index = AudioReview.createIndex(data.ledger);
  const candidates = candidatesFor(data);
  for (const approval of index.values()) {
    const candidate = candidates.get(AudioReview.identity(approval));
    if (!candidate) throw new Error(`Approval labels do not match the current corpus: ${approval.audio_path}`);
    if (candidate.blocked_reason) throw new Error(`Quarantined audio cannot be approved: ${approval.audio_path}`);
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
  for (const word of data.words) {
    const natives = (recordingsByWord.get(word.id) || []).filter(recording =>
      AudioReview.nativeApproval(index, word, recording));
    if (!natives.length) continue;
    const comparisons = (word.pinyin_syllables || []).flatMap(base =>
      ['1', '2', '3', '4'].flatMap(tone => ['pinyin_public', 'audio_cmn'].map(mode =>
        AudioReview.correctionSelection(CorrectionAudio, CorrectionAudio.correctionKey(base, tone),
          data.quality, data.publicRecordings, index, mode))));
    if (!comparisons.length || comparisons.some(item => !item)) continue;
    eligibleWords.push(word.id);
    for (const recording of [...natives, ...comparisons]) audio.add(recording.audio_path);
  }
  return { eligibleWords, audio };
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
    options: { export: { type: 'string' }, 'tone-screen': { type: 'string' } },
  });
  if (values['tone-screen'] && !values.export) {
    throw new Error('--tone-screen requires --export; it never creates listening approvals');
  }
  const data = loadReviewData();
  const index = validateLedger(data);
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
    console.log(`Exported ${candidates.length} pending recording/label candidates to ${output}. No approvals created.`);
  }
  const inventory = practiceInventory(data, index);
  console.log(`Listening review: ${index.size} approvals; ${inventory.eligibleWords.length}/${data.words.length} practice entries eligible; ${inventory.audio.size} reachable audio files.`);
  if (!inventory.eligibleWords.length) console.log('Practice is paused pending independent listening review. This is not a pronunciation accuracy certificate.');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(); } catch (error) {
    console.error(`Listening review failed: ${error.message}`);
    process.exitCode = 1;
  }
}
