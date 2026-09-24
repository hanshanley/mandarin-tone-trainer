import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {gunzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import AudioReview from '../app/audio_review.js';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)
  ?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);

export function agrees(prediction,reference,options){
  const blind=prediction?.blind;
  const label=reference.weak_training_label;
  return prediction?.id===reference.id&&prediction.sha256===reference.sha256
    &&prediction.label_identity===reference.label_identity
    &&prediction.assessment==='reference_supported'
    &&prediction.reference_overlap===false&&prediction.identity_supported===true
    &&prediction.original_pattern_represented_in_model===true
    &&prediction.supplied_pattern===label&&prediction.blind_pattern===label&&blind?.pattern===label
    &&blind.quality_ok===true&&blind.boundary_stable===true
    &&Number.isFinite(blind.minimum_top_probability)&&blind.minimum_top_probability>=options.reference_agreement_threshold
    &&Number.isFinite(blind.minimum_margin)&&blind.minimum_margin>=options.minimum_margin
    &&Number.isFinite(blind.probabilities?.[label])&&blind.probabilities[label]>=options.reference_agreement_threshold
    &&reference.quarantined===false;
}

export function selectAgreements(acoustic,inventory,predictions,options){
  const predicted=new Map(predictions.records.map(row=>[row.id,row]));
  if(predicted.size!==predictions.records.length)throw new Error('Duplicate native predictions');
  const referenceByRecording=new Map();
  for(const row of inventory.records){
    if(!agrees(predicted.get(row.id),row,options))continue;
    const key=JSON.stringify([row.audio_path,row.weak_training_label]);
    if(!referenceByRecording.has(key))referenceByRecording.set(key,[]);
    referenceByRecording.get(key).push(row);
  }
  const selected=new Map();
  for(const entry of acoustic.approvals){
    if(AudioReview.isSentenceDerived(entry))continue;
    AudioReview.validateApproval(entry);
    const pattern=entry.kind==='comparison'?entry.key.slice(-1):entry.surface_pattern;
    const refs=referenceByRecording.get(JSON.stringify([entry.audio_path,pattern]))||[];
    const reference=refs.find(row=>entry.kind==='native'
      ?row.kind==='word'&&row.word_id===entry.word_id&&row.word===entry.word
      :row.syllables.length===1&&row.syllables[0]+row.weak_training_label===entry.key);
    if(!reference||reference.sha256!==entry.sha256)continue;
    const prediction=predicted.get(reference.id);
    selected.set(AudioReview.identity(entry),{
      identity:AudioReview.identity(entry),audio_path:entry.audio_path,sha256:entry.sha256,
      kind:entry.kind,pattern,distribution_scope:entry.distribution_scope,
      native_reference_id:reference.id,native_label_identity:reference.label_identity,
      agreement:{
        supplied_pattern:pattern,blind_pattern:prediction.blind.pattern,
        probability:prediction.blind.minimum_top_probability,margin:prediction.blind.minimum_margin,
        quality_ok:true,boundary_stable:true,identity_supported:true,training_overlap:false,
      },
    });
  }
  const comparisons=new Map();
  for(const entry of acoustic.approvals){
    if(entry.kind!=='comparison'||!selected.has(AudioReview.identity(entry)))continue;
    if(!comparisons.has(entry.key))comparisons.set(entry.key,[]);
    comparisons.get(entry.key).push(entry);
  }
  for(const entry of acoustic.approvals){
    if(entry.kind!=='native'||!selected.has(AudioReview.identity(entry)))continue;
    const supported=entry.pinyin_syllables.every((base,index)=>{
      const tone=entry.surface_pattern.split('-')[index];
      if(tone==='N')return true;
      return (comparisons.get(base.replace(/ü/g,'v')+tone)||[]).some(reference=>
        reference.sha256!==entry.sha256
        &&(entry.distribution_scope==='local_only'||reference.distribution_scope!=='local_only'));
    });
    if(!supported)selected.delete(AudioReview.identity(entry));
  }
  return [...selected.values()];
}

function main(){
  const {values}=parseArgs({options:{
    inventory:{type:'string'},predictions:{type:'string'},output:{type:'string',default:'.audit/agreement-selection-preview.json'},
  }});
  if(!values.inventory||!values.predictions)throw new Error('--inventory and --predictions are required');
  const inventory=JSON.parse(fs.readFileSync(values.inventory,'utf8'));
  const inventoryBody={...inventory};delete inventoryBody.inventory_sha256;
  if(sha256(canonical(inventoryBody))!==inventory.inventory_sha256)throw new Error('Native inventory fingerprint mismatch');
  const predictionsBytes=fs.readFileSync(values.predictions);
  const predictions=JSON.parse(predictionsBytes);
  const modelBytes=fs.readFileSync(path.join(ROOT,'data/native_tone_validator.json.gz'));
  const model=JSON.parse(gunzipSync(modelBytes));
  if(predictions.inventory_sha256!==inventory.inventory_sha256||model.inventory_sha256!==inventory.inventory_sha256
    ||predictions.model_sha256!==sha256(modelBytes))throw new Error('Predictions do not belong to the pinned native model/inventory');
  const options=JSON.parse(fs.readFileSync(path.join(ROOT,'config/native_tone_validation.json'),'utf8'));
  const acousticBytes=fs.readFileSync(path.join(ROOT,'data/acoustic_reviews.json'));
  const acoustic=JSON.parse(acousticBytes);
  AudioReview.createIndex({version:1,approvals:[]},acoustic);
  if(options.reference_agreement_threshold!==.9||options.minimum_margin!==.15){
    throw new Error('Agreement thresholds changed; update and review the runtime policy before rebuilding');
  }
  const selected=selectAgreements(acoustic,inventory,predictions,options);
  const native=selected.filter(entry=>entry.kind==='native');
  if(!native.length||!native.some(entry=>entry.audio_path.startsWith('audio/mandarin_native/'))
    ||!native.some(entry=>entry.audio_path.startsWith('audio/audio_cmn/'))){
    throw new Error('Agreement practice requires both original and imported native examples');
  }
  for(const tone of ['1','2','3','4','N']){
    if(!native.some(entry=>entry.pattern.split('-').includes(tone)))throw new Error(`Agreement corpus lacks tone ${tone}`);
  }
  for(const entry of selected){
    const file=path.resolve(ROOT,entry.audio_path);
    if(!file.startsWith(ROOT+path.sep)||sha256(fs.readFileSync(file))!==entry.sha256)throw new Error('Selected audio is missing or changed');
  }
  const selection={
    version:1,policy:'original-label-acoustic-model-agreement-v1',
    accuracy_certified:false,selection_basis:'supplied label + acoustic checks + label-blind agreement; not independent gold certification',
    native_model_sha256:sha256(modelBytes),native_inventory_sha256:inventory.inventory_sha256,
    native_predictions_sha256:sha256(predictionsBytes),acoustic_pipeline_sha256:acoustic.pipeline_sha256,
    minimum_probability:options.reference_agreement_threshold,minimum_margin:options.minimum_margin,
    entries:selected,
  };
  const output=path.resolve(values.output);
  if(!output.startsWith(ROOT+path.sep))throw new Error('Practice selection output must stay inside the repository');
  fs.mkdirSync(path.dirname(output),{recursive:true});
  fs.writeFileSync(output+'.part',JSON.stringify(selection,null,2)+'\n');
  fs.renameSync(output+'.part',output);
  console.log(`Diagnostic agreement preview: ${native.length} native recording/readings; ${selected.length-native.length} comparisons. This does not change runtime practice.`);
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{main();}catch(error){console.error(`Agreement selection failed: ${error.message}`);process.exitCode=1;}
}
