import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {loadReviewData,validateLedger,practiceInventory} from './review_audio.mjs';
import AudioReview from '../app/audio_review.js';
import CorrectionAudio from '../app/correction_audio.js';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const MODES=['pinyin_public','audio_cmn','mandarin_native'];
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));

export function comparisonCoverage(data,index,bases){
  const families=[];
  for(const base of [...bases].sort()){
    const slots=[];
    for(const tone of ['1','2','3','4']){
      const key=CorrectionAudio.correctionKey(base,tone);
      const chosen=Object.fromEntries(MODES.map(mode=>{
        const clip=AudioReview.correctionSelection(CorrectionAudio,key,data.quality,data.publicRecordings,index,mode);
        return [mode,clip?{audio_path:clip.audio_path,source:clip.source,sha256:clip.approval.sha256}:null];
      }));
      slots.push({key,tone,available:MODES.every(mode=>Boolean(chosen[mode])),chosen});
    }
    families.push({base,complete:slots.every(slot=>slot.available),slots});
  }
  return {
    bases:families.length,required_slots:families.length*4,
    playable_slots:families.reduce((n,family)=>n+family.slots.filter(slot=>slot.available).length,0),
    complete_families:families.filter(family=>family.complete).length,families,
  };
}

function main(){
  const {values}=parseArgs({options:{
    inventory:{type:'string'},outcomes:{type:'string',default:'.audit/mixed-audio-cross-fit.json'},
    baseline:{type:'string'},output:{type:'string',default:'data/mixed_audio_coverage.json'},
  }});
  if(!values.inventory||!values.baseline)throw new Error('--inventory and --baseline are required');
  const data=loadReviewData(),index=validateLedger(data),practice=practiceInventory(data,index);
  const baseline=read(values.baseline),inventory=read(values.inventory),outcomes=read(values.outcomes);
  if(outcomes.inventory_sha256!==inventory.inventory_sha256)throw new Error('Mixed-source outcomes have a stale inventory');
  const words=new Map(data.words.map(word=>[word.id,word]));
  const currentIds=new Set(practice.eligibleWords);
  const missingBaseline=baseline.entries.filter(id=>!currentIds.has(id));
  const currentPairs=new Set(practice.recordingLabelPairs.map(pair=>JSON.stringify(pair)));
  const removedExamples=baseline.initial_examples.filter(pair=>!currentPairs.has(JSON.stringify(pair)));
  if(missingBaseline.length||removedExamples.length)throw new Error('Mixed-source integration removed baseline practice');
  const beforeBases=new Set(baseline.bases);
  const currentBases=new Set(practice.eligibleWords.flatMap(id=>words.get(id).pinyin_syllables));
  const covered=comparisonCoverage(data,index,currentBases);
  const baselineCoverage=comparisonCoverage(data,index,beforeBases);
  const byKey=new Map();
  for(const row of inventory.records){
    if(row.syllables.length!==1||!['1','2','3','4'].includes(row.weak_training_label))continue;
    const key=row.syllables[0]+row.weak_training_label;
    if(!byKey.has(key))byKey.set(key,new Map());
    const outcome=outcomes.outcomes[row.id];
    const assessment=index.get(AudioReview.identity({kind:'comparison',audio_path:row.audio_path,key}));
    byKey.get(key).set(row.audio_path,{
      audio_path:row.audio_path,sha256:row.sha256,source:row.source,
      source_url:row.source_url,source_label:row.weak_training_label,
      known_quarantine:row.quarantined,assessed_for_comparison:Boolean(assessment),
      diagnostic_status:outcome?.status||'not_analyzed',
      unresolved_reason:row.quarantined?'known_source_quarantine':
        outcome?.status==='candidate_supported'&&!assessment?'no_distinct_source_corroboration':
          outcome?.reason||(!assessment?'no_qualifying_assessment':null),
    });
  }
  for(const family of covered.families)for(const slot of family.slots){
    slot.candidates=[...(byKey.get(slot.key)?.values()||[])];
    if(!slot.available){
      slot.next_action=slot.candidates.some(candidate=>!candidate.known_quarantine)
        ?'Confirm the unresolved phonetic/tone evidence independently, or obtain a clearer direct replacement.'
        :'Obtain a trustworthy direct recording; existing candidates are absent or explicitly quarantined.';
    }
  }
  const records=new Map(data.recordings.map(recording=>[recording.audio_path,recording]));
  const decisionsPath=path.join(ROOT,'.audit/acoustic-decisions.json');
  const originalDecisions=fs.existsSync(decisionsPath)?read(decisionsPath):null;
  if(originalDecisions&&originalDecisions.pipeline_sha256!==data.acousticLedger.pipeline_sha256){
    throw new Error('Original word-decision report does not match the acoustic ledger');
  }
  const originalReasons=new Map();
  for(const decision of originalDecisions?.findings||[]){
    if(decision.kind!=='native')continue;
    if(!originalReasons.has(decision.audio_path))originalReasons.set(decision.audio_path,new Set());
    originalReasons.get(decision.audio_path).add(decision.reason);
  }
  const imported=read(path.join(ROOT,'data/mandarin_native_recordings.json')).recordings.filter(recording=>recording.recording_type==='word_candidate');
  const initialPaths=new Set(practice.recordingLabelPairs.map(pair=>pair.audio_path));
  const comparisonPaths=new Set(covered.families.flatMap(family=>family.slots.flatMap(slot=>
    Object.values(slot.chosen).filter(Boolean).map(clip=>clip.audio_path))));
  const imports=imported.map(recording=>{
    const entries=[...index.values()].filter(entry=>entry.audio_path===recording.audio_path);
    const validWords=practice.recordingLabelPairs.filter(pair=>pair.audio_path===recording.audio_path).map(pair=>pair.word_id);
    return {
      audio_path:recording.audio_path,sha256:recording.sha256,source_url:recording.source_url,
      source_audio_key:recording.source_audio_key,candidate_word_ids:recording.candidate_hsk_ids,
      native_word_ids_used:validWords,used_for_initial:initialPaths.has(recording.audio_path),
      used_for_comparison:comparisonPaths.has(recording.audio_path),
      available_comparison_keys:entries.filter(entry=>entry.kind==='comparison').map(entry=>entry.key),
      original_word_evidence_reasons:[...(originalReasons.get(recording.audio_path)||[])].sort(),
      unresolved_reason:entries.length?null:
        recording.candidate_hsk_ids.length?'original_word_or_tone_evidence_unresolved':'no_exact_vocabulary_reading_mapping',
    };
  });
  const previous=fs.existsSync(values.output)?read(values.output):{};
  const report={
    ...previous,version:1,inventory_sha256:inventory.inventory_sha256,
    acoustic_ledger_sha256:createHash('sha256').update(fs.readFileSync(path.join(ROOT,'data/acoustic_reviews.json'))).digest('hex'),
    baseline:{commit:baseline.commit,entries:baseline.entries.length,initial_examples:baseline.initial_examples.length,
      bases:baseline.bases.length,entries_removed:missingBaseline,initial_examples_removed:removedExamples,
      current_comparison_coverage:{playable_slots:baselineCoverage.playable_slots,
        complete_families:baselineCoverage.complete_families,required_slots:baselineCoverage.required_slots}},
    current:{entries:practice.eligibleWords.length,initial_examples:practice.recordingLabelPairs.length,
      original_examples:practice.recordingLabelPairs.filter(pair=>records.get(pair.audio_path).source==='audio_cmn').length,
      imported_examples:practice.recordingLabelPairs.filter(pair=>records.get(pair.audio_path).source==='mandarin_native').length,
      initial_imported_files:imports.filter(row=>row.used_for_initial).length,
      comparison_imported_files:imports.filter(row=>row.used_for_comparison).length,
      reachable_audio:practice.audio.size,tone_coverage:practice.toneCoverage},
    comparison_coverage:covered,unresolved_keys:covered.families.flatMap(family=>family.slots.filter(slot=>!slot.available).map(slot=>slot.key)),
    imported_standalone_files:imports,independent_accuracy_verified:false,
  };
  fs.mkdirSync(path.dirname(path.resolve(values.output)),{recursive:true});
  fs.writeFileSync(values.output+'.part',JSON.stringify(report,null,2)+'\n');
  fs.renameSync(values.output+'.part',values.output);
  console.log(JSON.stringify({baseline:report.baseline,current:report.current,
    current_comparison_slots:covered.playable_slots,required:covered.required_slots,
    complete_families:covered.complete_families,unresolved:report.unresolved_keys.length},null,2));
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{main();}catch(error){console.error(`Mixed-source coverage failed: ${error.message}`);process.exitCode=1;}
}
