(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.AudioReview=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const nonempty=value=>typeof value==='string'&&value.trim().length>0;
  function nativeCandidates(words,recordings){
    const byId=new Map(words.map(word=>[word.id,word]));
    const byText=new Map();
    for(const word of words){
      if(!byText.has(word.word))byText.set(word.word,[]);
      byText.get(word.word).push(word);
    }
    const candidates=[];
    for(const recording of recordings){
      if(!['audio_cmn','mandarin_native'].includes(recording.source)||(recording.language_code||'zh')!=='zh')continue;
      if(recording.source==='mandarin_native'&&recording.recording_type!=='word_candidate')continue;
      const targets=Array.isArray(recording.candidate_hsk_ids)
        ?[...new Set(recording.candidate_hsk_ids)].map(id=>byId.get(id)).filter(Boolean)
        :byText.get(recording.word)||[];
      for(const word of targets){
        if(!recording.hsk_id||recording.hsk_id===word.id)candidates.push({word,recording});
      }
    }
    return candidates;
  }
  function nativeBlockReason(recording,assessment=null){
    if(recording.review_status==='rejected')return recording.notes||'Explicitly rejected recording';
    if((recording.language_code||'zh')!=='zh')return 'Recording is not indexed as Mandarin';
    if(recording.source==='mandarin_native'&&recording.recording_type!=='word_candidate'){
      return 'Contextual or unidentified imported audio is not an isolated-word quiz prompt';
    }
    const localScreen=assessment?.assessment==='automated'&&assessment.distribution_scope==='local_only';
    if(recording.source==='mandarin_native'&&!localScreen&&(recording.rights_status!=='cleared'||!nonempty(recording.license))){
      return 'Mandarin Native recording has no verified reuse permission or license';
    }
    return recording.quiz_eligible===false?recording.notes||'Excluded native recording':null;
  }
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
    const automated=entry.assessment==='automated';
    if(entry.status!==(automated?'screened':'approved')||!/^[a-f0-9]{64}$/.test(entry.sha256||'')){
      throw new Error(`Missing approval or SHA-256: ${entry.audio_path}`);
    }
    if((!nonempty(entry.license)&&!(automated&&entry.distribution_scope==='local_only'))||!/^https?:\/\/\S+$/.test(entry.source_url||'')){
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
    if(automated){
      const evidence=entry.evidence;
      const expectedBases=entry.kind==='comparison'?[entry.key.slice(0,-1)]:entry.pinyin_syllables.map(base=>base.replace(/ü/g,'v'));
      const tones=entry.kind==='comparison'?[entry.key.slice(-1)]:entry.surface_pattern.split('-');
      if(!['local_only','redistributable'].includes(entry.distribution_scope)
        ||tones.some(tone=>!['1','2','3','4'].includes(tone))
        ||evidence?.method!=='spectral-consensus-1'
        ||!/^[a-f0-9]{64}$/.test(evidence.pipeline_sha256||'')
        ||evidence.audio_sha256!==entry.sha256||evidence.label_identity!==identity(entry)
        ||evidence.identity_method!=='unprompted_paraformer'
        ||JSON.stringify(evidence.recognized_bases)!==JSON.stringify(expectedBases)
        ||!Number.isFinite(evidence.register_hz)||evidence.register_hz<=0
        ||!Array.isArray(evidence.tones)||evidence.tones.length!==tones.length){
        throw new Error(`Invalid automated acoustic evidence: ${entry.audio_path}`);
      }
      if(evidence.identity_encoding==='literal_pinyin'){
        const spelling=(evidence.asr_transcript||'').trim().toLowerCase().replace(/ü/g,'v');
        if(!/^[a-zv]+(?:[ '\-]+[a-zv]+)*$/.test(spelling)||spelling.replace(/[ '\-]/g,'')!==expectedBases.join('')){
          throw new Error(`ASR spelling does not match the syllables: ${entry.audio_path}`);
        }
      }
      evidence.tones.forEach((decision,index)=>{
        const votes=decision?.votes;
        const values=votes&&Object.values(votes).filter(value=>value!==null);
        if(decision?.status!=='screened'||decision.expected!==tones[index]
          ||!votes||Object.keys(votes).some(method=>!['pyin','praat','world'].includes(method))
          ||values.length<2||values.some(value=>value!==tones[index])
          ||!Number.isFinite(decision.start)||!Number.isFinite(decision.end)
          ||decision.start<0||decision.end<=decision.start
          ||!Number.isFinite(decision.voiced_seconds)||decision.voiced_seconds<.12
          ||decision.voiced_seconds>decision.end-decision.start+.021){
          throw new Error(`Conflicting or incomplete tone evidence: ${entry.audio_path}`);
        }
      });
      if(!Array.isArray(evidence.comparison_support)
        ||(entry.kind==='native'&&evidence.comparison_support.length!==tones.length)){
        throw new Error(`Missing comparison support: ${entry.audio_path}`);
      }
      return;
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
  function createIndex(ledger,acousticLedger=null,{allowLocalOnly=true,sourceRecordings=[]}={}){
    if(ledger?.version!==1||!Array.isArray(ledger.approvals))throw new Error('Invalid audio review ledger');
    const index=new Map();
    for(const entry of ledger.approvals){
      if(entry.assessment==='automated')throw new Error('Automated results must use the acoustic ledger, not human attestations');
      validateApproval(entry);
      const key=identity(entry);
      if(index.has(key))throw new Error(`Duplicate audio approval: ${entry.audio_path}`);
      index.set(key,entry);
    }
    if(acousticLedger!==null){
      if(acousticLedger.version!==1||acousticLedger.method!=='spectral-consensus-1'
        ||acousticLedger.certifies_accuracy!==false||!Array.isArray(acousticLedger.approvals)){
        throw new Error('Invalid acoustic screening ledger');
      }
      const seen=new Set();
      for(const entry of acousticLedger.approvals){
        if(entry.assessment!=='automated')throw new Error('Acoustic ledger contains a non-automated entry');
        validateApproval(entry);
        if(entry.evidence.pipeline_sha256!==acousticLedger.pipeline_sha256)throw new Error('Acoustic pipeline fingerprint mismatch');
        const key=identity(entry);
        if(seen.has(key))throw new Error(`Duplicate acoustic decision: ${entry.audio_path}`);
        seen.add(key);
        if(!allowLocalOnly&&entry.distribution_scope==='local_only')continue;
        if(!index.has(key))index.set(key,entry);
      }
      for(const entry of index.values()){
        if(entry.assessment!=='automated'||entry.kind!=='native')continue;
        entry.evidence.comparison_support.forEach((support,position)=>{
          const expected=entry.pinyin_syllables[position].replace(/ü/g,'v')+entry.surface_pattern.split('-')[position];
          const comparison=index.get(identity(comparisonDescriptor(support.key,support)));
          if(support.key!==expected||!comparison||comparison.sha256!==support.sha256||support.sha256===entry.sha256){
            throw new Error(`Stale or missing independent tone reference: ${entry.audio_path}`);
          }
        });
      }
    }
    const alternatives=new Map();
    for(const entry of index.values()){
      if(entry.kind!=='comparison')continue;
      if(!alternatives.has(entry.key))alternatives.set(entry.key,[]);
      alternatives.get(entry.key).push(entry);
    }
    index.comparisonAlternatives=alternatives;
    index.sourceRecordings=new Map(sourceRecordings.map(recording=>[recording.audio_path,recording]));
    return index;
  }
  function nativeApproval(index,word,recording){
    if(!recording)return null;
    const assessment=index.get(identity(nativeDescriptor(word,recording)))||null;
    if(nativeBlockReason(recording,assessment))return null;
    if(recording.hsk_id&&recording.hsk_id!==word.id)return null;
    if(Array.isArray(recording.candidate_hsk_ids)&&!recording.candidate_hsk_ids.includes(word.id))return null;
    return assessment;
  }
  function comparisonApproval(index,key,recording){
    return index.get(identity(comparisonDescriptor(key,recording)))||null;
  }
  function correctionSelection(policy,key,quality,recordings,index,preferredSource='pinyin_public'){
    const allowed=approval=>{
      const recording=index.sourceRecordings?.get(approval.audio_path);
      return !recording||!nativeBlockReason(recording,approval);
    };
    const alternate=preferredSource==='audio_cmn'?'pinyin_public':'audio_cmn';
    for(const source of [preferredSource,alternate]){
      const selected=policy.correctionSelection(key,quality,recordings,source);
      if(!selected)continue;
      if(selected.source==='mandarin_native'||selected.audio_path.startsWith('audio/mandarin_native/'))continue;
      const approval=comparisonApproval(index,key,selected);
      if(approval&&allowed(approval))return {...selected,approval};
    }
    const excluded=new Set([
      recordings[key]?.audio_path,
      `audio/audio_cmn/syllabs/cmn-${key==='ju4'?'jv4':key}.mp3`,
    ]);
    for(const approval of index.comparisonAlternatives?.get(key)||[]){
      if(!excluded.has(approval.audio_path)&&approval.audio_path.startsWith('audio/audio_cmn/')&&allowed(approval)){
        return {audio_path:approval.audio_path,source:'audio_cmn',enhanced:false,approval};
      }
    }
    return null;
  }
  async function verifyBytes(bytes,approval){
    if(!approval)throw new Error('Audio has no qualifying assessment');
    validateApproval(approval);
    if(!globalThis.crypto?.subtle)throw new Error('Audio verification requires a secure browser context');
    const digest=await globalThis.crypto.subtle.digest('SHA-256',bytes);
    const actual=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
    if(actual!==approval.sha256)throw new Error(`Audio changed since ${approval.assessment==='automated'?'acoustic screening':'listening review'}: ${approval.audio_path}`);
  }
  return {
    nativeCandidates,nativeBlockReason,nativeDescriptor,comparisonDescriptor,identity,validateApproval,createIndex,
    nativeApproval,comparisonApproval,correctionSelection,verifyBytes,
  };
});
