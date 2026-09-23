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
  function spokenPinyin(syllables,pattern){
    const tones=typeof pattern==='string'?pattern.split('-'):[];
    if(!Array.isArray(syllables)||!syllables.length||tones.length!==syllables.length){
      throw new Error('Pinyin syllables and spoken tones must align');
    }
    const marks={a:'āáǎà',e:'ēéěè',i:'īíǐì',o:'ōóǒò',u:'ūúǔù','ü':'ǖǘǚǜ'};
    return syllables.map((syllable,index)=>{
      const base=String(syllable).toLowerCase().replace(/v/g,'ü');
      const tone=tones[index];
      if(!/^[a-zü]+$/.test(base)||!['1','2','3','4','N'].includes(tone))throw new Error('Invalid spoken-pinyin input');
      if(tone==='N')return base;
      let position=base.indexOf('a');
      if(position<0)position=base.indexOf('e');
      if(position<0&&base.includes('ou'))position=base.indexOf('o');
      if(position<0){
        for(let i=base.length-1;i>=0;i--){if(marks[base[i]]){position=i;break;}}
      }
      if(position>=0)return base.slice(0,position)+marks[base[position]][Number(tone)-1]+base.slice(position+1);
      if(/^(m|n|ng|hm|hng)$/.test(base)){
        const nasal=base.includes('n')?'n':'m';
        return base.replace(nasal,nasal+['\u0304','\u0301','\u030c','\u0300'][Number(tone)-1]).normalize('NFC');
      }
      throw new Error('This syllable cannot carry a standalone tone mark');
    }).join(' ');
  }
  function normalizationParameters(channels){
    const offsets=channels.map(channel=>{
      let total=0;
      for(const sample of channel)total+=sample;
      return channel.length?total/channel.length:0;
    });
    let activeEnergy=0,activeCount=0,peak=0;
    channels.forEach((channel,channelIndex)=>{
      for(const sample of channel){
        const centered=sample-offsets[channelIndex];
        const magnitude=Math.abs(centered);
        peak=Math.max(peak,magnitude);
        if(magnitude>=NORMALIZATION_ACTIVITY_THRESHOLD){
          activeEnergy+=centered*centered;
          activeCount++;
        }
      }
    });
    if(!activeCount||!peak)return {gain:1,offsets};
    const rms=Math.sqrt(activeEnergy/activeCount);
    return {
      gain:Math.min(NORMALIZATION_TARGET_RMS/rms,NORMALIZATION_MAX_PEAK/peak,NORMALIZATION_MAX_BOOST),
      offsets,
    };
  }
  function normalizationGain(channels){return normalizationParameters(channels).gain}
  function correctionSelection(key,quality,recordings,preferredSource='pinyin_public'){
    const audioCmnReview=quality.audio_cmn?.[key]||quality[key];
    const publicReview=quality.pinyin_public?.[key];
    const audioCmnUnavailable=unavailableAudioCmnKeys.has(key);
    const audioCmnBad=audioCmnReview?.status==='bad';
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
  return {correctionKey,spokenPinyin,correctionSelection,normalizationGain,normalizationParameters};
});
