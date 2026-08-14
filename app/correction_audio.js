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
    if(quality[key]?.status==='bad'){
      const replacement=recordings[key];
      return replacement?.audio_path?{
        audio_path:replacement.audio_path,
        enhanced:true,
        source:replacement.source,
      }:null;
    }
    if(unavailableAudioCmnKeys.has(key))return null;
    const audioCmnKey=key==='ju4'?'jv4':key;
    return {
      audio_path:`audio/audio_cmn/syllabs/cmn-${audioCmnKey}.mp3`,
      enhanced:false,
      source:'audio_cmn',
    };
  }
  return {correctionKey,correctionSelection};
});
