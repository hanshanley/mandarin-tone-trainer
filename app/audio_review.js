(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.AudioReview=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const nonempty=value=>typeof value==='string'&&value.trim().length>0;
  function nativeDescriptor(word,recording){
    return {
      kind:'native',
      audio_path:recording.audio_path,
      word_id:word.id,
      word:word.word,
      pinyin:word.pinyin,
      pinyin_syllables:word.pinyin_syllables,
      lexical_pattern:word.lexical_pattern,
      surface_pattern:recording.surface_pattern||word.default_surface_pattern||word.lexical_pattern,
    };
  }
  function comparisonDescriptor(key,recording){
    return {kind:'comparison',audio_path:recording.audio_path,key};
  }
  function identity(entry){
    if(entry.kind==='native'){
      return JSON.stringify([
        entry.kind,entry.audio_path,entry.word_id,entry.word,entry.pinyin,
        entry.pinyin_syllables,entry.lexical_pattern,entry.surface_pattern,
      ]);
    }
    return JSON.stringify([entry.kind,entry.audio_path,entry.key]);
  }
  function validateApproval(entry){
    if(!entry||!['native','comparison'].includes(entry.kind))throw new Error('Invalid audio review kind');
    if(!/^audio\/[^?#\\]+$/.test(entry.audio_path||'')||entry.audio_path.split('/').some(part=>!part||part==='.'||part==='..')){
      throw new Error('Audio review must reference a local audio/ file');
    }
    if(entry.status!=='approved'||!/^[a-f0-9]{64}$/.test(entry.sha256||'')){
      throw new Error(`Missing approval or SHA-256: ${entry.audio_path}`);
    }
    if(!nonempty(entry.license)||!/^https?:\/\/\S+$/.test(entry.source_url||'')){
      throw new Error(`Missing provenance: ${entry.audio_path}`);
    }
    if(entry.kind==='comparison'&&!/^[a-zv]+[1-4]$/.test(entry.key||'')){
      throw new Error(`Invalid comparison label: ${entry.audio_path}`);
    }
    if(entry.kind==='native'){
      const syllables=entry.pinyin_syllables;
      if(![entry.word_id,entry.word,entry.pinyin].every(nonempty)
        ||!Array.isArray(syllables)||!syllables.length||!syllables.every(nonempty)
        ||![entry.lexical_pattern,entry.surface_pattern].every(pattern=>
          typeof pattern==='string'&&/^[1-4N](?:-[1-4N])*$/.test(pattern)
          &&pattern.split('-').length===syllables.length)){
        throw new Error(`Invalid native reading labels: ${entry.audio_path}`);
      }
    }
    const reviews=entry.reviews;
    if(!Array.isArray(reviews)||reviews.length<2||reviews.some(review=>
      !review||!nonempty(review.reviewer)||review.method!=='human_listening'
      ||review.verdict!=='approved'||!nonempty(review.reviewed_at)
      ||!Number.isFinite(Date.parse(review.reviewed_at))
      ||review.audio_sha256!==entry.sha256||review.label_identity!==identity(entry)
      ||review.identity_correct!==true||review.tones_correct!==true
      ||review.clear_for_practice!==true)
      ||new Set(reviews.map(review=>review.reviewer.trim().toLowerCase())).size!==reviews.length){
      throw new Error(`Two independent listening approvals required: ${entry.audio_path}`);
    }
  }
  function createIndex(ledger){
    if(ledger?.version!==1||!Array.isArray(ledger.approvals))throw new Error('Invalid audio review ledger');
    const index=new Map();
    for(const entry of ledger.approvals){
      validateApproval(entry);
      const key=identity(entry);
      if(index.has(key))throw new Error(`Duplicate audio approval: ${entry.audio_path}`);
      index.set(key,entry);
    }
    return index;
  }
  function nativeApproval(index,word,recording){
    if(!recording||recording.quiz_eligible===false)return null;
    if(recording.hsk_id&&recording.hsk_id!==word.id)return null;
    return index.get(identity(nativeDescriptor(word,recording)))||null;
  }
  function comparisonApproval(index,key,recording){
    return index.get(identity(comparisonDescriptor(key,recording)))||null;
  }
  function correctionSelection(policy,key,quality,recordings,index,preferredSource='pinyin_public'){
    const alternate=preferredSource==='audio_cmn'?'pinyin_public':'audio_cmn';
    for(const source of [preferredSource,alternate]){
      const selected=policy.correctionSelection(key,quality,recordings,source);
      if(!selected)continue;
      const approval=comparisonApproval(index,key,selected);
      if(approval)return {...selected,approval};
    }
    return null;
  }
  async function verifyBytes(bytes,approval){
    if(!approval)throw new Error('Audio has no listening approval');
    validateApproval(approval);
    if(!globalThis.crypto?.subtle)throw new Error('Audio verification requires a secure browser context');
    const digest=await globalThis.crypto.subtle.digest('SHA-256',bytes);
    const actual=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
    if(actual!==approval.sha256)throw new Error(`Audio changed since listening review: ${approval.audio_path}`);
  }
  return {
    nativeDescriptor,comparisonDescriptor,identity,validateApproval,createIndex,
    nativeApproval,comparisonApproval,correctionSelection,verifyBytes,
  };
});
