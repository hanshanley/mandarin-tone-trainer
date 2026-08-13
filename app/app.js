let words=[], recordings=[], byWord=new Map(), current=null, currentRec=null, mediaRecorder=null, chunks=[], mineUrl=null, selectedTones=[];
let results=[];
const $=id=>document.getElementById(id);
function saveResults(){updateProgress()}
function updateProgress(){
  const correct=results.filter(r=>r.correct).length;
  $('progress').textContent=`${results.length} attempts · ${correct} correct${results.length?` (${Math.round(correct/results.length*100)}%)`:''}`;
}
function sourceName(source){return source==='audio_cmn'?'audio-cmn':source}
function rebuildIndex(){
  byWord=new Map();
  const selected=recordings.filter(r=>r.source==='audio_cmn' && (r.language_code||'zh')==='zh');
  for(const r of selected){if(!byWord.has(r.word))byWord.set(r.word,[]);byWord.get(r.word).push(r)}
  $('audioStatus').textContent=selected.length?`audio-cmn: ${selected.length.toLocaleString()} recordings across ${byWord.size.toLocaleString()} words.`:'No audio-cmn recordings found.';
}
async function load(){
  const wordResponse=await fetch('../data/hsk_words.json');
  if(!wordResponse.ok)throw new Error(`HSK data failed: HTTP ${wordResponse.status}`);
  words=await wordResponse.json();
  const recordingResponse=await fetch('../data/recordings.json');
  if(!recordingResponse.ok)throw new Error(`recordings failed: HTTP ${recordingResponse.status}`);
  recordings=await recordingResponse.json();
  $('progress').textContent='';
  rebuildIndex();
}
function filtered(){return words.filter(w=>{
  const syllables=$('syllables').value; const count=(w.lexical_tones||[]).length;
  if(syllables==='one' && count!==1)return false;
  if(syllables==='multi' && count<2)return false;
  if($('sandhiOnly').checked && !w.sandhi_tags.length)return false;
  if(!byWord.has(w.word))return false;
  return true;
})}
function choose(a){return a[Math.floor(Math.random()*a.length)]}
function patternFor(w,r){
  return (r&&r.surface_pattern)||w.default_surface_pattern||w.lexical_pattern;
}
function renderToneChoices(){
  const syllables=[...current.word], tones=['1','2','3','4','N'];
  selectedTones=Array(syllables.length).fill(null); $('answers').innerHTML=''; $('answers').className='answers tone-columns';
  syllables.forEach((syllable,index)=>{
    const column=document.createElement('div'); column.className='tone-column';
    const heading=document.createElement('div'); heading.className='tone-heading'; heading.textContent=`Syllable ${index+1}`; column.appendChild(heading);
    tones.forEach(tone=>{const button=document.createElement('button'); button.className='tone-choice'; button.innerHTML=`${tone==='N'?'Neutral':`${tone}th tone`} <small>${tone}</small>`; button.onclick=()=>{
      playCorrection(index,tone);
      if(current._graded)return;
      selectedTones[index]=tone; [...column.querySelectorAll('button')].forEach(x=>x.classList.remove('selected')); button.classList.add('selected');
      if(selectedTones.every(Boolean))grade(button,selectedTones.join('-'),current._correct);
    }; column.appendChild(button)});
    $('answers').appendChild(column);
  });
}
function next(play=false){
  const pool=filtered();
  if(!pool.length){
    current=null; currentRec=null; $('prompt').innerHTML='<div class="muted">No words match the current filters.</div><p>Try another syllable-count setting or turn off Sandhi only.</p>';
    $('answers').innerHTML=''; return;
  }
  current=choose(pool); const rs=byWord.get(current.word)||[]; currentRec=rs.length?choose(rs):null;
  current._graded=false;
  const correct=patternFor(current,currentRec);
  current._correct=correct;
  $('prompt').innerHTML=`<div class="muted">Listen first — word hidden until you answer</div>${currentRec?`<div class="muted">Speaker: ${currentRec.speaker||'unknown'} · ${currentRec.source||''}</div>`:'<div class="muted">No local recording for this item.</div>'}`;
  $('reveal').classList.add('hidden'); renderToneChoices();
  if(currentRec && play)playNative();
}
function grade(btn,p,correct){
  current._graded=true; btn.classList.add(p===correct?'correct':'wrong');
  if(p!==correct)selectedTones.forEach((tone,index)=>{if(tone===correct.split('-')[index])$('answers').children[index].querySelector('.selected').classList.add('correct')});
  if(p!==correct){const wrong=selectedTones.findIndex((tone,index)=>tone!==correct.split('-')[index]); if(wrong>=0)playCorrection(wrong,selectedTones[wrong]);}
  results.push({timestamp:new Date().toISOString(),word:current.word,pinyin:current.pinyin,selected_pattern:p,correct_pattern:correct,correct:p===correct,source:'audio_cmn',recording:currentRec?.filename||null});
  saveResults();
  const tags=current.sandhi_tags.map(x=>`<span class="tag">${x}</span>`).join('');
  $('reveal').innerHTML=`<div class="word">${current.word}</div><div class="pinyin">${current.pinyin}</div><p>Correct tone pattern: <b>${correct}</b></p><p class="muted">Press a tone button again to replay that tone.</p><div>${tags}</div>${current.surface_label_needs_clip_review?'<p class="muted">This word may vary with prosodic grouping.</p>':''}`;
  $('reveal').classList.remove('hidden');
}
function audioURL(r){if(!r)return null;let p=r.audio_path||''; if(p.startsWith('audio/'))return '../'+p; return p}
function playNative(){const u=audioURL(currentRec);if(!u)return;new Audio(u).play()}
function playCorrection(index,tone){
  const pinyin=current.pinyin_syllables?.[index]; if(!pinyin||tone==='N')return;
  const key=pinyin.replace(/ü/g,'v')+tone;
  const fallback={zha2:'../audio/audio_cmn/炸/cmn-炸.mp3'};
  new Audio(fallback[key]||`../audio/audio_cmn/syllabs/cmn-${key}.mp3`).play().catch(()=>{});
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
