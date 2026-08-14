import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUT = path.join(ROOT, 'www');
const APP_FILES = ['index.html', 'style.css', 'app.js'];
const DATA_FILES = [
  'hsk_words.json',
  'definitions.json',
  'recordings.json',
  'pinyin_public_recordings.json',
  'audio_cmn_syllable_quality.json',
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

function correctionKey(pinyin, tone) {
  return `${pinyin.toLowerCase().replaceAll('ü', 'v')}${tone}`;
}

function collectAudioFiles(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectAudioFiles(entryPath));
    } else if (entry.isFile()) {
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
const correctionQuality = readJSON('data/audio_cmn_syllable_quality.json');
const referencedAudio = new Set();

for (const recording of recordings) {
  if (!recording.audio_path) {
    throw new Error(`Recording has no audio_path: ${JSON.stringify(recording)}`);
  }
  referencedAudio.add(recording.audio_path);
}

for (const recording of Object.values(correctionRecordings)) {
  if (recording.audio_path) {
    referencedAudio.add(recording.audio_path);
  }
}

for (const word of words) {
  const tones = (word.default_surface_pattern || word.lexical_pattern || '').split('-');
  if (!Array.isArray(word.pinyin_syllables) || word.pinyin_syllables.length !== tones.length) {
    continue;
  }
  word.pinyin_syllables.forEach((pinyin, index) => {
    const tone = tones[index];
    if (tone === 'N') {
      return;
    }
    const key = correctionKey(pinyin, tone);
    if (correctionQuality[key]?.status === 'bad') {
      const fallback = correctionRecordings[key];
      if (fallback?.audio_path) {
        referencedAudio.add(fallback.audio_path);
      }
      return;
    }
    const audioCmnKey = key === 'ju4' ? 'jv4' : key;
    referencedAudio.add(`audio/audio_cmn/syllabs/cmn-${audioCmnKey}.mp3`);
  });
}

let referencedBytes = 0;
for (const relativePath of referencedAudio) {
  referencedBytes += requireFile(relativePath, 'referenced audio');
}

const audioRoot = path.join(ROOT, 'audio');
const audioFiles = collectAudioFiles(audioRoot);
let audioBytes = 0;
for (const filePath of audioFiles) {
  const stats = fs.statSync(filePath);
  if (stats.size === 0) {
    throw new Error(`Empty audio file: ${path.relative(ROOT, filePath)}`);
  }
  audioBytes += stats.size;
}

fs.rmSync(OUTPUT, { recursive: true, force: true });
fs.mkdirSync(path.join(OUTPUT, 'data'), { recursive: true });
for (const file of APP_FILES) {
  fs.copyFileSync(path.join(ROOT, 'app', file), path.join(OUTPUT, file));
}
for (const file of DATA_FILES) {
  fs.copyFileSync(path.join(ROOT, 'data', file), path.join(OUTPUT, 'data', file));
}
fs.cpSync(audioRoot, path.join(OUTPUT, 'audio'), { recursive: true });

const dataBytes = DATA_FILES.reduce(
  (total, file) => total + fs.statSync(path.join(ROOT, 'data', file)).size,
  0,
);
const totalBytes = APP_FILES.reduce(
  (total, file) => total + fs.statSync(path.join(ROOT, 'app', file)).size,
  dataBytes + audioBytes,
);

console.log(
  [
    'Built offline mobile assets:',
    `  ${words.length.toLocaleString()} vocabulary entries`,
    `  ${recordings.length.toLocaleString()} word recordings`,
    `  ${referencedAudio.size.toLocaleString()} referenced audio files (${(referencedBytes / 1024 / 1024).toFixed(1)} MiB)`,
    `  ${audioFiles.length.toLocaleString()} bundled audio files`,
    `  ${(totalBytes / 1024 / 1024).toFixed(1)} MiB total`,
  ].join('\n'),
);
