let words=[], recordings=[], correctionRecordings={}, correctionQuality={}, byWord=new Map(), patternsByWord=new Map(), current=null, currentRec=null, currentNative=null, nativeAudio=null, correctionContext=null, correctionSource=null, correctionPlayId=0, mediaRecorder=null, chunks=[], mineUrl=null, selectedTones=[];
let results=[];
const rawPinyinBuffers=new Map(), correctionBuffers=new Map(), CORRECTION_LEAD_SECONDS=.12, CORRECTION_TAIL_SECONDS=.20, NATIVE_SYLLABLE_GAP_SECONDS=.06;
const $=id=>document.getElementById(id);
const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function saveResults(){updateProgress()}
function updateProgress(){
  const correct=results.filter(r=>r.correct).length;
  $('progress').textContent=`${results.length} attempts · ${correct} correct${results.length?` (${Math.round(correct/results.length*100)}%)`:''}`;
}
function sourceName(source){
  if(source==='audio_cmn')return 'audio-cmn';
  if(source==='mp3_chinese_pinyin_sound')return 'public pinyin';
  return source;
}
function expectedPattern(w){return w.default_surface_pattern||w.lexical_pattern}
function rebuildIndex(){
  byWord=new Map();
  patternsByWord=new Map();
  for(const w of words){
    if(!patternsByWord.has(w.word))patternsByWord.set(w.word,new Set());
    patternsByWord.get(w.word).add(expectedPattern(w));
  }
  const selected=recordings.filter(r=>r.source==='audio_cmn' && (r.language_code||'zh')==='zh');
  for(const r of selected){if(!byWord.has(r.word))byWord.set(r.word,[]);byWord.get(r.word).push(r)}
}
async function load(){
  const wordResponse=await fetch('../data/hsk_words.json');
  if(!wordResponse.ok)throw new Error(`HSK data failed: HTTP ${wordResponse.status}`);
  words=await wordResponse.json();
  const definitionResponse=await fetch('../data/definitions.json');
  if(!definitionResponse.ok)throw new Error(`definitions failed: HTTP ${definitionResponse.status}`);
  const definitions=await definitionResponse.json();
  for(const word of words)word.definition=definitions[word.id]||word.definition||'';
  const recordingResponse=await fetch('../data/recordings.json');
  if(!recordingResponse.ok)throw new Error(`recordings failed: HTTP ${recordingResponse.status}`);
  recordings=await recordingResponse.json();
  const correctionResponse=await fetch('../data/pinyin_public_recordings.json');
  if(correctionResponse.ok)correctionRecordings=await correctionResponse.json();
  else if(correctionResponse.status!==404)throw new Error(`Pinyin corrections failed: HTTP ${correctionResponse.status}`);
  const qualityResponse=await fetch('../data/audio_cmn_syllable_quality.json');
  if(!qualityResponse.ok)throw new Error(`Syllable quality data failed: HTTP ${qualityResponse.status}`);
  correctionQuality=await qualityResponse.json();
  $('progress').textContent='';
  rebuildIndex();
}
function recordingsFor(w){
  const expected=expectedPattern(w);
  const ambiguous=(patternsByWord.get(w.word)?.size||0)>1;
  return (byWord.get(w.word)||[]).filter(r=>{
    if(r.hsk_id)return r.hsk_id===w.id;
    return r.surface_pattern?r.surface_pattern===expected:!ambiguous;
  });
}
function nativePlayback(w,r){
  return {
    playable:Boolean(audioURL(r)),
    url:audioURL(r),
    source:r?.source||null,
    speaker:r?.speaker||'unknown',
    filename:r?.filename||null,
  };
}
function hasAlignedCorrections(w){
  const tones=(expectedPattern(w)||'').split('-').filter(Boolean);
  return Array.isArray(w.pinyin_syllables) && w.pinyin_syllables.length===tones.length;
}
function hasVerifiedCorrections(w){
  const tones=(expectedPattern(w)||'').split('-');
  return (w.pinyin_syllables||[]).every((pinyin,index)=>
    tones[index]==='N'||Boolean(correctionSelection(correctionKey(pinyin,tones[index])))
  );
}
function filtered(){return words.filter(w=>{
  const syllables=$('syllables').value; const count=(w.lexical_tones||[]).length;
  if(syllables==='one' && count!==1)return false;
  if(syllables==='two' && count!==2)return false;
  if($('sandhiOnly').checked && !w.sandhi_tags.length)return false;
  if(!recordingsFor(w).length || !hasAlignedCorrections(w) || !hasVerifiedCorrections(w))return false;
  return true;
})}
function choose(a){return a[Math.floor(Math.random()*a.length)]}
function patternFor(w,r){return (r&&r.surface_pattern)||expectedPattern(w)}
function renderToneChoices(){
  const syllables=current._correct.split('-'), tones=['1','2','3','4','N'];
  selectedTones=Array(syllables.length).fill(null); $('answers').innerHTML=''; $('answers').className='answers tone-columns';
  syllables.forEach((_,index)=>{
    const column=document.createElement('div'); column.className='tone-column';
    const heading=document.createElement('div'); heading.className='tone-heading'; heading.textContent=`Syllable ${index+1}`; column.appendChild(heading);
    tones.forEach(tone=>{const button=document.createElement('button'); button.className='tone-choice'; button.dataset.tone=tone; button.innerHTML=`${tone==='N'?'Neutral':`${tone}th tone`} <small>${tone}</small>`; button.onclick=()=>{
      playCorrection(index,tone);
      if(current._graded)return;
      selectedTones[index]=tone; [...column.querySelectorAll('button')].forEach(x=>x.classList.remove('selected')); button.classList.add('selected');
      if(selectedTones.every(Boolean))grade(selectedTones.join('-'),current._correct);
    }; column.appendChild(button)});
    $('answers').appendChild(column);
  });
}
function next(play=false){
  stopNative();
  stopCorrection();
  const pool=filtered();
  if(!pool.length){
    current=null; currentRec=null; currentNative=null; $('prompt').innerHTML='<div class="muted">No words match the current filters.</div><p>Try another syllable-count setting or turn off Sandhi only.</p>';
    $('answers').innerHTML=''; return;
  }
  current=choose(pool); const rs=recordingsFor(current); currentRec=rs.length?choose(rs):null; currentNative=nativePlayback(current,currentRec);
  current._graded=false;
  const correct=patternFor(current,currentRec);
  current._correct=correct;
  $('prompt').innerHTML=`<div class="muted">Listen first — word hidden until you answer</div>${currentNative?.playable?`<div class="muted">Speaker: ${escapeHTML(currentNative.speaker)} · ${escapeHTML(sourceName(currentNative.source||''))}</div>`:'<div class="muted">No local recording for this item.</div>'}`;
  $('reveal').classList.add('hidden'); renderToneChoices();
  if(currentNative?.playable && play)playNative();
}
function grade(p,correct){
  current._graded=true;
  const correctTones=correct.split('-');
  selectedTones.forEach((tone,index)=>{
    const column=$('answers').children[index];
    const selected=column.querySelector('.selected');
    const correctButton=column.querySelector(`[data-tone="${correctTones[index]}"]`);
    selected.classList.add(tone===correctTones[index]?'correct':'wrong');
    if(tone!==correctTones[index])correctButton.classList.add('correct-answer');
  });
  results.push({timestamp:new Date().toISOString(),word:current.word,pinyin:current.pinyin,selected_pattern:p,correct_pattern:correct,correct:p===correct,source:currentNative?.source||null,recording:currentNative?.filename||null});
  saveResults();
  const tags=current.sandhi_tags.map(x=>`<span class="tag">${x}</span>`).join('');
  const definition=current.definition?`<p class="definition"><strong>Definition:</strong> ${escapeHTML(current.definition)}</p>`:'';
  $('reveal').innerHTML=`<div class="word">${escapeHTML(current.word)}</div><div class="pinyin">${escapeHTML(current.pinyin)}</div><p>Correct tone pattern: <b>${escapeHTML(correct)}</b></p>${definition}<div>${tags}</div>${current.surface_label_needs_clip_review?'<p class="muted">This word may vary with prosodic grouping.</p>':''}`;
  $('reveal').classList.remove('hidden');
}
function audioURL(r){if(!r)return null;let p=r.audio_path||''; if(p.startsWith('audio/'))return '../'+p; return p}
function stopNative(){
  if(!nativeAudio)return;
  nativeAudio.pause();
  nativeAudio.currentTime=0;
  nativeAudio=null;
}
function playNative(){
  const u=currentNative?.url;if(!u)return;
  stopCorrection();
  stopNative();
  const audio=new Audio(u);
  nativeAudio=audio;
  audio.onended=()=>{if(nativeAudio===audio)nativeAudio=null};
  audio.play().catch(error=>{if(nativeAudio===audio)nativeAudio=null;console.error('Native audio failed',error)});
}
function correctionKey(pinyin,tone){
  return pinyin.toLowerCase().replace(/ü/g,'v')+tone;
}
function correctionSelection(key){
  const quality=correctionQuality[key];
  if(quality?.status==='bad'){
    const replacement=correctionRecordings[key];
    return replacement?.audio_path?{
      url:`../${replacement.audio_path}`,
      enhanced:true,
      source:replacement.source,
    }:null;
  }
  const audioCmnKey=key==='ju4'?'jv4':key;
  return {
    url:`../audio/audio_cmn/syllabs/cmn-${audioCmnKey}.mp3`,
    enhanced:false,
    source:'audio_cmn',
  };
}
function getCorrectionContext(){
  if(!correctionContext)correctionContext=new (window.AudioContext||window.webkitAudioContext)();
  return correctionContext;
}
async function rawPinyinBuffer(key){
  if(rawPinyinBuffers.has(key))return rawPinyinBuffers.get(key);
  const promise=(async()=>{
    const selected=correctionSelection(key);
    if(!selected)throw new Error(`No verified correction recording for ${key}`);
    const response=await fetch(selected.url);
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const context=getCorrectionContext();
    return context.decodeAudioData(await response.arrayBuffer());
  })();
  rawPinyinBuffers.set(key,promise);
  try{return await promise}catch(error){rawPinyinBuffers.delete(key);throw error}
}
async function pinyinSequenceBuffer(keys){
  const cacheKey=keys.join('+');
  if(correctionBuffers.has(cacheKey))return correctionBuffers.get(cacheKey);
  const promise=(async()=>{
    const context=getCorrectionContext();
    const decoded=await Promise.all(keys.map(rawPinyinBuffer));
    const sampleRate=decoded[0].sampleRate;
    if(decoded.some(buffer=>buffer.sampleRate!==sampleRate))throw new Error('Pinyin sample rates do not match');
    const channels=Math.max(...decoded.map(buffer=>buffer.numberOfChannels));
    const lead=Math.round(sampleRate*CORRECTION_LEAD_SECONDS);
    const tail=Math.round(sampleRate*CORRECTION_TAIL_SECONDS);
    const gap=Math.round(sampleRate*NATIVE_SYLLABLE_GAP_SECONDS);
    const length=lead+tail+decoded.reduce((total,buffer)=>total+buffer.length,0)+gap*Math.max(0,decoded.length-1);
    const combined=context.createBuffer(channels,length,sampleRate);
    let offset=lead;
    for(const buffer of decoded){
      for(let channel=0;channel<channels;channel++){
        const sourceChannel=Math.min(channel,buffer.numberOfChannels-1);
        combined.copyToChannel(buffer.getChannelData(sourceChannel),channel,offset);
      }
      offset+=buffer.length+gap;
    }
    return combined;
  })();
  correctionBuffers.set(cacheKey,promise);
  try{return await promise}catch(error){correctionBuffers.delete(cacheKey);throw error}
}
function stopCorrection(){
  correctionPlayId++;
  const source=correctionSource;
  correctionSource=null;
  if(source){
    try{source.stop()}catch(error){if(error.name!=='InvalidStateError')throw error}
    source.disconnect();
  }
}
function connectPinyinSource(source,context,key){
  if(correctionSelection(key)?.enhanced){
    const highpass=context.createBiquadFilter();
    highpass.type='highpass';
    highpass.frequency.value=70;
    highpass.Q.value=.7;
    const presence=context.createBiquadFilter();
    presence.type='peaking';
    presence.frequency.value=2200;
    presence.Q.value=.8;
    presence.gain.value=2;
    const clarity=context.createBiquadFilter();
    clarity.type='highshelf';
    clarity.frequency.value=4500;
    clarity.gain.value=3;
    const headroom=context.createGain();
    headroom.gain.value=.88;
    source.connect(highpass).connect(presence).connect(clarity).connect(headroom).connect(context.destination);
  }else{
    source.connect(context.destination);
  }
}
async function playPinyinKey(key){
  return playPinyinSequence([key]);
}
async function playPinyinSequence(keys){
  stopNative();
  stopCorrection();
  const playId=correctionPlayId;
  try{
    const context=getCorrectionContext();
    await context.resume();
    const buffer=await pinyinSequenceBuffer(keys);
    if(playId!==correctionPlayId)return;
    const source=context.createBufferSource();
    source.buffer=buffer;
    connectPinyinSource(source,context,keys[0]);
    source.onended=()=>{if(correctionSource===source)correctionSource=null};
    correctionSource=source;
    source.start();
  }catch(error){
    if(playId===correctionPlayId)console.error(`Pinyin audio failed for ${keys.join('+')}`,error);
  }
}
async function playCorrection(index,tone){
  const pinyin=current.pinyin_syllables?.[index];
  if(!pinyin||tone==='N'){
    stopNative();
    stopCorrection();
    return;
  }
  return playPinyinKey(correctionKey(pinyin,tone));
}
$('play').onclick=playNative;
$('next').onclick=()=>next(true);
$('syllables').onchange=()=>next(true);
$('sandhiOnly').onchange=()=>next(true);
$('resetProgress').onclick=()=>{if(confirm('Clear all saved tone-practice results?')){results=[];saveResults()}};
$('record').onclick=async()=>{
  if(mediaRecorder&&mediaRecorder.state==='recording'){mediaRecorder.stop();$('record').textContent='● Record me';return}
  const stream=await navigator.mediaDevices.getUserMedia({audio:true}); chunks=[]; mediaRecorder=new MediaRecorder(stream); mediaRecorder.ondataavailable=e=>chunks.push(e.data); mediaRecorder.onstop=()=>{if(mineUrl)URL.revokeObjectURL(mineUrl);mineUrl=URL.createObjectURL(new Blob(chunks,{type:mediaRecorder.mimeType}));$('playMine').disabled=false;$('overlay').disabled=false;stream.getTracks().forEach(t=>t.stop())};mediaRecorder.start();$('record').textContent='■ Stop';
}
$('playMine').onclick=()=>{if(mineUrl)new Audio(mineUrl).play()};
$('overlay').onclick=()=>{let u=audioURL(currentRec);if(!u||!mineUrl)return;let a=new Audio(u),b=new Audio(mineUrl);a.play();b.play()};
updateProgress();
load().then(()=>next(true)).catch(error=>{
  console.error(error);
  $('prompt').innerHTML=`<div class="muted">The practice data could not load.</div><p>${error.message}. Start the app with <code>python3 scripts/serve.py</code> and open its localhost URL.</p>`;
  $('answers').innerHTML='';
});
