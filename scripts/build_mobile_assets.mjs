import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadReviewData, validateLedger, practiceInventory } from './review_audio.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUT = path.join(ROOT, 'www');
const APP_FILES = ['index.html', 'style.css', 'audio_review.js', 'correction_audio.js', 'app.js'];
const DATA_FILES = [
  'hsk_words.json',
  'definitions.json',
  'recordings.json',
  'pinyin_public_recordings.json',
  'correction_audio_quality.json',
  'audio_reviews.json',
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

for (const file of APP_FILES) {
  requireFile(path.join('app', file), 'app file');
}
for (const file of DATA_FILES) {
  requireFile(path.join('data', file), 'runtime data');
}
requireFile(path.join('audio', 'audio_cmn', 'syllabs', 'cmn-ma1.mp3'), 'audio corpus');

const words = readJSON('data/hsk_words.json');
const reviewData = loadReviewData();
const reviewIndex = validateLedger(reviewData);
const inventory = practiceInventory(reviewData, reviewIndex);
const referencedAudio = inventory.audio;

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
    `  ${inventory.eligibleWords.length.toLocaleString()} listening-reviewed practice entries`,
    `  ${referencedAudio.size.toLocaleString()} referenced audio files (${(referencedBytes / 1024 / 1024).toFixed(1)} MiB)`,
    `  ${(totalBytes / 1024 / 1024).toFixed(1)} MiB total`,
  ].join('\n'),
);
