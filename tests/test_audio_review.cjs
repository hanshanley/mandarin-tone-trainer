const assert=require('node:assert/strict');
const {test}=require('node:test');
const {createHash,webcrypto}=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const Review=require('../app/audio_review.js');
const Policy=require('../app/correction_audio.js');
const ROOT=path.resolve(__dirname,'..');
const bytes=Uint8Array.from([1,2,3,4]);
const hash=createHash('sha256').update(bytes).digest('hex');
const nativeBytes=Uint8Array.from([5,6,7,8]);
const nativeHash=createHash('sha256').update(nativeBytes).digest('hex');
const word={
  id:'test-1',word:'test',pinyin:'ma',pinyin_syllables:['ma'],
  lexical_tones:[1],lexical_pattern:'1',default_surface_pattern:'1',sandhi_tags:[],
};
const native={
  word:'test',source:'audio_cmn',audio_path:'audio/audio_cmn/test/test.mp3',
  source_url:'https://example.com/native',license:'test-only',
};
function approve(descriptor){
  return {
    ...descriptor,status:'approved',sha256:hash,source_url:'https://example.com/audio',
    license:'test-only',
    reviews:['listener-a','listener-b'].map(reviewer=>({
      reviewer,method:'human_listening',verdict:'approved',reviewed_at:'2026-09-22',
      identity_correct:true,tones_correct:true,clear_for_practice:true,
      audio_sha256:hash,label_identity:Review.identity(descriptor),
    })),
  };
}
const comparison=(key,source='pinyin_public')=>approve(Review.comparisonDescriptor(key,{
  audio_path:source==='pinyin_public'?`audio/pinyin_public/${key}.mp3`:`audio/audio_cmn/syllabs/cmn-${key}.mp3`,
}));
const index=approvals=>Review.createIndex({version:1,approvals});

test('missing, malformed, automated, conflicting and single-review approvals fail closed',()=>{
  assert.throws(()=>Review.createIndex({}),/ledger/);
  const good=comparison('ma1');
  for(const edit of [
    a=>a.status='pending',
    a=>a.sha256='',
    a=>a.reviews.pop(),
    a=>a.reviews[1].reviewer=' Listener-A ',
    a=>a.reviews[0].method='pitch_analysis',
    a=>a.reviews[0].tones_correct=false,
    a=>a.reviews[0].reviewed_at='not-a-date',
    a=>a.source_url='',
    a=>a.license='',
    a=>a.audio_path='audio/../outside.mp3',
    a=>a.key='ma3',
    a=>a.sha256='0'.repeat(64),
  ]){
    const changed=structuredClone(good);
    edit(changed);
    assert.throws(()=>index([changed]));
  }
  assert.throws(()=>index([good,good]),/Duplicate/);
});

test('native approvals bind word, pinyin, lexical and spoken tones, and exact clip',()=>{
  const approval=approve(Review.nativeDescriptor(word,native));
  const reviewed=index([approval]);
  assert.equal(Review.nativeApproval(reviewed,word,native),approval);
  for(const [field,value] of Object.entries({
    id:'another',word:'other',pinyin:'wrong',pinyin_syllables:['na'],
    lexical_pattern:'2',default_surface_pattern:'3',
  })){
    assert.equal(Review.nativeApproval(reviewed,{...word,[field]:value},native),null);
  }
  assert.equal(Review.nativeApproval(reviewed,word,{...native,audio_path:'audio/other.mp3'}),null);
  assert.equal(Review.nativeApproval(reviewed,word,{...native,surface_pattern:'2'}),null);
  assert.equal(Review.nativeApproval(reviewed,word,{...native,quiz_eligible:false}),null);
  assert.equal(Review.nativeApproval(reviewed,word,{...native,hsk_id:'other'}),null);
});

test('unreviewed preferred voices and fallbacks cannot bypass the gate',()=>{
  const publicRecordings={ma1:{audio_path:'audio/pinyin_public/ma1.mp3',source:'public'}};
  for(const mode of ['pinyin_public','audio_cmn']){
    assert.equal(Review.correctionSelection(Policy,'ma1',{},publicRecordings,index([]),mode),null);
    const human=index([comparison('ma1','audio_cmn')]);
    assert.equal(Review.correctionSelection(Policy,'ma1',{},publicRecordings,human,mode).source,'audio_cmn');
    const quality={audio_cmn:{ma1:{status:'bad',replacement:'pinyin_public'}}};
    assert.equal(Review.correctionSelection(Policy,'ma1',quality,publicRecordings,human,mode),null);
  }
  assert.equal(Review.comparisonApproval(index([comparison('ma2')]),'ma3',
    {audio_path:'audio/pinyin_public/ma2.mp3'}),null);
});

test('content hashes reject changed audio instead of playing a newly replaced file',async()=>{
  const approval=comparison('ma1');
  await Review.verifyBytes(bytes,approval);
  await assert.rejects(Review.verifyBytes(Uint8Array.from([9,9]),approval),/changed since listening review/);
  await assert.rejects(Review.verifyBytes(bytes,null),/no qualifying assessment/);
});

test('spoken pinyin reflects graded tones, including sandhi, neutral and vowel placement',()=>{
  assert.equal(Policy.spokenPinyin(['ni','hao'],'2-3'),'ní hǎo');
  assert.equal(Policy.spokenPinyin(['ma','ma'],'1-N'),'mā ma');
  assert.equal(Policy.spokenPinyin(['liu','gui','lv','lve','ou'],'2-4-4-4-3'),'liú guì lǜ lüè ǒu');
  assert.equal(Policy.spokenPinyin(['n'],'2'),'ń');
  assert.throws(()=>Policy.spokenPinyin(['ma'],'1-2'),/align/);
  assert.throws(()=>Policy.spokenPinyin(['r'],'3'),/tone mark/);
});

function element(){
  const classes=new Set();
  return {
    children:[],dataset:{},value:'',disabled:false,checked:false,textContent:'',
    classList:{
      add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),
      toggle:(c,on)=>on?classes.add(c):classes.delete(c),
    },
    appendChild(child){this.children.push(child)},
    set innerHTML(value){this.html=value;this.children=[]},
    get innerHTML(){return this.html||''},
    setAttribute(){},
    querySelectorAll(){return this.children.filter(child=>child.dataset.tone)},
    querySelector(selector){
      if(selector==='.selected')return this.children.find(child=>child.classList.contains('selected'));
      const tone=/data-tone="([^"]+)"/.exec(selector)?.[1];
      return this.children.find(child=>child.dataset.tone===tone);
    },
    scrollIntoView(){},
  };
}
function selectedPractice(approvals){
  return {
    version:1,policy:'original-label-acoustic-model-agreement-v1',accuracy_certified:false,
    acoustic_pipeline_sha256:'a'.repeat(64),minimum_probability:.9,minimum_margin:.15,
    native_model_sha256:'b'.repeat(64),native_inventory_sha256:'c'.repeat(64),native_predictions_sha256:'d'.repeat(64),
    entries:approvals.filter(entry=>!Review.isSentenceDerived(entry)).map(entry=>{
      const pattern=entry.kind==='native'?entry.surface_pattern:entry.key.slice(-1);
      return {identity:Review.identity(entry),kind:entry.kind,audio_path:entry.audio_path,sha256:entry.sha256,
        pattern,distribution_scope:entry.distribution_scope,
        agreement:{supplied_pattern:pattern,blind_pattern:pattern,probability:.99,margin:.9,
          quality_ok:true,boundary_stable:true,identity_supported:true,training_overlap:false}};
    }),
  };
}
async function appHarness({approvals=[],acousticApprovals=[],supplementalPipelines={},practiceSelection,tamper=null,ledgerFailure=false,recording=native,importedRecordings=[],importedWords=[],excerpts=[],blockedAutoplay=false}={}){
  const elements=new Map();
  const html=fs.readFileSync(path.join(ROOT,'app/index.html'),'utf8');
  const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]));
  const get=id=>{
    if(!ids.has(id))return null;
    if(!elements.has(id))elements.set(id,element());
    return elements.get(id);
  };
  get('wordSource').value='all';
  get('syllables').value='all';
  get('correctionSource').value='pinyin_public';
  const publicRecordings=Object.fromEntries(['1','2','3','4'].map(tone=>[
    `ma${tone}`,{audio_path:`audio/pinyin_public/ma${tone}.mp3`,source:'public'},
  ]));
  const data={
    '../data/hsk_words.json':[structuredClone(word)],'../data/definitions.json':{},
    '../data/mandarin_native_words.json':{version:1,words:importedWords},
    '../data/recordings.json':recording?[recording]:[],'../data/pinyin_public_recordings.json':publicRecordings,
    '../data/correction_audio_quality.json':{},'../data/audio_reviews.json':{version:1,approvals},
    '../data/mandarin_native_recordings.json':{version:1,recordings:importedRecordings},
    '../data/context_word_recordings.json':{version:1,recordings:excerpts},
    '../data/acoustic_reviews.json':{
      version:1,method:'spectral-consensus-1',pipeline_sha256:'a'.repeat(64),
      neutral_lexicon_sha256:acousticApprovals.find(entry=>entry.evidence?.neutral_lexicon_sha256)?.evidence.neutral_lexicon_sha256,
      certifies_accuracy:false,approvals:acousticApprovals,
      supplemental_pipelines:supplementalPipelines,
    },
  };
  data['../data/practice_selection.json']=practiceSelection===undefined
    ?selectedPractice([...approvals,...acousticApprovals]):practiceSelection;
  const requests=[],played=[],errors=[];
  const context=vm.createContext({
    AudioReview:Review,CorrectionAudio:Policy,crypto:webcrypto,Blob,
    URL:{createObjectURL:()=>`blob:verified-${played.length}`,revokeObjectURL(){}},
    console:{error:(...args)=>errors.push(args)},navigator:{},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},
    document:{getElementById:get,createElement:element,addEventListener(){},querySelectorAll:()=>[]},
    Audio:class{
      constructor(url){played.push(url)}
      play(){
        if(blockedAutoplay){
          blockedAutoplay=false;
          return Promise.reject(Object.assign(new Error('User gesture required'),{name:'NotAllowedError'}));
        }
        return Promise.resolve();
      }
      pause(){}
    },
    fetch:async url=>{
      requests.push(url);
      if(url==='../data/audio_reviews.json'&&ledgerFailure)return {ok:false,status:404};
      if(url in data)return {ok:true,json:async()=>data[url]};
      const assessment=acousticApprovals.find(entry=>url===`../${entry.audio_path}`);
      const payload=assessment?.sha256===nativeHash?nativeBytes:bytes;
      return {ok:true,arrayBuffer:async()=>Uint8Array.from(url===tamper?[9,9]:payload).buffer};
    },
  });
  const source=fs.readFileSync(path.join(ROOT,'app/app.js'),'utf8')
    .replace('load().then(()=>next(true,false))','globalThis.appReady=load().then(()=>next(true,false))');
  vm.runInContext(source,context);
  await context.appReady;
  await new Promise(resolve=>setImmediate(resolve));
  return {context,get,requests,played,errors,run:code=>vm.runInContext(code,context)};
}
function allApprovals(){
  return [approve(Review.nativeDescriptor(word,native)),...['1','2','3','4'].map(t=>comparison(`ma${t}`))];
}

function acousticApproval(descriptor,scope='redistributable'){
  const nativeEntry=descriptor.kind==='native';
  const digest=nativeEntry?nativeHash:hash;
  const syllables=nativeEntry?descriptor.pinyin_syllables.map(base=>base.replace(/ü/g,'v')):[descriptor.key.slice(0,-1)];
  const tones=nativeEntry?descriptor.surface_pattern.split('-'):[descriptor.key.slice(-1)];
  return {
    ...descriptor,assessment:'automated',status:'screened',sha256:digest,
    source_url:'https://example.com/audio',license:scope==='local_only'?null:'test-only',
    distribution_scope:scope,
    evidence:{
      method:'spectral-consensus-1',pipeline_sha256:'a'.repeat(64),
      audio_sha256:digest,label_identity:Review.identity(descriptor),
      identity_method:'unprompted_paraformer',recognized_bases:syllables,register_hz:310,
      tones:tones.map(tone=>({
        status:'screened',expected:tone,votes:{pyin:tone,praat:tone,world:tone},
        start:0,end:.5,voiced_seconds:.5,
      })),
      comparison_support:nativeEntry?syllables.map((base,i)=>({
        key:base+tones[i],audio_path:`audio/pinyin_public/${base}${tones[i]}.mp3`,sha256:hash,
      })):[],
    },
  };
}
function acousticLedger(approvals){
  return {version:1,method:'spectral-consensus-1',pipeline_sha256:'a'.repeat(64),certifies_accuracy:false,approvals,
    neutral_lexicon_sha256:approvals.find(entry=>entry.evidence?.neutral_lexicon_sha256)?.evidence.neutral_lexicon_sha256};
}
function automaticComparisons(){
  return ['1','2','3','4'].map(tone=>acousticApproval(Review.comparisonDescriptor(`ma${tone}`,{
    audio_path:`audio/pinyin_public/ma${tone}.mp3`,
  })));
}

test('automatic evidence enables actual practice without human attestations',async()=>{
  const automatic=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(word,native))];
  const app=await appHarness({acousticApprovals:automatic});
  assert.equal(app.run('questionVerified'),true);
  assert.equal(app.get('answers').children.length,1);
  assert.equal(app.played.length,1);
  assert.equal(app.get('reviewStatus'),null);
  assert.equal(app.get('coverageStatus'),null);
  assert.match(app.get('prompt').innerHTML,/Listen first/);
  assert.throws(()=>index(automatic),/acoustic ledger/);
});

test('word-source controls select original or imported recordings without changing the comparison policy',async()=>{
  const imported={
    ...native,source:'mandarin_native',recording_type:'word_candidate',
    audio_path:'audio/mandarin_native/ma1.mp3',word:null,candidate_hsk_ids:[word.id],
    rights_status:'unverified',license:null,review_status:'acoustic_screened',quiz_eligible:true,
  };
  const assessments=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(word,native)),
    acousticApproval(Review.nativeDescriptor(word,imported),'local_only')];
  const app=await appHarness({importedRecordings:[imported],acousticApprovals:assessments});
  assert.equal(app.run('recordingsFor(current).length'),2);
  const reference=app.run("correctionSelection('ma1').audio_path");
  for(const source of ['mandarin_native','audio_cmn']){
    app.get('wordSource').value=source;
    await app.get('wordSource').onchange();
    assert.equal(app.run('questionVerified'),true);
    assert.equal(app.run('currentRec.source'),source);
    assert.equal(app.run('recordingsFor(current).length'),1);
    assert.equal(app.run('quizHistory.length'),0);
    assert.equal(app.run("correctionSelection('ma1').audio_path"),reference);
  }
  app.get('wordSource').value='all';
  await app.get('wordSource').onchange();
  assert.equal(app.run('recordingsFor(current).length'),2);
  app.run('setPracticeControlsDisabled(true)');
  assert.equal(app.get('wordSource').disabled,true);
});

test('missing imported source never silently plays an original recording instead',async()=>{
  const app=await appHarness({approvals:allApprovals()});
  app.get('wordSource').value='mandarin_native';
  await app.run('next(false,false)');
  assert.equal(app.run('current'),null);
  assert.match(app.get('prompt').innerHTML,/Choose Both sources/);
  assert.equal(app.get('play').disabled,true);
  app.get('wordSource').value='all';
  await app.run('next(false,false)');
  assert.equal(app.run('questionVerified'),true);
});

test('pending microphone permission cannot attach a recording to a different question',async()=>{
  const app=await appHarness({approvals:allApprovals()});
  let resolvePermission,stopped=0,constructed=0;
  const permission=new Promise(resolve=>resolvePermission=resolve);
  app.context.navigator.mediaDevices={getUserMedia:()=>permission};
  app.context.MediaRecorder=class{constructor(){constructed++}};
  const starting=app.get('record').onclick();
  assert.equal(app.get('wordSource').disabled,true);
  assert.equal(app.get('next').disabled,true);
  await app.run('next(false,false)');
  resolvePermission({getTracks:()=>[{stop:()=>stopped++}]});
  await starting;
  assert.equal(constructed,0);
  assert.equal(stopped,1);
  assert.equal(app.run('recordingStarting'),false);
  assert.equal(app.get('wordSource').disabled,false);
  assert.match(app.get('audioStatus').textContent,/Recording cancelled/);
});

test('automatic evidence must agree on labels, identity, hashes and references',()=>{
  const valid=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(word,native))];
  for(const edit of [
    a=>a.evidence.tones[0].votes.world='2',
    a=>a.evidence.tones[0].votes={pyin:'1'},
    a=>a.evidence.recognized_bases=['na'],
    a=>a.evidence.audio_sha256='0'.repeat(64),
    a=>a.evidence.label_identity='wrong',
    a=>a.evidence.comparison_support[0].sha256='0'.repeat(64),
    a=>a.evidence.pipeline_sha256='0'.repeat(64),
  ]){
    const entries=structuredClone(valid);
    edit(entries.at(-1));
    assert.throws(()=>Review.createIndex({version:1,approvals:[]},acousticLedger(entries)));
  }
});

test('experimental agreement previews cannot remove screened practice items',async()=>{
  const acoustic=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(word,native))];
  const valid=selectedPractice(acoustic);
  const app=await appHarness({acousticApprovals:acoustic,practiceSelection:valid});
  assert.equal(app.run('questionVerified'),true);
  const noAgreement=selectedPractice(automaticComparisons());
  const retained=await appHarness({acousticApprovals:acoustic,practiceSelection:noAgreement});
  assert.equal(retained.run('questionVerified'),true);
  assert.equal(retained.played.length,1);
  const missing=await appHarness({acousticApprovals:acoustic,practiceSelection:null});
  assert.equal(missing.run('questionVerified'),true);
  assert.equal(missing.requests.includes('../data/practice_selection.json'),false);
  assert.equal(retained.requests.includes('../data/practice_selection.json'),false);
});

test('explicit diagnostic agreement previews still reject invalid evidence',()=>{
  const acoustic=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(word,native))];
  const valid=selectedPractice(acoustic);
  for(const modify of [
    item=>item.agreement.blind_pattern='4',
    item=>item.agreement.supplied_pattern='2',
    item=>item.agreement.probability=.5,
    item=>item.agreement.margin=.05,
    item=>item.agreement.training_overlap=true,
    item=>item.agreement.identity_supported=false,
    item=>item.agreement.boundary_stable=false,
    item=>item.sha256='0'.repeat(64),
  ]){
    const invalid=structuredClone(valid);modify(invalid.entries.at(-1));
    assert.throws(()=>Review.createIndex({version:1,approvals:[]},acousticLedger(acoustic),{practiceSelection:invalid}));
  }
  const stale={...valid,acoustic_pipeline_sha256:'0'.repeat(64)};
  assert.throws(()=>Review.createIndex({version:1,approvals:[]},acousticLedger(acoustic),{practiceSelection:stale}),/agreement/);
});

test('agreement selection can be built only from matching original prediction evidence',async()=>{
  const {agrees}=await import('../scripts/build_agreement_practice.mjs');
  const ref={id:'ref',sha256:hash,label_identity:'label',weak_training_label:'2',quarantined:false};
  const predicted={id:'ref',sha256:hash,label_identity:'label',assessment:'reference_supported',
    reference_overlap:false,identity_supported:true,original_pattern_represented_in_model:true,
    supplied_pattern:'2',blind_pattern:'2',
    blind:{pattern:'2',quality_ok:true,boundary_stable:true,minimum_top_probability:.98,minimum_margin:.95,probabilities:{2:.98}}};
  const options={reference_agreement_threshold:.9,minimum_margin:.15};
  assert.ok(agrees(predicted,ref,options));
  assert.equal(agrees({...predicted,assessment:'possible_spoken_variant'},ref,options),false);
  assert.equal(agrees({...predicted,reference_overlap:true},ref,options),false);
  assert.equal(agrees(predicted,{...ref,quarantined:true},options),false);
  assert.equal(agrees({...predicted,sha256:'bad'},ref,options),false);
});

test('local-only screened imports work locally but are never selected for distribution',async()=>{
  const imported={
    ...native,source:'mandarin_native',recording_type:'word_candidate',
    word:null,candidate_hsk_ids:[word.id],audio_path:'audio/mandarin_native/ma1.mp3',
    quiz_eligible:true,review_status:'acoustic_screened',rights_status:'unverified',license:null,
  };
  const automatic=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(word,imported),'local_only')];
  const app=await appHarness({recording:null,importedRecordings:[imported],acousticApprovals:automatic});
  assert.equal(app.run('questionVerified'),true);
  const packaged=Review.createIndex({version:1,approvals:[]},acousticLedger(automatic),{allowLocalOnly:false});
  assert.equal(Review.nativeApproval(packaged,word,imported),null);
  assert.equal(packaged.size,4);
  const local=Review.createIndex({version:1,approvals:[]},acousticLedger(automatic));
  assert.equal(Review.nativeApproval(local,word,{...imported,review_status:'rejected'}),null);
});

test('a screened isolated-word reference can replace unclear comparison-corpus clips',()=>{
  const reference=approve(Review.comparisonDescriptor('ma3',{audio_path:'audio/audio_cmn/test/test.mp3'}));
  const selected=Review.correctionSelection(Policy,'ma3',{}, {},index([reference]));
  assert.equal(selected.audio_path,reference.audio_path);
  assert.equal(selected.approval,reference);
  const quarantined=Review.createIndex({version:1,approvals:[reference]},null,{
    sourceRecordings:[{...native,quiz_eligible:false}],
  });
  assert.equal(Review.correctionSelection(Policy,'ma3',{}, {},quarantined),null);
});

test('empty practice pool is stated simply without audit backlog or unsafe audio',async()=>{
  const app=await appHarness();
  assert.match(app.get('prompt').innerHTML,/No exercises are available right now/);
  assert.doesNotMatch(app.get('prompt').innerHTML,/screen|review|approval|withheld|quarantin/i);
  assert.equal(app.get('answers').children.length,0);
  assert.equal(app.get('play').disabled,true);
  assert.equal(app.get('record').disabled,true);
  assert.equal(app.played.length,0);
  assert.equal(app.requests.filter(url=>url.endsWith('.mp3')).length,0);
});

test('actual app rejects a missing ledger instead of trusting the old corpus',async()=>{
  const app=await appHarness({ledgerFailure:true});
  assert.match(app.get('prompt').innerHTML,/practice data could not load/);
  assert.equal(app.played.length,0);
  assert.equal(app.get('next').disabled,true);
});

test('missing alternative examples never play but do not disable a checked question',async()=>{
  const app=await appHarness({approvals:allApprovals().filter(a=>a.key!=='ma3')});
  assert.equal(app.run('questionVerified'),true);
  assert.equal(app.get('answers').children.length,1);
  app.run("mineUrl='blob:personal-recording'");
  await app.get('overlay').onclick();
  assert.equal(app.run('overlayAudios.length'),2);
  app.get('answers').children[0].querySelector('[data-tone="3"]').onclick();
  assert.match(app.get('audioStatus').textContent,/No comparison recording is available/);
  assert.equal(app.run('results.length'),1);
  assert.equal(app.run('results[0].correct'),false);
  assert.equal(app.requests.includes('../audio/pinyin_public/ma3.mp3'),false);
  assert.equal(app.run('overlayAudios.length'),0);
  const missingCorrect=await appHarness({approvals:allApprovals().filter(a=>a.key!=='ma1')});
  assert.match(missingCorrect.get('prompt').innerHTML,/No exercises are available/);
  assert.equal(missingCorrect.played.length,0);
});

test('complete approvals verify all five files before rendering and playing',async()=>{
  const app=await appHarness({approvals:allApprovals()});
  assert.equal(app.requests.filter(url=>url.endsWith('.mp3')).length,5);
  assert.equal(app.get('answers').children.length,1);
  assert.equal(app.get('answers').children[0].children.filter(c=>c.dataset.tone).length,5);
  assert.equal(app.run('questionVerified'),true);
  assert.equal(app.played.length,1);
  assert.match(app.played[0],/^blob:verified-/);
  await app.run('playNative()');
  assert.equal(app.requests.filter(url=>url.endsWith('.mp3')).length,5);
});

test('browser autoplay restrictions leave a playable exercise rather than an error state',async()=>{
  const app=await appHarness({approvals:allApprovals(),blockedAutoplay:true});
  assert.equal(app.run('questionVerified'),true);
  assert.equal(app.get('play').disabled,false);
  assert.match(app.get('audioStatus').textContent,/Tap an audio button/);
  assert.equal(app.get('audioStatus').classList.contains('error'),false);
  await app.get('play').onclick();
  assert.match(app.get('audioStatus').textContent,/Playing the native recording/);
});

test('tampered native or comparison audio prevents all playback and grading',async()=>{
  for(const audio of ['../audio/audio_cmn/test/test.mp3','../audio/pinyin_public/ma2.mp3']){
    const app=await appHarness({approvals:allApprovals(),tamper:audio});
    assert.equal(app.run('questionVerified'),false);
    assert.equal(app.get('answers').children.length,0);
    assert.equal(app.played.length,0);
    assert.match(app.get('audioStatus').textContent,/changed since listening review/);
    app.run("grade('1','1')");
    assert.equal(app.run('results.length'),0);
  }
});

test('changing native spoken-tone labels invalidates eligibility',async()=>{
  const app=await appHarness({approvals:allApprovals(),recording:{...native,surface_pattern:'3'}});
  assert.match(app.get('prompt').innerHTML,/No exercises are available/);
  assert.equal(app.played.length,0);
});

test('reviewed questions can be graded and link out without fetching external audio',async()=>{
  const app=await appHarness({approvals:allApprovals()});
  app.run(`
    selectedTones=['1'];
    $('answers').children[0].querySelector('[data-tone="1"]').classList.add('selected');
    grade('1','1');
  `);
  assert.equal(app.run('results.length'),1);
  assert.equal(app.run('results[0].correct'),true);
  assert.match(app.get('reveal').children[0].href,/^https:\/\/mandarin-native\.com\/#/);
  assert.equal(app.requests.some(url=>url.startsWith('https:')),false);
  const before=app.run('current.id');
  app.get('correctionSource').value='audio_cmn';
  await app.get('correctionSource').onchange();
  assert.equal(app.run('current.id'),before);
  assert.equal(app.run('current._graded'),true);
  assert.equal(app.run('selectedTones[0]'),'1');
  assert.equal(app.run('results.length'),1);
});

test('practice card contains exercises and controls, not corpus audit counters',()=>{
  const html=fs.readFileSync(path.join(ROOT,'app/index.html'),'utf8');
  const card=html.split('<section class="card"')[1].split('</section>')[0];
  assert.doesNotMatch(card,/reviewStatus|coverageStatus|eligible vocabulary|acoustic checks|listening approvals/);
  for(const id of ['prompt','answers','play','next','audioStatus'])assert.ok(card.includes(`id="${id}"`));
});

test('empty filters explain how to get back to the available exercises',async()=>{
  const app=await appHarness({approvals:allApprovals()});
  const available=app.run('practiceWords().length');
  app.get('syllables').value='two';
  await app.run('next(false,false)');
  assert.match(app.get('prompt').innerHTML,/No exercises match these filters/);
  assert.match(app.get('prompt').innerHTML,/Try another syllable setting/);
  assert.doesNotMatch(app.get('prompt').innerHTML,/screen|approval|review/i);
  assert.equal(app.run('practiceWords().length'),available);
  app.get('syllables').value='all';
  await app.run('next(false,false)');
  assert.equal(app.run('questionVerified'),true);
  assert.match(app.get('prompt').innerHTML,/Listen first/);
});

test('overlay and alternate-voice fallback use only verified bytes',async()=>{
  const app=await appHarness({approvals:allApprovals()});
  app.run("mineUrl='blob:personal-recording'");
  await app.get('overlay').onclick();
  assert.equal(app.played.length,3);
  assert.match(app.played[1],/^blob:verified-/);
  assert.equal(app.played[2],'blob:personal-recording');
  app.get('correctionSource').value='audio_cmn';
  await app.run('next(false,false)');
  assert.equal(app.run('questionVerified'),true);
  assert.equal(app.run("correctionSelection('ma1').audio_path"),'audio/pinyin_public/ma1.mp3');
  assert.equal(app.requests.some(url=>url.includes('/syllabs/')),false);
});

test('bundle inventory uses the same review policy and never includes unreviewed files',async()=>{
  const {practiceInventory}=await import('../scripts/review_audio.mjs');
  const publicRecordings=Object.fromEntries(['1','2','3','4'].map(t=>[
    `ma${t}`,{audio_path:`audio/pinyin_public/ma${t}.mp3`,source:'public'},
  ]));
  const data={words:[word],recordings:[native],quality:{},publicRecordings};
  assert.equal(practiceInventory(data,index([])).audio.size,0);
  const inventory=practiceInventory(data,index(allApprovals()));
  assert.deepEqual(inventory.eligibleWords,[word.id]);
  assert.equal(inventory.audio.size,5);
  assert.equal(practiceInventory(data,index(allApprovals().filter(a=>a.key!=='ma3'))).audio.size,4);
  assert.equal(practiceInventory(data,index(allApprovals().filter(a=>a.key!=='ma1'))).audio.size,0);
});

test('build-time validation rejects stale labels, quarantines, provenance and changed bytes',async()=>{
  const {validateLedger}=await import('../scripts/review_audio.mjs');
  const os=require('node:os');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'tone-review-test-'));
  const file=path.join(root,native.audio_path);
  try{
    fs.mkdirSync(path.dirname(file),{recursive:true});
    fs.writeFileSync(file,bytes);
    const approval={
      ...approve(Review.nativeDescriptor(word,native)),
      source_url:native.source_url,license:native.license,
    };
    const data={
      words:[word],recordings:[native],publicRecordings:{},quality:{},
      snapshots:{audio_cmn:{repository:'https://example.com',revision:'a'.repeat(40),syllable_quality:'64k'}},
      ledger:{version:1,approvals:[approval]},
    };
    assert.equal(validateLedger(data,root).size,1);
    assert.throws(()=>validateLedger({...data,words:[{...word,pinyin:'changed'}]},root),/labels do not match/);
    assert.throws(()=>validateLedger({...data,recordings:[{...native,quiz_eligible:false}]},root),/Quarantined/);
    assert.throws(()=>validateLedger({...data,recordings:[{...native,license:'different'}]},root),/provenance/);
    fs.writeFileSync(file,'different bytes');
    assert.throws(()=>validateLedger(data,root),/changed since listening review/);
  }finally{
    fs.rmSync(root,{recursive:true,force:true});
  }
});

test('tone-screen evidence prioritizes review without approving or dropping candidates',async()=>{
  const {attachToneScreen}=await import('../scripts/review_audio.mjs');
  const pending=key=>({
    ...Review.comparisonDescriptor(key,{audio_path:`audio/pinyin_public/${key}.mp3`}),
    sha256:hash,status:'pending',reviews:[],
  });

  const candidates=[pending('ma1'),pending('ma2'),pending('ma3')];
  const report={
    certifies_accuracy:false,scope:'selected_keys',
    sources:{pinyin_public:[{
      ...candidates[1],status:'review',reason:'Missing pitch track',
    }]},
  };
  const queue=attachToneScreen(candidates,report);
  assert.equal(queue.length,3);
  assert.equal(queue[0].key,'ma2');
  assert.equal(queue[0].tone_screen.status,'review');
  assert.ok(queue.every(item=>item.status==='pending'&&item.reviews.length===0));
  assert.equal(queue.find(item=>item.key==='ma1').tone_screen.status,'not_screened');
  assert.throws(()=>attachToneScreen(candidates,{
    ...report,sources:{pinyin_public:[{...report.sources.pinyin_public[0],sha256:'0'.repeat(64)}]},
  }),/stale/);
  assert.throws(()=>attachToneScreen(candidates,{...report,certifies_accuracy:true}),/must not certify/);
  assert.throws(()=>attachToneScreen(candidates,{
    ...report,sources:{pinyin_public:[...report.sources.pinyin_public,...report.sources.pinyin_public]},
  }),/Duplicate/);
});

test('quarantines cannot disappear when their explanatory notes are empty',async()=>{
  const {candidatesFor}=await import('../scripts/review_audio.mjs');
  const data={
    words:[word],recordings:[{...native,quiz_eligible:false,notes:''}],
    publicRecordings:{ma1:{audio_path:'audio/pinyin_public/ma1.mp3'}},
    quality:{
      pinyin_public:{ma1:{status:'bad'}},
      audio_cmn:{ma1:{status:'bad',replacement_audio_path:native.audio_path}},
    },
    snapshots:{audio_cmn:{repository:'https://example.com',revision:'a'.repeat(40),syllable_quality:'64k'}},
  };
  const candidates=[...candidatesFor(data).values()];
  assert.ok(candidates.find(item=>item.kind==='comparison'&&item.audio_path===native.audio_path).blocked_reason);
  assert.ok(candidates.find(item=>item.audio_path==='audio/pinyin_public/ma1.mp3').blocked_reason);
});

test('imported recordings need both cleared reuse rights and exact listening approvals',async()=>{
  const imported={
    ...native,source:'mandarin_native',word:null,candidate_hsk_ids:[word.id],
    recording_type:'word_candidate',
    audio_path:'audio/mandarin_native/ma1.mp3',quiz_eligible:false,rights_status:'unverified',license:null,
  };
  const approvals=[
    approve(Review.nativeDescriptor(word,imported)),
    ...['1','2','3','4'].map(t=>comparison(`ma${t}`)),
  ];
  for(const changed of [
    imported,
    {...imported,quiz_eligible:true},
    {...imported,quiz_eligible:true,rights_status:'cleared'},
    {...imported,rights_status:'cleared',license:'test-only'},
  ]){
    const app=await appHarness({approvals,recording:null,importedRecordings:[changed]});
    assert.equal(app.run('questionVerified'),false);
    assert.equal(app.played.length,0);
  }
  const cleared={...imported,quiz_eligible:true,rights_status:'cleared',license:'test-only'};
  const approved=await appHarness({approvals,recording:null,importedRecordings:[cleared]});
  assert.equal(approved.run('questionVerified'),true);
  assert.equal(approved.run('currentRec.source'),'mandarin_native');
  const {practiceInventory,candidatesFor}=await import('../scripts/review_audio.mjs');
  const data={words:[word],recordings:[cleared],publicRecordings:{},quality:{},
    snapshots:{audio_cmn:{repository:'https://example.com',revision:'a'.repeat(40),syllable_quality:'64k'}}};
  for(const tone of ['1','2','3','4'])data.publicRecordings[`ma${tone}`]={audio_path:`audio/pinyin_public/ma${tone}.mp3`};
  assert.equal(practiceInventory(data,index(approvals)).eligibleWords.length,1);
  assert.equal(practiceInventory({...data,recordings:[imported]},index(approvals)).audio.size,0);
  const unmapped={...imported,candidate_hsk_ids:[],source_audio_key:'ma1'};
  const queue=[...candidatesFor({...data,recordings:[unmapped]}).values()];
  assert.equal(queue.filter(item=>item.kind==='unmapped_native').length,1);
  assert.throws(()=>index([approve(queue.find(item=>item.kind==='unmapped_native'))]),/kind/);
});

test('sentence recordings never become isolated prompts even with matching word IDs and approvals',async()=>{
  const contextual={
    ...native,source:'mandarin_native',recording_type:'context_sentence',
    source_audio_key:'sentence.m4a',word:null,candidate_hsk_ids:[word.id],
    audio_path:'audio/mandarin_native/context/example.m4a',
    quiz_eligible:true,rights_status:'cleared',license:'test-only',context_words:[word.word],
  };
  const approvals=[
    approve(Review.nativeDescriptor(word,contextual)),
    ...['1','2','3','4'].map(t=>comparison(`ma${t}`)),
  ];
  const app=await appHarness({approvals,recording:null,importedRecordings:[contextual]});
  assert.equal(app.run('questionVerified'),false);
  assert.equal(app.played.length,0);
  assert.deepEqual(Review.nativeCandidates([word],[contextual]),[]);
  assert.equal(Review.nativeApproval(index(approvals),word,contextual),null);
  const {candidatesFor,practiceInventory}=await import('../scripts/review_audio.mjs');
  const data={words:[word],recordings:[contextual],quality:{},publicRecordings:{},
    snapshots:{audio_cmn:{repository:'https://example.com',revision:'a'.repeat(40),syllable_quality:'64k'}}};
  assert.equal(practiceInventory(data,index(approvals)).audio.size,0);
  const queue=[...candidatesFor(data).values()];
  assert.equal(queue.filter(item=>item.kind==='context_native').length,1);
  const quality={audio_cmn:{ma1:{
    status:'bad',replacement_audio_path:contextual.audio_path,replacement_source:'audio_cmn',
  }}};
  const comparisonApproval=approve(Review.comparisonDescriptor('ma1',contextual));
  assert.equal(Review.correctionSelection(Policy,'ma1',quality,{},index([comparisonApproval])),null);
  const fallbackQueue=[...candidatesFor({...data,quality}).values()];
  assert.ok(fallbackQueue.find(item=>item.kind==='comparison'&&item.audio_path===contextual.audio_path).blocked_reason);
});

test('coverage counts vocabulary entries, initial examples and missing audio separately',async()=>{
  const {coverageReport}=await import('../scripts/review_audio.mjs');
  const extraVoice={...native,audio_path:'audio/audio_cmn/test/second.mp3'};
  const missing={...word,id:'no-clip',word:'no-clip'};
  const blocked={...word,id:'blocked',word:'blocked'};
  const noReference={...word,id:'no-ref',word:'no-ref',lexical_pattern:'2',default_surface_pattern:'2'};
  const noRefRecording={...native,word:'no-ref',audio_path:'audio/audio_cmn/no-ref/no-ref.mp3'};
  const blockedRecording={...native,word:'blocked',audio_path:'audio/audio_cmn/blocked/blocked.mp3',quiz_eligible:false};
  const approvals=[
    ...allApprovals().filter(entry=>entry.key!=='ma2'),
    approve(Review.nativeDescriptor(word,extraVoice)),
    approve(Review.nativeDescriptor(noReference,noRefRecording)),
  ];
  const data={
    words:[word,missing,blocked,noReference],recordings:[native,extraVoice,blockedRecording,noRefRecording],
    quality:{},publicRecordings:{ma1:{audio_path:'audio/pinyin_public/ma1.mp3'}},
  };
  const report=coverageReport(data,index(approvals));
  assert.deepEqual(report.vocabulary,{
    total:4,eligible:1,no_isolated_recording:1,missing_correct_tone_reference:1,native_screening_unresolved:1,
  });
  assert.equal(report.initial_recording_examples,2);
  assert.equal(report.initial_audio_files,2);
  assert.equal(report.entries.length,4);
  assert.equal(Object.values(report.primary_blocker_word_counts).reduce((sum,count)=>sum+count,0),3);
  assert.throws(()=>coverageReport(data,index(approvals),{pipeline_sha256:'stale',findings:[]}),/stale/);
  const fingerprint='a'.repeat(64);
  const traced=coverageReport({...data,acousticLedger:{pipeline_sha256:fingerprint}},index(approvals),{
    pipeline_sha256:fingerprint,
    findings:[{label_identity:Review.identity(Review.nativeDescriptor(blocked,blockedRecording)),
      reason:'known native recording quarantine'}],
  });
  assert.deepEqual(traced.primary_blocker_word_counts,{
    no_isolated_recording:1,quarantined:1,comparison_unresolved:1,
  });
});

test('prepared recognition checks cannot hide conflicting bases or a different payload',()=>{
  const comparison=automaticComparisons()[0];
  const checks=[
    {input:'raw',audio_sha256:hash,evidence_version:'paraformer-unprompted-1',
      transcript:'ma',decoded_bases:['ma']},
    {input:'prepared',audio_sha256:hash,evidence_version:'paraformer-dc70-rms010-trim30-1',
      transcript:'ma',decoded_bases:['ma'],
      preparation:{sample_rate:16000,gain:4,trim_start_seconds:.1,trim_end_seconds:.8}},
  ];
  comparison.evidence.recognition_checks=checks;
  Review.validateApproval(comparison);
  for(const edit of [
    a=>a.evidence.recognition_checks[1].decoded_bases=['na'],
    a=>a.evidence.recognition_checks[1].audio_sha256='0'.repeat(64),
    a=>a.evidence.recognition_checks[1].preparation.sample_rate=8000,
    a=>a.evidence.recognition_checks[1].preparation.gain=-1,
    a=>a.evidence.recognition_checks[0].input='prepared',
  ]){
    const changed=structuredClone(comparison);edit(changed);
    assert.throws(()=>Review.validateApproval(changed));
  }
  const ledger={...acousticLedger(automaticComparisons()),recognition_policy:'raw-prepared-no-phonetic-conflict-1'};
  assert.throws(()=>Review.createIndex({version:1,approvals:[]},ledger),/prepared recognition/);
});

test('new long words use direct whole-word recordings',async()=>{
  const long={
    ...word,id:'MN-long',word:'新词语',pinyin:'ma ma ma',pinyin_syllables:['ma','ma','ma'],
    lexical_tones:[1,1,1],lexical_pattern:'1-1-1',default_surface_pattern:'1-1-1',definition:'a new word',
  };
  const direct={
    ...native,word:long.word,source:'mandarin_native',recording_type:'word_candidate',
    audio_path:'audio/mandarin_native/direct-long.mp3',candidate_hsk_ids:[long.id],
    rights_status:'unverified',quiz_eligible:true,review_status:'acoustic_screened',
  };
  const automatic=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(long,direct),'local_only')];
  const app=await appHarness({recording:null,importedWords:[long],importedRecordings:[direct],acousticApprovals:automatic});
  assert.equal(app.run('questionVerified'),true);
  assert.equal(app.run('current.id'),long.id);
  app.get('syllables').value='longer';
  await app.run('next(false,false)');
  assert.equal(app.run('current.id'),long.id);
  assert.equal(app.get('answers').children.length,3);
  for(const column of app.get('answers').children)column.querySelector('[data-tone="1"]').onclick();
  assert.equal(app.run('results.at(-1).correct'),true);
  assert.equal(app.run('currentRec.recording_type'),'word_candidate');
  assert.equal(app.requests.includes('../data/context_word_recordings.json'),false);
});

test('sentence cuts cannot enter practice even with old acoustic or human assessments',async()=>{
  const origin={
    audio_path:'audio/mandarin_native/context/'+'a'.repeat(64)+'.m4a',
    sha256:'b'.repeat(64),start_sample:1600,end_sample:16000,sample_rate:16000,
  };
  const excerpt={
    ...native,source:'mandarin_native',recording_type:'aligned_word',
    audio_path:'audio/mandarin_native/excerpts/test.wav',candidate_hsk_ids:[word.id],
    source_segment:origin,alignment_method:'exact-unprompted-transcript-fa-zh-1',
    rights_status:'cleared',quiz_eligible:true,review_status:'acoustic_screened',
  };
  const automatic=[...automaticComparisons(),acousticApproval(Review.nativeDescriptor(word,excerpt),'local_only')];
  const app=await appHarness({recording:null,importedRecordings:[excerpt],excerpts:[excerpt],acousticApprovals:automatic});
  assert.equal(app.run('questionVerified'),false);
  assert.equal(app.played.length,0);
  assert.equal(app.requests.some(url=>url.includes('/excerpts/')),false);
  assert.equal(app.requests.includes('../data/context_word_recordings.json'),false);
  const human=approve(Review.nativeDescriptor(word,excerpt));
  const humanIndex=Review.createIndex({version:1,approvals:[human]});
  assert.equal(humanIndex.size,0);
  assert.equal(Review.nativeApproval(humanIndex,word,excerpt),null);
  await assert.rejects(Review.verifyBytes(bytes,human),/Sentence-extracted/);
  const renamed={...excerpt,recording_type:'word_candidate',audio_path:'audio/mandarin_native/renamed.mp3'};
  assert.deepEqual(Review.nativeCandidates([word],[renamed]),[]);
  const legacy={...excerpt};delete legacy.source_segment;
  assert.deepEqual(Review.nativeCandidates([word],[legacy]),[]);
  const disguised={...legacy,recording_type:'word_candidate',audio_path:origin.audio_path};
  assert.deepEqual(Review.nativeCandidates([word],[disguised]),[]);
});

test('normal inventory and candidate exports do not depend on sentence archives',async()=>{
  const {loadReviewData,candidatesFor,validateLedger,practiceInventory}=await import('../scripts/review_audio.mjs');
  const originalRead=fs.readFileSync;
  fs.readFileSync=(file,...args)=>{
    assert.doesNotMatch(String(file),/practice_selection\.json|context_word_recordings\.json|\/mandarin_native\/(?:excerpts|context)\//);
    return originalRead(file,...args);
  };
  try{
    const data=loadReviewData();
    assert.ok(data.recordings.length>0);
    assert.equal(data.recordings.some(recording=>Review.isSentenceDerived(recording)||recording.recording_type==='context_sentence'),false);
    const candidates=[...candidatesFor(data).values()];
    assert.equal(candidates.some(candidate=>candidate.source_segment||candidate.audio_path.includes('/excerpts/')||candidate.audio_path.includes('/context/')),false);
    assert.ok(practiceInventory(data,validateLedger(data)).eligibleWords.length>0);
  }finally{fs.readFileSync=originalRead;}
});

test('normal ledger validation ignores diagnostic selection fields',async()=>{
  const {loadReviewData,validateLedger,practiceInventory}=await import('../scripts/review_audio.mjs');
  const data=loadReviewData();
  const before=practiceInventory(data,validateLedger(data));
  const after=practiceInventory({...data,practiceSelection:{version:1,entries:[]}},
    validateLedger({...data,practiceSelection:{version:1,entries:[]}}));
  assert.deepEqual(after.eligibleWords,before.eligibleWords);
  assert.deepEqual(after.recordingLabelPairs,before.recordingLabelPairs);
  assert.deepEqual([...after.audio].sort(),[...before.audio].sort());
});

test('staged audio updates preserve every usable initial recording, not just total word counts',async()=>{
  const {loadReviewData,validateAudioUpdate}=await import('../scripts/review_audio.mjs');
  const data=loadReviewData();
  const imported={recordings:data.recordings.filter(recording=>recording.source==='mandarin_native')};
  const impact=validateAudioUpdate({ledger:data.acousticLedger,imported},data);
  assert.equal(impact.before_words,impact.after_words);
  assert.equal(impact.before_examples,impact.after_examples);
  assert.equal(impact.before_words_redistributable,impact.after_words_redistributable);
  assert.equal(impact.before_examples_redistributable,impact.after_examples_redistributable);
  const removed=data.acousticLedger.approvals.find(entry=>entry.kind==='native'
    &&entry.distribution_scope==='redistributable'&&entry.evidence.method==='cross-source-native-reference-v1');
  assert.ok(removed);
  const ledger={...data.acousticLedger,approvals:data.acousticLedger.approvals.filter(entry=>entry!==removed)};
  assert.throws(()=>validateAudioUpdate({ledger,imported},data),/removed a usable initial recording/);
  const restricted={...data.acousticLedger,approvals:data.acousticLedger.approvals.map(entry=>
    entry===removed?{...entry,distribution_scope:'local_only'}:entry)};
  assert.throws(()=>validateAudioUpdate({ledger:restricted,imported},data),/redistributable audio update removed/);
});

function mixedComparison(){
  const selfPath='audio/mandarin_native/ma3.mp3',peerPath='audio/pinyin_public/ma3.mp3';
  function prediction(audio_path,sha256,source,decoded_sha256){
    return {audio_path,sha256,source,decoded_sha256,key:'ma3',expected_tone:'3',predicted_tone:'3',
      status:'candidate_supported',fold:0,family:'base:ma',model_sha256:'b'.repeat(64),
      expected_probability:.99,margin:.95,training_overlap:false,identity_supported:true,
      identity_method:'paraformer-raw-prepared',
      quality:{jointly_voiced_frames:30,usable_trackers:3,tracker_difference:.1,clipped_fraction:0},
      recognition_checks:['raw','prepared'].map(input=>({input,audio_sha256:sha256,transcript:'马',decoded_bases:['ma']}))};
  }
  const descriptor=Review.comparisonDescriptor('ma3',{audio_path:selfPath});
  return {
    ...descriptor,assessment:'automated',status:'screened',sha256:hash,
    source_url:'https://example.com/ma3.mp3',license:null,distribution_scope:'local_only',
    evidence:{
      method:'cross-source-native-reference-v1',pipeline_sha256:'a'.repeat(64),model_bundle_sha256:'c'.repeat(64),
      audio_sha256:hash,label_identity:Review.identity(descriptor),original_key:'ma3',source:'mandarin_native',
      cross_fit:prediction(selfPath,hash,'mandarin_native','e'.repeat(64)),
      corroboration:{
        audio_path:peerPath,sha256:nativeHash,decoded_sha256:'f'.repeat(64),key:'ma3',source:'pinyin_public',
        waveform_similarity:.2,cross_fit:prediction(peerPath,nativeHash,'pinyin_public','f'.repeat(64)),
      },
      independent_gold_accuracy_claimed:false,
    },
  };
}
function mixedPipeline(){
  return {'cross-source-native-reference-v1':{pipeline_sha256:'a'.repeat(64),model_bundle_sha256:'c'.repeat(64),
    minimum_probability:.95,minimum_margin:.25,minimum_distinct_sources:2,independent_gold_accuracy_claimed:false}};
}

test('mixed-source references reject self-training, source copying, wrong identity and weak evidence',()=>{
  const assessment=mixedComparison();
  Review.validateApproval(assessment);
  for(const edit of [
    a=>a.evidence.cross_fit.predicted_tone='2',
    a=>a.evidence.cross_fit.training_overlap=true,
    a=>a.evidence.cross_fit.expected_probability=.7,
    a=>a.evidence.cross_fit.identity_supported=false,
    a=>a.evidence.cross_fit.recognition_checks[0].decoded_bases=['na'],
    a=>a.evidence.corroboration.source='mandarin_native',
    a=>a.evidence.corroboration.decoded_sha256=a.evidence.cross_fit.decoded_sha256,
    a=>a.evidence.corroboration.waveform_similarity=.999,
    a=>a.evidence.corroboration.key='ma2',
    a=>a.evidence.independent_gold_accuracy_claimed=true,
  ]){
    const invalid=structuredClone(assessment);edit(invalid);
    assert.throws(()=>Review.validateApproval(invalid));
  }
  const goodLedger={...acousticLedger([assessment]),supplemental_pipelines:mixedPipeline()};
  assert.equal(Review.createIndex({version:1,approvals:[]},goodLedger).size,1);
  assert.equal(Review.createIndex({version:1,approvals:[]},goodLedger,{allowLocalOnly:false}).size,0);
  assert.throws(()=>Review.createIndex({version:1,approvals:[]},acousticLedger([assessment])),/Mixed-source pipeline/);
});

test('new-source comparison voice fills a tone independently of the initial word source',async()=>{
  const comparison=mixedComparison();
  const imported={word:null,source:'mandarin_native',recording_type:'word_candidate',
    audio_path:comparison.audio_path,candidate_hsk_ids:[],quiz_eligible:true,
    review_status:'source_corroborated',rights_status:'unverified',license:null};
  const acoustic=[...automaticComparisons().filter(a=>a.key!=='ma3'),
    acousticApproval(Review.nativeDescriptor(word,native)),comparison];
  const app=await appHarness({acousticApprovals:acoustic,importedRecordings:[imported],supplementalPipelines:mixedPipeline()});
  assert.equal(app.run('questionVerified'),true);
  assert.equal(app.run('currentRec.source'),'audio_cmn');
  for(const source of ['pinyin_public','audio_cmn','mandarin_native']){
    app.get('correctionSource').value=source;
    assert.equal(app.run("correctionSelection('ma3').audio_path"),comparison.audio_path);
  }
  assert.equal(app.run('currentRec.source'),'audio_cmn');
});

test('imported comparison preference falls back without bypassing rejections or changing the tone',()=>{
  const assessment=mixedComparison(),publicComparison=comparison('ma3');
  const recording={source:'mandarin_native',recording_type:'word_candidate',audio_path:assessment.audio_path,
    quiz_eligible:true,review_status:'source_corroborated',rights_status:'unverified',license:null};
  const ledger={...acousticLedger([assessment]),supplemental_pipelines:mixedPipeline()};
  const publicRecordings={ma3:{audio_path:publicComparison.audio_path}};
  const choose=changed=>{
    const reviewed=Review.createIndex({version:1,approvals:[publicComparison]},ledger,{sourceRecordings:[changed]});
    return Review.correctionSelection(Policy,'ma3',{},publicRecordings,reviewed,'mandarin_native');
  };
  assert.equal(choose(recording).audio_path,assessment.audio_path);
  for(const changed of [
    {...recording,quiz_eligible:false},
    {...recording,review_status:'rejected'},
    {...recording,recording_type:'context_sentence'},
  ]){
    const fallback=choose(changed);
    assert.equal(fallback.audio_path,publicComparison.audio_path);
    assert.equal(fallback.approval.key,'ma3');
  }
});

test('whole-word corroboration binds an unseen imported pair to an assessed original reading',()=>{
  const pair={...word,id:'pair',pinyin:'ma ma',pinyin_syllables:['ma','ma'],
    lexical_pattern:'1-2',default_surface_pattern:'1-2'};
  const reference=acousticApproval(Review.nativeDescriptor(pair,native));
  const descriptor=Review.nativeDescriptor(pair,{...native,audio_path:'audio/mandarin_native/pair.mp3'});
  const digest='d'.repeat(64);
  const imported={
    ...descriptor,assessment:'automated',status:'screened',sha256:digest,
    source_url:'https://example.com/pair.mp3',license:null,distribution_scope:'local_only',
    evidence:{
      method:'cross-source-whole-word-v1',pipeline_sha256:'a'.repeat(64),native_model_sha256:'b'.repeat(64),
      audio_sha256:digest,label_identity:Review.identity(descriptor),supplied_pattern:'1-2',blind_pattern:'1-2',
      minimum_probability:.99,minimum_margin:.9,boundary_variants:3,boundary_stable:true,quality_ok:true,
      training_overlap:false,decoded_sha256:'e'.repeat(64),independent_gold_accuracy_claimed:false,
      recognition_checks:['raw','prepared'].map(input=>({input,audio_sha256:digest,transcript:'ma ma',decoded_bases:['ma','ma']})),
      whole_word_reference:{identity:Review.identity(reference),audio_path:reference.audio_path,sha256:reference.sha256,
        decoded_sha256:'f'.repeat(64),waveform_similarity:.2},
      comparison_support:reference.evidence.comparison_support,
    },
  };
  const ledger={...acousticLedger([...automaticComparisons(),reference,imported]),supplemental_pipelines:{
    'cross-source-whole-word-v1':{pipeline_sha256:'a'.repeat(64),native_model_sha256:'b'.repeat(64),
      minimum_probability:.95,minimum_margin:.25,independent_gold_accuracy_claimed:false},
  }};
  Review.validateApproval(imported);
  assert.equal(Review.createIndex({version:1,approvals:[]},ledger).get(Review.identity(imported)),imported);
  assert.equal(Review.createIndex({version:1,approvals:[]},ledger,{allowLocalOnly:false}).has(Review.identity(imported)),false);
  for(const edit of [
    a=>a.evidence.blind_pattern='1-3',
    a=>a.evidence.minimum_probability=.9,
    a=>a.evidence.minimum_margin=.2,
    a=>a.evidence.boundary_variants=2,
    a=>a.evidence.training_overlap=true,
    a=>a.evidence.boundary_stable=false,
    a=>a.evidence.quality_ok=false,
    a=>a.evidence.recognition_checks[1].decoded_bases=['ma','na'],
    a=>a.evidence.recognition_checks[0].audio_sha256=hash,
    a=>a.evidence.recognition_checks.forEach(check=>check.decoded_bases=null),
    a=>a.evidence.whole_word_reference.decoded_sha256=a.evidence.decoded_sha256,
    a=>a.evidence.whole_word_reference.waveform_similarity=.999,
    a=>a.evidence.whole_word_reference.audio_path='audio/mandarin_native/copy.mp3',
    a=>a.evidence.independent_gold_accuracy_claimed=true,
    a=>a.distribution_scope='redistributable',
    a=>a.surface_pattern='1-N',
  ]){
    const changed=structuredClone(imported);edit(changed);
    assert.throws(()=>Review.validateApproval(changed));
  }
  for(const edit of [
    l=>l.approvals.splice(l.approvals.indexOf(l.approvals.find(a=>a.audio_path===reference.audio_path)),1),
    l=>l.approvals.at(-1).evidence.whole_word_reference.sha256='9'.repeat(64),
    l=>l.approvals.at(-1).evidence.comparison_support[0].key='ma4',
    l=>l.supplemental_pipelines['cross-source-whole-word-v1'].native_model_sha256='9'.repeat(64),
  ]){
    const changed=structuredClone(ledger);edit(changed);
    assert.throws(()=>Review.createIndex({version:1,approvals:[]},changed));
  }
});

test('checked new standalone character audio is usable as a local comparison, not a sentence crop',()=>{
  const imported={
    source:'mandarin_native',recording_type:'word_candidate',audio_path:'audio/mandarin_native/ma1.mp3',
    rights_status:'unverified',license:null,review_status:'acoustic_screened',quiz_eligible:true,
  };
  const assessment=acousticApproval(Review.comparisonDescriptor('ma1',imported),'local_only');
  const local=Review.createIndex({version:1,approvals:[]},acousticLedger([assessment]),{sourceRecordings:[imported]});
  assert.equal(Review.correctionSelection(Policy,'ma1',{}, {},local).audio_path,imported.audio_path);
  const packaged=Review.createIndex({version:1,approvals:[]},acousticLedger([assessment]),{
    allowLocalOnly:false,sourceRecordings:[imported],
  });
  assert.equal(Review.correctionSelection(Policy,'ma1',{}, {},packaged),null);
});

test('neutral tone has a contextual example but cannot be forged as a fifth isolated clip',async()=>{
  const neutralWord={
    ...word,id:'neutral-word',word:'妈妈',pinyin:'ma ma',pinyin_syllables:['ma','ma'],
    lexical_tones:[1,0],lexical_pattern:'1-N',default_surface_pattern:'1-N',
  };
  const recording={...native,word:neutralWord.word,audio_path:'audio/audio_cmn/neutral/neutral.mp3'};
  const assessed=acousticApproval(Review.nativeDescriptor(neutralWord,recording));
  assessed.evidence.neutral_lexicon='CC-CEDICT';
  assessed.evidence.neutral_lexicon_sha256='c'.repeat(64);
  assessed.evidence.neutral_lexical_reading=['ma1','ma5'];
  assessed.evidence.comparison_support[1]=null;
  assessed.evidence.tones[1]={
    status:'screened',expected:'N',votes:{pyin:'N',praat:'N',world:'N'},
    start:.6,end:.75,voiced_seconds:.15,method:'contextual-neutral-reduction-1',
    prosody:{preceding_tone:'1',duration_ratio:.3,intensity_ratio:.4,
      pitch_ratios:{pyin:.6,praat:.61,world:.6},
      pitch_ranges:{pyin:[.58,.62],praat:[.59,.63],world:[.58,.62]},
      lexical_votes:{pyin:null,praat:null,world:null}},
  };
  const automatic=[...automaticComparisons(),assessed];
  const app=await appHarness({recording,importedWords:[neutralWord],acousticApprovals:automatic});
  assert.equal(app.run('questionVerified'),true);
  await app.run("playCorrection(1,'N')");
  assert.match(app.get('audioStatus').textContent,/neutral tone on syllable 2/);
  assert.equal(app.played.length,2);
  for(const edit of [
    entry=>delete entry.evidence.tones[1].prosody,
    entry=>delete entry.evidence.neutral_lexical_reading,
    entry=>entry.evidence.neutral_lexical_reading[0]='ma2',
    entry=>entry.evidence.tones[1].prosody.duration_ratio=1.1,
    entry=>entry.evidence.tones[1].prosody.intensity_ratio=1.1,
    entry=>entry.evidence.tones[1].prosody.pitch_ratios.pyin=1.4,
    entry=>entry.evidence.tones[1].prosody.lexical_votes.pyin='4',
    entry=>{
      entry.evidence.tones[1].votes.pyin=null;
      entry.evidence.tones[1].prosody.lexical_votes.pyin='4';
    },
    entry=>entry.evidence.comparison_support[1]={key:'ma5',audio_path:recording.audio_path,sha256:nativeHash},
  ]){
    const invalid=structuredClone(assessed);edit(invalid);
    assert.throws(()=>Review.createIndex({version:1,approvals:[]},acousticLedger([...automaticComparisons(),invalid])));
  }
  const standalone=acousticApproval(Review.comparisonDescriptor('ma5',recording));
  assert.throws(()=>Review.validateApproval(standalone),/comparison label/);
  assert.throws(()=>Review.createIndex({version:1,approvals:[]},{
    ...acousticLedger(automatic),neutral_lexicon_sha256:'d'.repeat(64),
  }),/dictionary fingerprint/);
  const corrected={...recording,surface_pattern:'1-4'};
  const staleIndex=Review.createIndex({version:1,approvals:[]},acousticLedger(automatic),{
    sourceRecordings:[corrected],sourceWords:[neutralWord],
  });
  assert.equal(Review.neutralSelection(staleIndex,'ma'),null);
});

test('corpus coverage requires all four lexical tones and contextual neutral',async()=>{
  const {requireToneCoverage}=await import('../scripts/review_audio.mjs');
  requireToneCoverage({toneCoverage:{1:10,2:10,3:10,4:10,N:1}});
  for(const tone of ['1','2','3','4','N']){
    const coverage={1:10,2:10,3:10,4:10,N:10};coverage[tone]=0;
    assert.throws(()=>requireToneCoverage({toneCoverage:coverage}),/lacks usable tone categories/);
  }
});

test('neutral reference cannot reuse a superseded source tone label',()=>{
  const neutralWord={...word,id:'neutral-current',word:'妈妈',pinyin:'ma ma',
    pinyin_syllables:['ma','ma'],lexical_pattern:'1-N',default_surface_pattern:'1-N'};
  const recording={...native,word:neutralWord.word,surface_pattern:'1-N'};
  const assessment=approve(Review.nativeDescriptor(neutralWord,recording));
  const index=Review.createIndex({version:1,approvals:[assessment]},null,{
    sourceRecordings:[{...recording,surface_pattern:'1-4'}],sourceWords:[neutralWord],
  });
  assert.equal(Review.neutralSelection(index,'ma'),null);
});
