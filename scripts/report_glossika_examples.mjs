import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {loadReviewData,validateLedger,quizInventory,practiceInventory} from './review_audio.mjs';

const data=loadReviewData();
if(!data.publisherCatalog.available)throw new Error('Restore the individual Glossika catalog before reporting coverage');
const index=validateLedger(data),quiz=quizInventory(data,index),library=practiceInventory(data,index);
const baselineData=loadReviewData({includePublisher:false});
const baseline=quizInventory(baselineData,validateLedger(baselineData));
const currentIds=new Set(quiz.eligibleWords);
const currentPairs=new Set(quiz.recordingLabelPairs.map(pair=>JSON.stringify(pair)));
if(baseline.eligibleWords.some(id=>!currentIds.has(id))
  ||baseline.recordingLabelPairs.some(pair=>!currentPairs.has(JSON.stringify(pair)))){
  throw new Error('Publisher integration removed an existing quiz entry or initial recording');
}
const words=new Map(data.words.map(word=>[word.id,word]));
const publisherIds=quiz.eligibleWords.filter(id=>words.get(id).source==='glossika');
const group=ids=>ids.reduce((counts,id)=>{
  const length=words.get(id).pinyin_syllables.length;
  counts[length]=(counts[length]||0)+1;return counts;
},{});
const uniqueReadings=ids=>new Set(ids.map(id=>{
  const word=words.get(id);
  return JSON.stringify([word.word.replace(/\d+$/,''),word.pinyin_syllables,word.lexical_pattern]);
})).size;
const publisherMedia=new Set([...quiz.audio].filter(path=>path.startsWith('audio/glossika/lessons/')));
const hash=file=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const summary=JSON.parse(fs.readFileSync('data/glossika_example_summary.json','utf8'));
const report={
  version:1,method:'publisher-example-coverage-v1',
  book_sha256:data.publisherCatalog.book_sha256,index_sha256:data.publisherCatalog.index_sha256,
  boundaries_sha256:data.publisherCatalog.boundaries_sha256,
  private_catalog_sha256:hash('data/glossika_practice.json'),
  source_counts:summary.counts,advertised_counts:summary.advertised_counts,
  count_discrepancies:summary.count_discrepancies,
  mapping:data.publisherCatalog.counts,
  quiz:{entries:quiz.eligibleWords.length,initial_examples:quiz.recordingLabelPairs.length,
    unique_word_readings:uniqueReadings(quiz.eligibleWords),by_syllables:group(quiz.eligibleWords),
    physical_media_files:quiz.audio.size,all_four_comparisons_required:true},
  publisher_quiz:{entries:publisherIds.length,unique_word_readings:uniqueReadings(publisherIds),
    by_syllables:group(publisherIds),physical_parent_files:publisherMedia.size},
  retained_library:{entries:library.eligibleWords.length,initial_examples:library.recordingLabelPairs.length,
    physical_media_files:library.audio.size},
  previous_quiz:{entries:baseline.eligibleWords.length,initial_examples:baseline.recordingLabelPairs.length,
    entries_removed:0,initial_examples_removed:0},
  existing_entries_unlocked:quiz.eligibleWords.length-publisherIds.length-baseline.eligibleWords.length,
  distribution_scope:'local_only',
  independent_pronunciation_certification:false,
  interpretation:'Counts distinguish printed examples, mapped original-media intervals, graded catalog entries, word/reading labels and physical files. Phonetic drills are not necessarily dictionary words. Publisher assessments establish source labels and complete-utterance mappings, not independent human pronunciation certification.',
};
fs.writeFileSync('data/glossika_example_coverage.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
