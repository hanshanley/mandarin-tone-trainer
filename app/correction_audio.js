(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.CorrectionAudio=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const unavailableAudioCmnKeys=new Set(['r1','r2','r3','r4']);
  function correctionKey(pinyin,tone){
    return pinyin.toLowerCase().replace(/ü/g,'v')+tone;
  }
  function correctionSelection(key,quality,recordings){
    const audioCmnReview=quality.audio_cmn?.[key]||quality[key];
    const publicReview=quality.pinyin_public?.[key];
    const preferredSource=quality.preferred_sources?.[key];
    const audioCmnUnavailable=unavailableAudioCmnKeys.has(key);
    const audioCmnBad=audioCmnReview?.status==='bad';
    if(audioCmnReview?.status==='bad'&&audioCmnReview.replacement===null)return null;
    const publicRecording=recordings[key];
    const publicAvailable=publicRecording?.audio_path&&publicReview?.status!=='bad';
    if(preferredSource==='pinyin_public'&&publicAvailable)return {
      audio_path:publicRecording.audio_path,
      enhanced:true,
      source:publicRecording.source,
    };
    if(!audioCmnUnavailable&&!audioCmnBad){
      const audioCmnKey=key==='ju4'?'jv4':key;
      return {
        audio_path:`audio/audio_cmn/syllabs/cmn-${audioCmnKey}.mp3`,
        enhanced:false,
        source:'audio_cmn',
      };
    }
    if((audioCmnReview?.replacement==='pinyin_public'||audioCmnUnavailable)&&publicAvailable)return {
      audio_path:publicRecording.audio_path,
      enhanced:true,
      source:publicRecording.source,
    };
    return null;
  }
  return {correctionKey,correctionSelection};
});
