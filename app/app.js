let words=[], recordings=[], correctionRecordings={}, correctionQuality={}, byWord=new Map(), patternsByWord=new Map(), current=null, currentRec=null, currentNative=null, nativeAudio=null, correctionContext=null, correctionSource=null, correctionPlayId=0, mediaRecorder=null, mediaStream=null, recordingStarting=false, mineUrl=null, mineAudio=null, overlayAudios=[], selectedTones=[], quizHistory=[];
let results=[];
const rawPinyinBuffers=new Map(), correctionBuffers=new Map(), RAW_BUFFER_CACHE_LIMIT=64, CORRECTION_BUFFER_CACHE_LIMIT=32, QUIZ_HISTORY_LIMIT=50, CORRECTION_LEAD_SECONDS=.12, CORRECTION_TAIL_SECONDS=.20, NATIVE_SYLLABLE_GAP_SECONDS=.06;
const $=id=>document.getElementById(id);
const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function saveResults(){updateProgress()}
function isPlaybackInterruption(error){return error?.name==='AbortError'}
function cachedValue(cache,key){
  if(!cache.has(key))return null;
  const value=cache.get(key);
  cache.delete(key);
  cache.set(key,value);
  return value;
}
function cacheValue(cache,key,value,limit){
  cache.set(key,value);
  while(cache.size>limit)cache.delete(cache.keys().next().value);
}
function setAudioStatus(message='',error=false){
  $('audioStatus').textContent=message;
  $('audioStatus').classList.toggle('error',error);
}
function resetRecordButton(){
  $('record').innerHTML='<span aria-hidden="true">●</span> Record me';
  $('record').setAttribute('aria-pressed','false');
}
function updateBackButton(){$('back').disabled=!quizHistory.length}
function setPracticeControlsDisabled(disabled){
  for(const id of ['play','back','next','syllables','sandhiOnly'])$(id).disabled=disabled;
  document.querySelectorAll('.tone-choice').forEach(button=>button.disabled=disabled);
  if(!disabled)updateBackButton();
}
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
  const qualityResponse=await fetch('../data/correction_audio_quality.json');
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
function currentSnapshot(){
  if(!current)return null;
  return {
    word:current,
    recording:currentRec,
    correct:current._correct,
    graded:current._graded,
    selectedTones:[...selectedTones],
    promptHTML:$('prompt').innerHTML,
    revealHTML:$('reveal').innerHTML,
    revealHidden:$('reveal').classList.contains('hidden'),
  };
}
function restoreToneState(state){
  selectedTones=[...state.selectedTones];
  const correctTones=state.correct.split('-');
  selectedTones.forEach((tone,index)=>{
    if(!tone)return;
    const column=$('answers').children[index];
    const selected=column.querySelector(`[data-tone="${tone}"]`);
    selected.classList.add('selected');
    if(!state.graded)return;
    selected.classList.add(tone===correctTones[index]?'correct':'wrong');
    if(tone!==correctTones[index])column.querySelector(`[data-tone="${correctTones[index]}"]`).classList.add('correct-answer');
  });
}
function back(play=false){
  if(!quizHistory.length)return;
  stopAllAudio();
  clearPersonalRecording();
  setAudioStatus();
  const state=quizHistory.pop();
  current=state.word;
  currentRec=state.recording;
  currentNative=nativePlayback(current,currentRec);
  current._correct=state.correct;
  current._graded=state.graded;
  $('prompt').innerHTML=state.promptHTML;
  renderToneChoices();
  restoreToneState(state);
  $('reveal').innerHTML=state.revealHTML;
  $('reveal').classList.toggle('hidden',state.revealHidden);
  updateBackButton();
  if(currentNative?.playable&&play)playNative();
}
function next(play=false,remember=true){
  if(remember){
    const snapshot=currentSnapshot();
    if(snapshot){
      quizHistory.push(snapshot);
      while(quizHistory.length>QUIZ_HISTORY_LIMIT)quizHistory.shift();
    }
  }
  stopAllAudio();
  clearPersonalRecording();
  setAudioStatus();
  const pool=filtered();
  if(!pool.length){
    current=null; currentRec=null; currentNative=null; $('prompt').innerHTML='<div class="muted">No words match the current filters.</div><p>Try another syllable-count setting or turn off Sandhi only.</p>';
    $('answers').innerHTML=''; $('reveal').classList.add('hidden'); updateBackButton(); return;
  }
  current=choose(pool); const rs=recordingsFor(current); currentRec=rs.length?choose(rs):null; currentNative=nativePlayback(current,currentRec);
  current._graded=false;
  const correct=patternFor(current,currentRec);
  current._correct=correct;
  $('prompt').innerHTML=`<div class="muted">Listen first — word hidden until you answer</div>${currentNative?.playable?`<div class="muted">Speaker: ${escapeHTML(currentNative.speaker)} · ${escapeHTML(sourceName(currentNative.source||''))}</div>`:'<div class="muted">No local recording for this item.</div>'}`;
  $('reveal').classList.add('hidden'); renderToneChoices();
  updateBackButton();
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
function stopPersonalAudio(){
  if(mineAudio){
    mineAudio.pause();
    mineAudio.currentTime=0;
    mineAudio=null;
  }
  for(const audio of overlayAudios){
    audio.pause();
    audio.currentTime=0;
  }
  overlayAudios=[];
}
function clearPersonalRecording(){
  stopPersonalAudio();
  if(mineUrl)URL.revokeObjectURL(mineUrl);
  mineUrl=null;
  $('playMine').disabled=true;
  $('overlay').disabled=true;
}
function stopAllAudio(){
  stopNative();
  stopCorrection();
  stopPersonalAudio();
}
function playNative(){
  const u=currentNative?.url;if(!u)return;
  stopCorrection();
  stopNative();
  stopPersonalAudio();
  const audio=new Audio(u);
  nativeAudio=audio;
  audio.onended=()=>{if(nativeAudio===audio)nativeAudio=null};
  audio.play().then(()=>setAudioStatus('Playing the native recording.')).catch(error=>{
    if(nativeAudio===audio)nativeAudio=null;
    if(isPlaybackInterruption(error))return;
    setAudioStatus('The native recording could not be played.',true);
    console.error('Native audio failed',error);
  });
}
function correctionKey(pinyin,tone){return CorrectionAudio.correctionKey(pinyin,tone)}
function correctionSelection(key){
  const selected=CorrectionAudio.correctionSelection(key,correctionQuality,correctionRecordings);
  return selected?{...selected,url:`../${selected.audio_path}`}:null;
}
function getCorrectionContext(){
  if(!correctionContext)correctionContext=new (window.AudioContext||window.webkitAudioContext)();
  return correctionContext;
}
async function rawPinyinBuffer(key){
  const cached=cachedValue(rawPinyinBuffers,key);
  if(cached)return cached;
  const promise=(async()=>{
    const selected=correctionSelection(key);
    if(!selected)throw new Error(`No verified correction recording for ${key}`);
    const response=await fetch(selected.url);
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const context=getCorrectionContext();
    return context.decodeAudioData(await response.arrayBuffer());
  })();
  cacheValue(rawPinyinBuffers,key,promise,RAW_BUFFER_CACHE_LIMIT);
  try{return await promise}catch(error){
    if(rawPinyinBuffers.get(key)===promise)rawPinyinBuffers.delete(key);
    throw error;
  }
}
async function pinyinSequenceBuffer(keys){
  const cacheKey=keys.join('+');
  const cached=cachedValue(correctionBuffers,cacheKey);
  if(cached)return cached;
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
      const sourceChannels=Array.from(
        {length:buffer.numberOfChannels},
        (_,channel)=>buffer.getChannelData(channel),
      );
      const gain=CorrectionAudio.normalizationGain(sourceChannels);
      for(let channel=0;channel<channels;channel++){
        const sourceChannel=Math.min(channel,buffer.numberOfChannels-1);
        const input=buffer.getChannelData(sourceChannel);
        const output=combined.getChannelData(channel);
        for(let index=0;index<input.length;index++)output[offset+index]=input[index]*gain;
      }
      offset+=buffer.length+gap;
    }
    return combined;
  })();
  cacheValue(correctionBuffers,cacheKey,promise,CORRECTION_BUFFER_CACHE_LIMIT);
  try{return await promise}catch(error){
    if(correctionBuffers.get(cacheKey)===promise)correctionBuffers.delete(cacheKey);
    throw error;
  }
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
  const limiter=context.createDynamicsCompressor();
  limiter.threshold.value=-3;
  limiter.knee.value=0;
  limiter.ratio.value=20;
  limiter.attack.value=.003;
  limiter.release.value=.12;
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
    source.connect(highpass).connect(presence).connect(clarity).connect(headroom).connect(limiter).connect(context.destination);
  }else{
    source.connect(limiter).connect(context.destination);
  }
}
async function playPinyinKey(key){
  return playPinyinSequence([key]);
}
async function playPinyinSequence(keys){
  stopNative();
  stopCorrection();
  stopPersonalAudio();
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
    setAudioStatus('Playing the selected tone.');
  }catch(error){
    if(playId===correctionPlayId){
      setAudioStatus('The selected tone recording could not be played.',true);
      console.error(`Pinyin audio failed for ${keys.join('+')}`,error);
    }
  }
}
async function playCorrection(index,tone){
  const pinyin=current.pinyin_syllables?.[index];
  if(!pinyin||tone==='N'){
    stopNative();
    stopCorrection();
    return;
  }
  const key=correctionKey(pinyin,tone);
  if(!correctionSelection(key)){
    stopNative();
    stopCorrection();
    setAudioStatus('No comparison recording is available for this tone.');
    return;
  }
  return playPinyinKey(key);
}
$('play').onclick=playNative;
$('back').onclick=()=>back(true);
$('next').onclick=()=>next(true);
$('syllables').onchange=()=>{quizHistory=[];next(true,false)};
$('sandhiOnly').onchange=()=>{quizHistory=[];next(true,false)};
$('resetProgress').onclick=()=>{if(confirm('Clear all saved tone-practice results?')){results=[];saveResults()}};
$('record').onclick=async()=>{
  if(recordingStarting)return;
  if(mediaRecorder?.state==='recording'){
    mediaRecorder.stop();
    setAudioStatus('Finishing your recording.');
    return;
  }
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){
    setAudioStatus('Audio recording is not available on this device.',true);
    return;
  }
  stopAllAudio();
  recordingStarting=true;
  $('record').disabled=true;
  setAudioStatus('Waiting for microphone permission…');
  let stream=null;
  let failed=false;
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:true});
    const sessionChunks=[];
    const recorder=new MediaRecorder(stream);
    mediaStream=stream;
    mediaRecorder=recorder;
    recorder.ondataavailable=event=>{if(event.data.size)sessionChunks.push(event.data)};
    recorder.onerror=event=>{
      failed=true;
      const isCurrent=mediaRecorder===recorder||mediaStream===stream;
      stream.getTracks().forEach(track=>track.stop());
      if(mediaStream===stream)mediaStream=null;
      if(mediaRecorder===recorder)mediaRecorder=null;
      if(!isCurrent)return;
      recordingStarting=false;
      $('record').disabled=false;
      resetRecordButton();
      setPracticeControlsDisabled(false);
      setAudioStatus(`Recording failed${event.error?.message?`: ${event.error.message}`:'.'}`,true);
    };
    recorder.onstop=()=>{
      const isCurrent=mediaRecorder===recorder||mediaStream===stream;
      stream.getTracks().forEach(track=>track.stop());
      if(mediaStream===stream)mediaStream=null;
      if(mediaRecorder===recorder)mediaRecorder=null;
      if(!isCurrent)return;
      recordingStarting=false;
      $('record').disabled=false;
      resetRecordButton();
      setPracticeControlsDisabled(false);
      if(failed)return;
      const blob=new Blob(sessionChunks,{type:recorder.mimeType});
      if(!blob.size){
        setAudioStatus('No audio was captured. Try recording again.',true);
        return;
      }
      mineUrl=URL.createObjectURL(blob);
      $('playMine').disabled=false;
      $('overlay').disabled=false;
      setAudioStatus('Your recording is ready.');
    };
    clearPersonalRecording();
    setPracticeControlsDisabled(true);
    recordingStarting=false;
    $('record').disabled=false;
    recorder.start();
    $('record').innerHTML='<span aria-hidden="true">■</span> Stop';
    $('record').setAttribute('aria-pressed','true');
    setAudioStatus('Recording… Tap Stop when you are finished.');
  }catch(error){
    stream?.getTracks().forEach(track=>track.stop());
    if(mediaStream===stream)mediaStream=null;
    if(!mediaStream)mediaRecorder=null;
    recordingStarting=false;
    $('record').disabled=false;
    resetRecordButton();
    setPracticeControlsDisabled(false);
    const message=error.name==='NotAllowedError'
      ?'Microphone permission was denied. Allow it in Android Settings to record yourself.'
      :error.name==='NotFoundError'
        ?'No microphone is available on this device.'
        :'The microphone could not be started. Close other audio apps and try again.';
    setAudioStatus(message,true);
    console.error('Microphone failed',error);
  }
}
$('playMine').onclick=()=>{
  if(!mineUrl)return;
  stopNative();
  stopCorrection();
  stopPersonalAudio();
  const audio=new Audio(mineUrl);
  mineAudio=audio;
  audio.onended=()=>{if(mineAudio===audio)mineAudio=null};
  audio.play().then(()=>setAudioStatus('Playing your recording.')).catch(error=>{
    if(mineAudio===audio)mineAudio=null;
    if(isPlaybackInterruption(error))return;
    setAudioStatus('Your recording could not be played.',true);
    console.error('Recorded audio failed',error);
  });
};
$('overlay').onclick=async()=>{
  const nativeUrl=audioURL(currentRec);
  if(!nativeUrl||!mineUrl)return;
  stopAllAudio();
  const native=new Audio(nativeUrl),mine=new Audio(mineUrl);
  overlayAudios=[native,mine];
  const finished=()=>{if(overlayAudios.includes(native)&&native.ended&&mine.ended)overlayAudios=[]};
  native.onended=finished;
  mine.onended=finished;
  try{
    await Promise.all([native.play(),mine.play()]);
    setAudioStatus('Playing the native and recorded audio together.');
  }catch(error){
    stopPersonalAudio();
    if(isPlaybackInterruption(error))return;
    setAudioStatus('Overlay playback could not be started.',true);
    console.error('Overlay audio failed',error);
  }
};
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden)return;
  if(mediaRecorder?.state==='recording')mediaRecorder.stop();
  stopAllAudio();
});
window.addEventListener('beforeunload',()=>{
  mediaStream?.getTracks().forEach(track=>track.stop());
  stopAllAudio();
  clearPersonalRecording();
});
resetRecordButton();
if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){
  $('record').disabled=true;
  setAudioStatus('Audio recording is not available on this device.',true);
}
updateProgress();
updateBackButton();
load().then(()=>next(true,false)).catch(error=>{
  console.error(error);
  $('prompt').innerHTML=`<div class="muted">The practice data could not load.</div><p>${error.message}. Start the app with <code>python3 scripts/serve.py</code> and open its localhost URL.</p>`;
  $('answers').innerHTML='';
});
