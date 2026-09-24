let words=[], recordings=[], correctionRecordings={}, correctionQuality={}, byWord=new Map(), readingsByWord=new Map(), current=null, currentRec=null, currentNative=null, nativeAudio=null, correctionContext=null, correctionSource=null, correctionPlayId=0, mediaRecorder=null, mediaStream=null, recordingStarting=false, mineUrl=null, mineAudio=null, overlayAudios=[], selectedTones=[], quizHistory=[];
let results=[];
let audioReviews=new Map(), nativePlayId=0, overlayPlayId=0, nativeObjectURL=null, overlayObjectURL=null, questionLoadId=0, questionVerified=false;
const reviewedAudioBytes=new Map();
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
  for(const id of ['play','back','next','syllables','correctionSource','sandhiOnly'])$(id).disabled=disabled;
  document.querySelectorAll('.tone-choice').forEach(button=>button.disabled=disabled);
  if(!disabled)updateBackButton();
}
function updateProgress(){
  const correct=results.filter(r=>r.correct).length;
  $('progress').textContent=`${results.length} attempts · ${correct} correct${results.length?` (${Math.round(correct/results.length*100)}%)`:''}`;
}
function sourceName(source){
  if(source==='audio_cmn')return 'audio-cmn';
  if(source==='mandarin_native')return 'Mandarin Native';
  if(source==='mp3_chinese_pinyin_sound')return 'public pinyin';
  return source;
}
function expectedPattern(w){return w.default_surface_pattern||w.lexical_pattern}
function readingKey(w){return `${w.pinyin||''}|${(w.pinyin_syllables||[]).join('+')}|${w.lexical_pattern||''}`}
function rebuildIndex(){
  byWord=new Map();
  readingsByWord=new Map();
  for(const w of words){
    if(!readingsByWord.has(w.word))readingsByWord.set(w.word,new Set());
    readingsByWord.get(w.word).add(readingKey(w));
  }
  for(const {word,recording} of AudioReview.nativeCandidates(words,recordings)){
    if(!byWord.has(word.word))byWord.set(word.word,[]);
    if(!byWord.get(word.word).includes(recording))byWord.get(word.word).push(recording);
  }
}
async function load(){
  const wordResponse=await fetch('../data/hsk_words.json');
  if(!wordResponse.ok)throw new Error(`HSK data failed: HTTP ${wordResponse.status}`);
  words=await wordResponse.json();
  const importedWordResponse=await fetch('../data/mandarin_native_words.json');
  if(!importedWordResponse.ok)throw new Error(`Imported vocabulary failed: HTTP ${importedWordResponse.status}`);
  const importedWords=await importedWordResponse.json();
  if(importedWords.version!==1||!Array.isArray(importedWords.words))throw new Error('Invalid imported vocabulary');
  words.push(...importedWords.words);
  if(new Set(words.map(word=>word.id)).size!==words.length)throw new Error('Duplicate vocabulary identifiers');
  const definitionResponse=await fetch('../data/definitions.json');
  if(!definitionResponse.ok)throw new Error(`definitions failed: HTTP ${definitionResponse.status}`);
  const definitions=await definitionResponse.json();
  for(const word of words)word.definition=definitions[word.id]||word.definition||'';
  const recordingResponse=await fetch('../data/recordings.json');
  if(!recordingResponse.ok)throw new Error(`recordings failed: HTTP ${recordingResponse.status}`);
  recordings=await recordingResponse.json();
  const importedResponse=await fetch('../data/mandarin_native_recordings.json');
  if(!importedResponse.ok)throw new Error(`Mandarin Native index failed: HTTP ${importedResponse.status}`);
  const imported=await importedResponse.json();
  if(imported.version!==1||!Array.isArray(imported.recordings))throw new Error('Invalid Mandarin Native recording index');
  if(imported.explore_vocabulary!==undefined&&!Array.isArray(imported.explore_vocabulary))throw new Error('Invalid Mandarin Native vocabulary index');
  recordings.push(...imported.recordings.filter(recording=>recording.recording_type==='word_candidate'));
  const correctionResponse=await fetch('../data/pinyin_public_recordings.json');
  if(correctionResponse.ok)correctionRecordings=await correctionResponse.json();
  else if(correctionResponse.status!==404)throw new Error(`Pinyin corrections failed: HTTP ${correctionResponse.status}`);
  const qualityResponse=await fetch('../data/correction_audio_quality.json');
  if(!qualityResponse.ok)throw new Error(`Syllable quality data failed: HTTP ${qualityResponse.status}`);
  correctionQuality=await qualityResponse.json();
  const reviewResponse=await fetch('../data/audio_reviews.json',{cache:'no-store'});
  if(!reviewResponse.ok)throw new Error(`Listening approvals failed: HTTP ${reviewResponse.status}`);
  const acousticResponse=await fetch('../data/acoustic_reviews.json',{cache:'no-store'});
  if(!acousticResponse.ok)throw new Error(`Acoustic screening data failed: HTTP ${acousticResponse.status}`);
  audioReviews=AudioReview.createIndex(await reviewResponse.json(),await acousticResponse.json(),{
    sourceRecordings:recordings,sourceWords:words,
  });
  $('progress').textContent='';
  rebuildIndex();
  const eligible=practiceWords();
  for(const option of Array.from($('syllables').options||[])){
    const available=eligible.some(word=>{
      const count=word.pinyin_syllables.length;
      return option.value==='all'||option.value==='one'&&count===1
        ||option.value==='two'&&count===2||option.value==='longer'&&count>=3;
    });
    option.hidden=!available;
    option.disabled=!available;
  }
  if($('syllables').selectedOptions?.[0]?.disabled)$('syllables').value='all';
}
function recordingsFor(w){
  return (byWord.get(w.word)||[]).filter(r=>{
    if(r.quiz_eligible===false)return false;
    return Boolean(AudioReview.nativeApproval(audioReviews,w,r))&&hasVerifiedCorrections(w,patternFor(w,r));
  });
}
function nativePlayback(w,r){
  const approval=AudioReview.nativeApproval(audioReviews,w,r);
  return {
    playable:Boolean(audioURL(r)&&approval),
    url:audioURL(r),
    approval,
    source:r?.source||null,
    speaker:r?.speaker||'unknown',
    filename:r?.filename||null,
  };
}
function hasAlignedCorrections(w){
  const tones=(expectedPattern(w)||'').split('-').filter(Boolean);
  return Array.isArray(w.pinyin_syllables) && w.pinyin_syllables.length===tones.length;
}
function hasVerifiedCorrections(w,pattern=expectedPattern(w)){
  const tones=(pattern||'').split('-');
  return (w.pinyin_syllables||[]).every((pinyin,index)=>
    tones[index]==='N'||Boolean(correctionSelection(correctionKey(pinyin,tones[index])))
  );
}
function practiceWords(){return words.filter(w=>recordingsFor(w).length&&hasAlignedCorrections(w))}
function filtered(eligible=practiceWords()){return eligible.filter(w=>{
  const syllables=$('syllables').value; const count=(w.lexical_tones||[]).length;
  if(syllables==='one' && count!==1)return false;
  if(syllables==='two' && count!==2)return false;
  if(syllables==='longer' && count<3)return false;
  if($('sandhiOnly').checked && !w.sandhi_tags.length)return false;
  return true;
})}
function choose(a){return a[Math.floor(Math.random()*a.length)]}
function patternFor(w,r){return (r&&r.surface_pattern)||expectedPattern(w)}
function renderToneChoices(){
  const syllables=current._correct.split('-'), tones=['1','2','3','4','N'];
  const toneLabels={1:'1st tone',2:'2nd tone',3:'3rd tone',4:'4th tone',N:'Neutral'};
  selectedTones=Array(syllables.length).fill(null); $('answers').innerHTML=''; $('answers').className='answers tone-columns';
  syllables.forEach((_,index)=>{
    const column=document.createElement('div'); column.className='tone-column';
    const heading=document.createElement('div'); heading.className='tone-heading'; heading.textContent=`Syllable ${index+1}`; column.appendChild(heading);
    tones.forEach(tone=>{const button=document.createElement('button'); button.className='tone-choice'; button.dataset.tone=tone; button.innerHTML=`${toneLabels[tone]} <small>${tone}</small>`; button.onclick=()=>{
      playCorrection(index,tone);
      if(current._graded)return;
      selectedTones[index]=tone; [...column.querySelectorAll('button')].forEach(x=>x.classList.remove('selected')); button.classList.add('selected');
      if(selectedTones.every(Boolean))grade(selectedTones.join('-'),current._correct);
    }; column.appendChild(button)});
    $('answers').appendChild(column);
  });
}
function currentSnapshot(){
  if(!current||!questionVerified)return null;
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
async function verifyCurrentQuestion(){
  const loadId=++questionLoadId;
  questionVerified=false;
  $('answers').innerHTML='';
  $('play').disabled=true;
  $('record').disabled=true;
  try{
    if(!hasVerifiedCorrections(current,current._correct))throw new Error('A correct-tone reference is unavailable');
    const approvals=[currentNative?.approval];
    for(const pinyin of current.pinyin_syllables){
      for(const tone of ['1','2','3','4']){
        const selected=correctionSelection(correctionKey(pinyin,tone));
        if(selected)approvals.push(selected.approval);
      }
    }
    await Promise.all(approvals.map(approvedAudioBytes));
    if(loadId!==questionLoadId)return false;
    questionVerified=true;
    $('play').disabled=false;
    $('record').disabled=!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined';
    return true;
  }catch(error){
    if(loadId!==questionLoadId)return false;
    $('prompt').textContent='This recording could not be loaded. Try another word.';
    setAudioStatus(error.message,true);
    console.error('Question audio verification failed',error);
    return false;
  }
}
async function back(play=false){
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
  $('reveal').classList.add('hidden');
  if(!await verifyCurrentQuestion())return;
  $('prompt').innerHTML=state.promptHTML;
  renderToneChoices();
  restoreToneState(state);
  $('reveal').innerHTML=state.revealHTML;
  $('reveal').classList.toggle('hidden',state.revealHidden);
  updateBackButton();
  if(currentNative?.playable&&play)playNative();
}
async function next(play=false,remember=true){
  if(remember){
    const snapshot=currentSnapshot();
    if(snapshot){
      quizHistory.push(snapshot);
      while(quizHistory.length>QUIZ_HISTORY_LIMIT)quizHistory.shift();
    }
  }
  stopAllAudio();
  questionLoadId++;
  questionVerified=false;
  clearPersonalRecording();
  setAudioStatus();
  const eligible=practiceWords();
  const pool=filtered(eligible);
  if(!pool.length){
    current=null; currentRec=null; currentNative=null;
    $('prompt').innerHTML=eligible.length
      ?'<div class="muted">No exercises match these filters.</div><p>Try another syllable setting or turn off Sandhi only.</p>'
      :'<div class="muted">No exercises are available right now.</div>';
    $('play').disabled=true; $('record').disabled=true;
    $('answers').innerHTML=''; $('reveal').classList.add('hidden'); updateBackButton(); return;
  }
  current=choose(pool); const rs=recordingsFor(current); currentRec=rs.length?choose(rs):null; currentNative=nativePlayback(current,currentRec);
  current._graded=false;
  const correct=patternFor(current,currentRec);
  current._correct=correct;
  $('prompt').innerHTML=`<div class="muted">Listen first — word hidden until you answer</div>${currentNative?.playable?`<div class="muted">Speaker: ${escapeHTML(currentNative.speaker)} · ${escapeHTML(sourceName(currentNative.source||''))}</div>`:'<div class="muted">No local recording for this item.</div>'}`;
  $('reveal').classList.add('hidden');
  if(!await verifyCurrentQuestion())return;
  renderToneChoices();
  updateBackButton();
  if(currentNative?.playable && play)playNative();
}
function grade(p,correct){
  if(!questionVerified||correct!==patternFor(current,currentRec)||!AudioReview.nativeApproval(audioReviews,current,currentRec)||!hasVerifiedCorrections(current,correct)){
    setAudioStatus('This exercise is not ready yet. Try another word.',true);
    return;
  }
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
  const heard=CorrectionAudio.spokenPinyin(current.pinyin_syllables,correct);
  const compact=pinyin=>pinyin.toLowerCase().normalize('NFC').replace(/[\s'’\-]/g,'');
  const listed=compact(current.pinyin)!==compact(heard)
    ?`<p class="muted">Listed pinyin: ${escapeHTML(current.pinyin)}. This recording uses the spoken form shown above.</p>`:'';
  $('reveal').innerHTML=`<div class="word">${escapeHTML(current.word)}</div><div class="muted">Heard here</div><div class="pinyin">${escapeHTML(heard)}</div><p>Correct tone pattern: <b>${escapeHTML(correct)}</b></p>${listed}${definition}<div>${tags}</div>${current.surface_label_needs_clip_review?'<p class="muted">This word may vary with prosodic grouping.</p>':''}`;
  $('reveal').classList.remove('hidden');
  const reference=document.createElement('a');
  reference.href=`https://mandarin-native.com/#${encodeURIComponent(`word/${current.word}`)}`;
  reference.target='_blank';
  reference.rel='noreferrer noopener';
  reference.textContent='Compare in Mandarin Native (online, independent reference)';
  $('reveal').appendChild(reference);
  if(window.matchMedia('(max-width: 680px)').matches){
    $('reveal').scrollIntoView({
      behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',
      block:'nearest',
    });
  }
}
function scrollToPractice(){
  if(!window.matchMedia('(max-width: 680px)').matches)return;
  document.querySelector('.card').scrollIntoView({
    behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',
    block:'start',
  });
}
function audioURL(r){if(!r)return null;let p=r.audio_path||''; if(p.startsWith('audio/'))return '../'+p; return p}
function stopNative(){
  nativePlayId++;
  if(nativeObjectURL){URL.revokeObjectURL(nativeObjectURL);nativeObjectURL=null}
  if(!nativeAudio)return;
  nativeAudio.pause();
  nativeAudio.currentTime=0;
  nativeAudio=null;
}
function stopPersonalAudio(){
  overlayPlayId++;
  if(overlayObjectURL){URL.revokeObjectURL(overlayObjectURL);overlayObjectURL=null}
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
async function approvedAudioBytes(approval){
  if(!approval)throw new Error('No qualifying assessment for this audio');
  const key=`${approval.audio_path}:${approval.sha256}`;
  const cached=cachedValue(reviewedAudioBytes,key);
  if(cached)return cached;
  const promise=(async()=>{
    const response=await fetch(`../${approval.audio_path}`,{cache:'no-store'});
    if(!response.ok)throw new Error(`Reviewed audio failed: HTTP ${response.status}`);
    const bytes=await response.arrayBuffer();
    await AudioReview.verifyBytes(bytes,approval);
    return bytes;
  })();
  cacheValue(reviewedAudioBytes,key,promise,RAW_BUFFER_CACHE_LIMIT);
  try{return await promise}catch(error){
    if(reviewedAudioBytes.get(key)===promise)reviewedAudioBytes.delete(key);
    throw error;
  }
}
async function playNative(){
  if(!questionVerified||!currentNative?.playable)return;
  return playAssessedRecording(currentNative.approval,'Playing the native recording.');
}
async function playAssessedRecording(approval,message){
  stopCorrection();
  stopNative();
  stopPersonalAudio();
  const playId=nativePlayId;
  try{
    const bytes=await approvedAudioBytes(approval);
    if(playId!==nativePlayId)return;
    nativeObjectURL=URL.createObjectURL(new Blob([bytes]));
    const audio=new Audio(nativeObjectURL);
    nativeAudio=audio;
    audio.onended=()=>{if(nativeAudio===audio)stopNative()};
    await audio.play();
    if(playId===nativePlayId)setAudioStatus(message);
  }catch(error){
    if(playId!==nativePlayId)return;
    stopNative();
    if(isPlaybackInterruption(error))return;
    if(error.name==='NotAllowedError'){
      setAudioStatus('Tap an audio button to start playback.');
      return;
    }
    setAudioStatus(`Native playback blocked: ${error.message}`,true);
    console.error('Native audio failed',error);
  }
}
function correctionKey(pinyin,tone){return CorrectionAudio.correctionKey(pinyin,tone)}
function correctionSelection(key){
  const selected=AudioReview.correctionSelection(CorrectionAudio,key,correctionQuality,correctionRecordings,audioReviews,$('correctionSource')?.value||'pinyin_public');
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
    const bytes=await approvedAudioBytes(selected.approval);
    const context=getCorrectionContext();
    return context.decodeAudioData(bytes.slice(0));
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
      const normalization=CorrectionAudio.normalizationParameters(sourceChannels);
      for(let channel=0;channel<channels;channel++){
        const sourceChannel=Math.min(channel,buffer.numberOfChannels-1);
        const input=buffer.getChannelData(sourceChannel);
        const output=combined.getChannelData(channel);
        const dcOffset=normalization.offsets[sourceChannel];
        for(let index=0;index<input.length;index++)output[offset+index]=(input[index]-dcOffset)*normalization.gain;
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
    const selection=correctionSelection(keys[0]);
    setAudioStatus(keys.length===1
      ?`Playing tone ${keys[0].slice(-1)} (${sourceName(selection.source)}).`
      :`Playing screened tone sequence ${keys.map(key=>key.slice(-1)).join('-')}.`);
  }catch(error){
    if(playId===correctionPlayId){
      setAudioStatus(`Comparison playback blocked: ${error.message}`,true);
      console.error(`Pinyin audio failed for ${keys.join('+')}`,error);
    }
  }
}
async function playCorrection(index,tone){
  if(!questionVerified)return;
  const pinyin=current.pinyin_syllables?.[index];
  if(tone==='N'){
    stopAllAudio();
    const reference=pinyin&&AudioReview.neutralSelection(audioReviews,pinyin);
    if(reference){
      const position=reference.pinyin_syllables.findIndex((base,index)=>
        base.replace(/ü/g,'v')===pinyin.replace(/ü/g,'v')&&reference.surface_pattern.split('-')[index]==='N');
      return playAssessedRecording(reference,`Playing a whole-word example; listen for neutral tone on syllable ${position+1}.`);
    }
    setAudioStatus('Neutral tone is context-dependent and has no standalone comparison clip.');
    return;
  }
  if(!pinyin){
    stopAllAudio();
    setAudioStatus('No comparison recording is available for this syllable.',true);
    return;
  }
  const key=correctionKey(pinyin,tone);
  if(!correctionSelection(key)){
    stopAllAudio();
    setAudioStatus('No comparison recording is available for this tone; your answer still counts.');
    return;
  }
  return playPinyinKey(key);
}
$('play').onclick=playNative;
$('back').onclick=()=>{back(true);scrollToPractice()};
$('next').onclick=()=>{next(true);scrollToPractice()};
$('syllables').onchange=()=>{quizHistory=[];next(true,false)};
$('correctionSource').onchange=async()=>{
  const state=currentSnapshot();
  stopAllAudio();
  rawPinyinBuffers.clear();
  correctionBuffers.clear();
  if(state){
    if(!await verifyCurrentQuestion())return;
    renderToneChoices();
    restoreToneState(state);
  }else{
    await next(false,false);
  }
  setAudioStatus(`Comparison voice: ${$('correctionSource').value==='audio_cmn'?'human':'reference'}.`);
};
$('sandhiOnly').onchange=()=>{quizHistory=[];next(true,false)};
$('resetProgress').onclick=()=>{if(confirm('Clear all saved tone-practice results?')){results=[];saveResults()}};
$('record').onclick=async()=>{
  if(recordingStarting)return;
  if(!questionVerified)return;
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
  if(!questionVerified||!currentNative?.playable||!mineUrl)return;
  stopAllAudio();
  const playId=overlayPlayId;
  try{
    const bytes=await approvedAudioBytes(currentNative.approval);
    if(playId!==overlayPlayId||!mineUrl)return;
    overlayObjectURL=URL.createObjectURL(new Blob([bytes]));
    const native=new Audio(overlayObjectURL),mine=new Audio(mineUrl);
    overlayAudios=[native,mine];
    const finished=()=>{if(overlayAudios.includes(native)&&native.ended&&mine.ended)stopPersonalAudio()};
    native.onended=finished;
    mine.onended=finished;
    await Promise.all([native.play(),mine.play()]);
    if(playId===overlayPlayId)setAudioStatus('Playing the native and recorded audio together.');
  }catch(error){
    if(playId!==overlayPlayId)return;
    stopPersonalAudio();
    if(isPlaybackInterruption(error))return;
    setAudioStatus(`Overlay playback blocked: ${error.message}`,true);
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
  setPracticeControlsDisabled(true);
  $('record').disabled=true;
});
