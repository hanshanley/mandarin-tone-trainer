import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {loadReviewData,validateLedger,practiceInventory,quizInventory,recordingReplacement} from './review_audio.mjs';
import AudioReview from '../app/audio_review.js';
import CorrectionAudio from '../app/correction_audio.js';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const MODES=AudioReview.COMPARISON_SOURCES;
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

export function importedSingleAudit(inventory,outcomes,imports){
  const files=new Map(),usage=new Map(imports.map(recording=>[recording.audio_path,recording]));
  for(const row of inventory.records){
    if(row.source!=='mandarin_native'||row.syllables.length!==1)continue;
    if(!usage.has(row.audio_path))throw new Error('Single-syllable inventory lacks imported-file metadata');
    if(!files.has(row.audio_path))files.set(row.audio_path,new Map());
    const outcome=outcomes[row.id];
    files.get(row.audio_path).set(row.syllables[0]+row.weak_training_label,{
      key:row.syllables[0]+row.weak_training_label,source_tone:row.weak_training_label,
      known_quarantine:row.quarantined,diagnostic_status:outcome?.status||'not_analyzed',
      reason:outcome?.reason||null,predicted_tone:outcome?.predicted_tone||null,
      source_tone_probability:outcome?.expected_probability??null,
      margin:outcome?.margin??null,identity_supported:outcome?.identity_supported??null,
      quality:outcome?.quality||null,
    });
  }
  const entries=[...files].map(([audio_path,results])=>{
    const recording=usage.get(audio_path),checks=[...results.values()];
    const used=recording.used_for_initial||recording.used_for_comparison;
    const reasons=[...new Set(checks.map(check=>check.reason||'missing_analysis'))];
    const status=used?'in_use':recording.review_status==='rejected'?'explicitly_withheld':
      checks.some(check=>check.known_quarantine)?'known_quarantine':
      checks.every(check=>check.source_tone==='N')?'neutral_requires_word_context':
      recording.available_comparison_keys.length?'eligible_not_selected':
      checks.some(check=>check.diagnostic_status==='candidate_supported')?'awaiting_distinct_source_corroboration':
      reasons.length===1?reasons[0]:'multiple_unresolved_checks';
    return {audio_path,used_for_initial:recording.used_for_initial,used_for_comparison:recording.used_for_comparison,
      status,checks};
  });
  const status_counts={};
  for(const entry of entries)status_counts[entry.status]=(status_counts[entry.status]||0)+1;
  return {
    total_files:entries.length,
    full_tone_files:entries.filter(entry=>entry.checks.some(check=>check.source_tone!=='N')).length,
    used_for_initial:entries.filter(entry=>entry.used_for_initial).length,
    used_for_comparison:entries.filter(entry=>entry.used_for_comparison).length,
    used_in_either_role:entries.filter(entry=>entry.used_for_initial||entry.used_for_comparison).length,
    status_counts,confirmed_source_error_count:null,
    interpretation:'Automated uncertainty or disagreement is not proof that the source recording is wrong. No independent source-error rate is established.',
    files:entries,
  };
}

function main(){
  const {values}=parseArgs({options:{
    inventory:{type:'string'},outcomes:{type:'string',default:'.audit/mixed-audio-cross-fit.json'},
    baseline:{type:'string'},output:{type:'string',default:'data/mixed_audio_coverage.json'},
  }});
  if(!values.inventory||!values.baseline)throw new Error('--inventory and --baseline are required');
  const data=loadReviewData(),index=validateLedger(data),practice=practiceInventory(data,index);
  const quiz=quizInventory(data,index);
  const baseline=read(values.baseline),inventory=read(values.inventory),outcomes=read(values.outcomes);
  if(outcomes.inventory_sha256!==inventory.inventory_sha256)throw new Error('Mixed-source outcomes have a stale inventory');
  const words=new Map(data.words.map(word=>[word.id,word]));
  const currentIds=new Set(practice.eligibleWords);
  const missingBaseline=baseline.entries.filter(id=>!currentIds.has(id));
  const currentPairs=new Set(practice.recordingLabelPairs.map(pair=>JSON.stringify(pair)));
  const missingExamples=baseline.initial_examples.filter(pair=>!currentPairs.has(JSON.stringify(pair)));
  const replacedExamples=missingExamples.map(pair=>recordingReplacement(data,index,practice,pair)).filter(Boolean);
  const removedExamples=missingExamples.filter(pair=>!recordingReplacement(data,index,practice,pair));
  if(missingBaseline.length||removedExamples.length)throw new Error('Mixed-source integration removed baseline practice');
  const beforeBases=new Set(baseline.bases);
  const currentBases=new Set(practice.eligibleWords.flatMap(id=>words.get(id).pinyin_syllables));
  const covered=comparisonCoverage(data,index,currentBases);
  const baselineCoverage=comparisonCoverage(data,index,beforeBases);
  const byKey=new Map();
  const rejectedPaths=new Set(data.recordings.filter(record=>record.review_status==='rejected').map(record=>record.audio_path));
  for(const row of inventory.records){
    if(row.syllables.length!==1||!['1','2','3','4'].includes(row.weak_training_label))continue;
    const key=row.syllables[0]+row.weak_training_label;
    if(!byKey.has(key))byKey.set(key,new Map());
    const outcome=outcomes.outcomes[row.id];
    const assessment=index.get(AudioReview.identity({kind:'comparison',audio_path:row.audio_path,key}));
    byKey.get(key).set(row.audio_path,{
      audio_path:row.audio_path,sha256:row.sha256,source:row.source,
      source_url:row.source_url,source_label:row.weak_training_label,
      known_quarantine:row.quarantined||rejectedPaths.has(row.audio_path),assessed_for_comparison:Boolean(assessment),
      diagnostic_status:outcome?.status||'not_analyzed',
      unresolved_reason:row.quarantined||rejectedPaths.has(row.audio_path)?'explicitly_withheld_recording':
        outcome?.status==='candidate_supported'&&!assessment?'no_distinct_source_corroboration':
          outcome?.reason||(!assessment?'no_qualifying_assessment':null),
    });
  }
  for(const recording of data.recordings.filter(row=>row.source==='sinosplice'&&row.comparison_key)){
    const key=recording.comparison_key;
    if(!byKey.has(key))byKey.set(key,new Map());
    const assessment=index.get(AudioReview.identity(AudioReview.comparisonDescriptor(key,recording)));
    byKey.get(key).set(recording.audio_path,{
      audio_path:recording.audio_path,sha256:recording.sha256,source:recording.source,
      source_url:recording.source_url,source_label:key.slice(-1),
      known_quarantine:recording.review_status==='rejected',assessed_for_comparison:Boolean(assessment),
      diagnostic_status:'outside_frozen_cross_fit_inventory',
      unresolved_reason:assessment?null:'no_qualifying_assessment',
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
      review_status:recording.review_status,
      practice_replacement:recording.practice_replacement||null,
      original_word_evidence_reasons:[...(originalReasons.get(recording.audio_path)||[])].sort(),
      unresolved_reason:recording.review_status==='rejected'?recording.notes:entries.length?null:
        recording.candidate_hsk_ids.length?'original_word_or_tone_evidence_unresolved':'no_exact_vocabulary_reading_mapping',
    };
  });
  const previous=fs.existsSync(values.output)?read(values.output):{};
  const quizInitialPaths=new Set(quiz.recordingLabelPairs.map(pair=>pair.audio_path));
  const sinosplice=data.recordings.filter(row=>row.source==='sinosplice').map(recording=>({
    audio_path:recording.audio_path,sha256:recording.sha256,source_key:recording.source_audio_key,
    source_pattern:recording.source_tone_pattern,surface_pattern:recording.surface_pattern,
    comparison_key:recording.comparison_key,candidate_word_ids:recording.candidate_hsk_ids,
    assessments:[...index.values()].filter(entry=>entry.audio_path===recording.audio_path).map(entry=>({
      kind:entry.kind,key:entry.key,word_id:entry.word_id,distribution_scope:entry.distribution_scope,
    })),
    used_for_library_initial:initialPaths.has(recording.audio_path),
    used_for_quiz_initial:quizInitialPaths.has(recording.audio_path),used_in_quiz:quiz.audio.has(recording.audio_path),
    isolated_clarity:data.quality.isolated_clarity?.[recording.audio_path]?.status||null,
    unresolved_reasons:[...new Set((originalDecisions?.findings||[])
      .filter(row=>row.audio_path===recording.audio_path).map(row=>row.reason))],
  }));
  const report={
    ...previous,version:1,inventory_sha256:inventory.inventory_sha256,
    acoustic_ledger_sha256:createHash('sha256').update(fs.readFileSync(path.join(ROOT,'data/acoustic_reviews.json'))).digest('hex'),
    baseline:{commit:baseline.commit,entries:baseline.entries.length,initial_examples:baseline.initial_examples.length,
      bases:baseline.bases.length,entries_removed:missingBaseline,initial_examples_removed:removedExamples,
      initial_examples_replaced:replacedExamples,
      current_comparison_coverage:{playable_slots:baselineCoverage.playable_slots,
        complete_families:baselineCoverage.complete_families,required_slots:baselineCoverage.required_slots}},
    current:{entries:practice.eligibleWords.length,initial_examples:practice.recordingLabelPairs.length,
      original_examples:practice.recordingLabelPairs.filter(pair=>records.get(pair.audio_path).source==='audio_cmn').length,
      imported_examples:practice.recordingLabelPairs.filter(pair=>records.get(pair.audio_path).source==='mandarin_native').length,
      sinosplice_examples:practice.recordingLabelPairs.filter(pair=>records.get(pair.audio_path).source==='sinosplice').length,
      initial_imported_files:imports.filter(row=>row.used_for_initial).length,
      comparison_imported_files:imports.filter(row=>row.used_for_comparison).length,
      reachable_audio:practice.audio.size,tone_coverage:practice.toneCoverage},
    quiz:{entries:quiz.eligibleWords.length,initial_examples:quiz.recordingLabelPairs.length,
      bases:new Set(quiz.eligibleWords.flatMap(id=>words.get(id).pinyin_syllables)).size,
      comparisons_complete:true,by_syllables:quiz.eligibleWords.reduce((counts,id)=>{
        const length=words.get(id).pinyin_syllables.length;counts[length]=(counts[length]||0)+1;return counts;
      },{}),tone_coverage:quiz.toneCoverage},
    comparison_coverage:covered,unresolved_keys:covered.families.flatMap(family=>family.slots.filter(slot=>!slot.available).map(slot=>slot.key)),
    imported_single_syllable_audit:importedSingleAudit(inventory,outcomes.outcomes,imports),
    imported_standalone_files:imports,independent_accuracy_verified:false,
    sinosplice_source:{
      files:sinosplice.length,single_syllable_files:sinosplice.filter(row=>row.comparison_key).length,
      two_syllable_files:sinosplice.filter(row=>row.surface_pattern.split('-').length===2).length,
      assessed_files:sinosplice.filter(row=>row.assessments.length).length,
      quiz_files:sinosplice.filter(row=>row.used_in_quiz).length,
      license:'CC-BY-NC-SA-2.5',distribution_scope:'local_only',recordings:sinosplice,
    },
  };
  fs.mkdirSync(path.dirname(path.resolve(values.output)),{recursive:true});
  fs.writeFileSync(values.output+'.part',JSON.stringify(report,null,2)+'\n');
  fs.renameSync(values.output+'.part',values.output);
  console.log(JSON.stringify({baseline:report.baseline,current:report.current,quiz:report.quiz,
    current_comparison_slots:covered.playable_slots,required:covered.required_slots,
    complete_families:covered.complete_families,unresolved:report.unresolved_keys.length},null,2));
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{main();}catch(error){console.error(`Mixed-source coverage failed: ${error.message}`);process.exitCode=1;}
}
