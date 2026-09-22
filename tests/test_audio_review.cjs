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
  await assert.rejects(Review.verifyBytes(bytes,null),/no listening approval/);
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
async function appHarness({approvals=[],tamper=null,ledgerFailure=false,recording=native}={}){
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
  get('syllables').value='all';
  get('correctionSource').value='pinyin_public';
  const publicRecordings=Object.fromEntries(['1','2','3','4'].map(tone=>[
    `ma${tone}`,{audio_path:`audio/pinyin_public/ma${tone}.mp3`,source:'public'},
  ]));
  const data={
    '../data/hsk_words.json':[structuredClone(word)],'../data/definitions.json':{},
    '../data/recordings.json':[recording],'../data/pinyin_public_recordings.json':publicRecordings,
    '../data/correction_audio_quality.json':{},'../data/audio_reviews.json':{version:1,approvals},
  };
  const requests=[],played=[],errors=[];
  const context=vm.createContext({
    AudioReview:Review,CorrectionAudio:Policy,crypto:webcrypto,Blob,
    URL:{createObjectURL:()=>`blob:verified-${played.length}`,revokeObjectURL(){}},
    console:{error:(...args)=>errors.push(args)},navigator:{},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},
    document:{getElementById:get,createElement:element,addEventListener(){},querySelectorAll:()=>[]},
    Audio:class{
      constructor(url){played.push(url)}
      play(){return Promise.resolve()}
      pause(){}
    },
    fetch:async url=>{
      requests.push(url);
      if(url==='../data/audio_reviews.json'&&ledgerFailure)return {ok:false,status:404};
      if(url in data)return {ok:true,json:async()=>data[url]};
      return {ok:true,arrayBuffer:async()=>Uint8Array.from(url===tamper?[9,9]:bytes).buffer};
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

test('actual app starts paused with an empty ledger and requests no audio',async()=>{
  const app=await appHarness();
  assert.match(app.get('prompt').innerHTML,/Practice paused/);
  assert.equal(app.get('answers').children.length,0);
  assert.equal(app.get('play').disabled,true);
  assert.equal(app.get('record').disabled,true);
  assert.equal(app.played.length,0);
  assert.equal(app.requests.filter(url=>url.endsWith('.mp3')).length,0);
});

test('actual app rejects a missing ledger instead of trusting the old corpus',async()=>{
  const app=await appHarness({ledgerFailure:true});
  assert.match(app.get('reviewStatus').textContent,/Practice blocked/);
  assert.equal(app.played.length,0);
  assert.equal(app.get('next').disabled,true);
});

test('every comparison must be reviewed, not only the correct answer',async()=>{
  const app=await appHarness({approvals:allApprovals().filter(a=>a.key!=='ma3')});
  assert.match(app.get('prompt').innerHTML,/Practice paused/);
  assert.equal(app.played.length,0);
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
  assert.match(app.get('prompt').innerHTML,/Practice paused/);
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
  assert.equal(practiceInventory(data,index(allApprovals().filter(a=>a.key!=='ma3'))).audio.size,0);
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
