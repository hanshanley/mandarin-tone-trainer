import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import CorrectionAudio from '../app/correction_audio.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUT = path.join(ROOT, 'www');
const APP_FILES = ['index.html', 'style.css', 'correction_audio.js', 'app.js'];
const DATA_FILES = [
  'hsk_words.json',
  'definitions.json',
  'recordings.json',
  'pinyin_public_recordings.json',
  'correction_audio_quality.json',
];

function readJSON(relativePath) {
  const filePath = path.join(ROOT, relativePath);
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    throw new Error(`Cannot read ${relativePath}: ${error.message}`);
  }
}

function requireFile(relativePath, description) {
  const filePath = path.resolve(ROOT, relativePath);
  const rootPrefix = `${ROOT}${path.sep}`;
  if (!filePath.startsWith(rootPrefix)) {
    throw new Error(`${description} escapes the repository: ${relativePath}`);
  }
  let stats;
  try {
    stats = fs.statSync(filePath);
  } catch {
    throw new Error(`Missing ${description}: ${relativePath}`);
  }
  if (!stats.isFile() || stats.size === 0) {
    throw new Error(`Empty or invalid ${description}: ${relativePath}`);
  }
  return stats.size;
}

function collectAudioFiles(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectAudioFiles(entryPath));
    } else if (entry.isFile() && path.extname(entry.name).toLowerCase() === '.mp3') {
      files.push(entryPath);
    }
  }
  return files;
}

for (const file of APP_FILES) {
  requireFile(path.join('app', file), 'app file');
}
for (const file of DATA_FILES) {
  requireFile(path.join('data', file), 'runtime data');
}
requireFile(path.join('audio', 'audio_cmn', 'syllabs', 'cmn-ma1.mp3'), 'audio corpus');

const words = readJSON('data/hsk_words.json');
const recordings = readJSON('data/recordings.json');
const correctionRecordings = readJSON('data/pinyin_public_recordings.json');
const correctionQuality = readJSON('data/correction_audio_quality.json');
const referencedAudio = new Set();
const syllableAudioRoot = path.join(ROOT, 'audio', 'audio_cmn', 'syllabs');

for (const recording of recordings) {
  if (!recording.audio_path) {
    throw new Error(`Recording has no audio_path: ${JSON.stringify(recording)}`);
  }
  if (recording.quiz_eligible === false) continue;
  referencedAudio.add(recording.audio_path);
}

for (const filePath of collectAudioFiles(syllableAudioRoot)) {
  referencedAudio.add(path.relative(ROOT, filePath));
}

for (const word of words) {
  if (!Array.isArray(word.pinyin_syllables)) continue;
  for (const pinyin of word.pinyin_syllables) {
    for (const tone of ['1', '2', '3', '4']) {
      const key = CorrectionAudio.correctionKey(pinyin, tone);
      const selected = CorrectionAudio.correctionSelection(
        key,
        correctionQuality,
        correctionRecordings,
      );
      if (selected?.audio_path) referencedAudio.add(selected.audio_path);
    }
  }
}

let referencedBytes = 0;
for (const relativePath of referencedAudio) {
  referencedBytes += requireFile(relativePath, 'referenced audio');
}

fs.rmSync(OUTPUT, { recursive: true, force: true });
fs.mkdirSync(path.join(OUTPUT, 'data'), { recursive: true });
for (const file of APP_FILES) {
  fs.copyFileSync(path.join(ROOT, 'app', file), path.join(OUTPUT, file));
}
for (const file of DATA_FILES) {
  fs.copyFileSync(path.join(ROOT, 'data', file), path.join(OUTPUT, 'data', file));
}
for (const relativePath of referencedAudio) {
  const targetPath = path.join(OUTPUT, relativePath);
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.copyFileSync(path.join(ROOT, relativePath), targetPath);
}

const dataBytes = DATA_FILES.reduce(
  (total, file) => total + fs.statSync(path.join(ROOT, 'data', file)).size,
  0,
);
const totalBytes = APP_FILES.reduce(
  (total, file) => total + fs.statSync(path.join(ROOT, 'app', file)).size,
  dataBytes + referencedBytes,
);

console.log(
  [
    'Built offline mobile assets:',
    `  ${words.length.toLocaleString()} vocabulary entries`,
    `  ${recordings.filter(recording => recording.quiz_eligible !== false).length.toLocaleString()} eligible word recordings`,
    `  ${referencedAudio.size.toLocaleString()} referenced audio files (${(referencedBytes / 1024 / 1024).toFixed(1)} MiB)`,
    `  ${(totalBytes / 1024 / 1024).toFixed(1)} MiB total`,
  ].join('\n'),
);
