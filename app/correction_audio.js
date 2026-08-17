(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.CorrectionAudio=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const unavailableAudioCmnKeys=new Set(['r1','r2','r3','r4']);
  const NORMALIZATION_TARGET_RMS=.16, NORMALIZATION_ACTIVITY_THRESHOLD=.01, NORMALIZATION_MAX_PEAK=.90, NORMALIZATION_MAX_BOOST=12;
  function correctionKey(pinyin,tone){
    return pinyin.toLowerCase().replace(/ü/g,'v')+tone;
  }
  function normalizationGain(channels){
    let activeEnergy=0,activeCount=0,peak=0;
    for(const channel of channels){
      for(const sample of channel){
        const magnitude=Math.abs(sample);
        peak=Math.max(peak,magnitude);
        if(magnitude>=NORMALIZATION_ACTIVITY_THRESHOLD){
          activeEnergy+=sample*sample;
          activeCount++;
        }
      }
    }
    if(!activeCount||!peak)return 1;
    const rms=Math.sqrt(activeEnergy/activeCount);
    return Math.min(NORMALIZATION_TARGET_RMS/rms,NORMALIZATION_MAX_PEAK/peak,NORMALIZATION_MAX_BOOST);
  }
  function correctionSelection(key,quality,recordings,preferredSource='pinyin_public'){
    const audioCmnReview=quality.audio_cmn?.[key]||quality[key];
    const publicReview=quality.pinyin_public?.[key];
    const audioCmnUnavailable=unavailableAudioCmnKeys.has(key);
    const audioCmnBad=audioCmnReview?.status==='bad';
    if(audioCmnReview?.status==='bad'&&audioCmnReview.replacement===null)return null;
    const publicRecording=recordings[key];
    const publicAvailable=publicRecording?.audio_path&&publicReview?.status!=='bad';
    const publicSelection=()=>({
      audio_path:publicRecording.audio_path,
      enhanced:true,
      source:publicRecording.source,
    });
    const replacementSelection=()=>({
      audio_path:audioCmnReview.replacement_audio_path,
      enhanced:false,
      source:audioCmnReview.replacement_source||'audio_cmn',
    });
    const audioCmnSelection=()=>{
      const audioCmnKey=key==='ju4'?'jv4':key;
      return {
        audio_path:`audio/audio_cmn/syllabs/cmn-${audioCmnKey}.mp3`,
        enhanced:false,
        source:'audio_cmn',
      };
    };
    if(preferredSource==='audio_cmn'){
      if(audioCmnBad){
        if(audioCmnReview.replacement_audio_path)return replacementSelection();
        if(audioCmnReview.replacement==='pinyin_public'&&publicAvailable)return publicSelection();
        return null;
      }
      if(!audioCmnUnavailable)return audioCmnSelection();
      return publicAvailable?publicSelection():null;
    }
    if(publicAvailable)return publicSelection();
    if(audioCmnBad){
      if(audioCmnReview.replacement_audio_path)return replacementSelection();
      return null;
    }
    return audioCmnUnavailable?null:audioCmnSelection();
  }
  return {correctionKey,correctionSelection,normalizationGain};
});
