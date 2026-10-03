import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {parseArgs} from 'node:util';
import { loadReviewData, validateLedger, practiceInventory, quizInventory, requireToneCoverage, audioHash } from './review_audio.mjs';
import AudioReview from '../app/audio_review.js';
import GlossikaLessons from '../app/glossika_lessons.js';
import GlossikaExamples from '../app/glossika_examples.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUT = path.join(ROOT, 'www');
const {values}=parseArgs({options:{'local-use':{type:'boolean',default:false}}});
const localUse=values['local-use'];
const APP_FILES = [
  'index.html','style.css','logo.svg','audio_review.js','correction_audio.js',
  'glossika_examples.js','glossika_lessons.js','app.js',
];
const DATA_FILES = [
  'hsk_words.json',
  'definitions.json',
  'recordings.json',
  'pinyin_public_recordings.json',
  'correction_audio_quality.json',
  'audio_reviews.json',
  'acoustic_reviews.json',
  'mandarin_native_recordings.json',
  'mandarin_native_words.json',
  'sinosplice_recordings.json',
  'glossika_recordings.json',
];
const GLOSSIKA_PRACTICE_FILE='glossika_practice.json';

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

const reviewData = loadReviewData({includePublisher:localUse});
const words = reviewData.words;
const reviewIndex = validateLedger(reviewData, ROOT, { allowLocalOnly: localUse });
const inventory = practiceInventory(reviewData, reviewIndex);
const quiz = quizInventory(reviewData, reviewIndex);
requireToneCoverage(quiz);
const referencedAudio = inventory.audio;
const companion=localUse?GlossikaLessons.validateCatalog(readJSON('data/glossika_recordings.json')):GlossikaLessons.excludedCatalog();
const companionAssets=GlossikaLessons.assetsFor(companion);
const practiceCatalog=localUse
  ?reviewData.publisherCatalog
  :GlossikaExamples.excludedCatalog('Glossika individual practice is excluded from redistributable builds.');
const practiceAssets=GlossikaExamples.mediaAssets(practiceCatalog);
for(const asset of companionAssets){
  if(requireFile(asset.audio_path,'companion asset')!==asset.byte_length||audioHash(asset.audio_path)!==asset.sha256){
    throw new Error(`Companion asset changed: ${asset.audio_path}`);
  }
}
for(const asset of practiceAssets){
  const companionAsset=companionAssets.find(candidate=>candidate.audio_path===asset.audio_path);
  if(!companionAsset||companionAsset.sha256!==asset.sha256
    ||audioHash(asset.audio_path)!==asset.sha256){
    throw new Error(`Glossika practice parent changed: ${asset.audio_path}`);
  }
}
const referencedAssets=new Set([
  ...referencedAudio,
  ...companionAssets.map(asset=>asset.audio_path),
  ...practiceAssets.map(asset=>asset.audio_path),
]);

let referencedBytes = 0;
for (const relativePath of referencedAssets) {
  referencedBytes += requireFile(relativePath, 'referenced audio');
}

fs.rmSync(OUTPUT, { recursive: true, force: true });
fs.mkdirSync(path.join(OUTPUT, 'data'), { recursive: true });
for (const file of APP_FILES) {
  fs.copyFileSync(path.join(ROOT, 'app', file), path.join(OUTPUT, file));
}
for (const file of DATA_FILES) {
  if (file === 'acoustic_reviews.json') {
    const distributable = {
      ...reviewData.acousticLedger,
      approvals: reviewData.acousticLedger.approvals.filter(entry =>
        (localUse||entry.distribution_scope !== 'local_only') && !AudioReview.isSentenceDerived(entry)),
    };
    fs.writeFileSync(path.join(OUTPUT, 'data', file), JSON.stringify(distributable, null, 2) + '\n');
  } else if(file==='glossika_recordings.json'){
    fs.writeFileSync(path.join(OUTPUT,'data',file),JSON.stringify(companion,null,2)+'\n');
  } else {
    fs.copyFileSync(path.join(ROOT, 'data', file), path.join(OUTPUT, 'data', file));
  }
}
fs.writeFileSync(
  path.join(OUTPUT,'data',GLOSSIKA_PRACTICE_FILE),
  JSON.stringify(practiceCatalog,null,2)+'\n',
);
fs.writeFileSync(path.join(OUTPUT,'data/build_scope.json'),JSON.stringify({
  version:1,scope:localUse?'local_use_only':'redistributable',
  includes_unverified_reuse_rights:localUse,
  includes_personal_companion_lessons:companion.available,
  includes_glossika_individual_examples:practiceCatalog.available,
  glossika_individual_example_count:practiceCatalog.examples.length,
  glossika_individual_unavailable_reason:practiceCatalog.available?null:practiceCatalog.unavailable_reason,
},null,2)+'\n');
for (const relativePath of referencedAssets) {
  if(AudioReview.isSentenceDerived({audio_path:relativePath}))throw new Error('Sentence audio cannot be packaged for practice');
  const targetPath = path.join(OUTPUT, relativePath);
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.copyFileSync(path.join(ROOT, relativePath), targetPath);
}

const dataBytes = DATA_FILES.reduce(
  (total, file) => total + fs.statSync(path.join(OUTPUT, 'data', file)).size,
  fs.statSync(path.join(OUTPUT,'data/build_scope.json')).size
    +fs.statSync(path.join(OUTPUT,'data',GLOSSIKA_PRACTICE_FILE)).size,
);
const totalBytes = APP_FILES.reduce(
  (total, file) => total + fs.statSync(path.join(ROOT, 'app', file)).size,
  dataBytes + referencedBytes,
);

console.log(
  [
    'Built offline mobile assets:',
    `  ${localUse?'Local-use build: not for redistribution':'Redistributable-source build'}`,
    `  ${words.length.toLocaleString()} vocabulary entries`,
    `  ${inventory.eligibleWords.length.toLocaleString()} retained library entries`,
    `  ${quiz.eligibleWords.length.toLocaleString()} quiz entries with all four comparisons`,
    `  ${referencedAudio.size.toLocaleString()} quiz/library audio files`,
    `  ${companion.lessons.length} complete personal-use book lessons with accompanying pages`,
    `  ${practiceCatalog.examples.length.toLocaleString()} individual Glossika examples`,
    `  ${(referencedBytes / 1024 / 1024).toFixed(1)} MiB referenced media`,
    `  ${(totalBytes / 1024 / 1024).toFixed(1)} MiB total`,
  ].join('\n'),
);
