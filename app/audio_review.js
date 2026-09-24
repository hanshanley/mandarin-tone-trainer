(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.AudioReview=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const nonempty=value=>typeof value==='string'&&value.trim().length>0;
  function isSentenceDerived(recording){
    return Boolean(recording?.source_segment||recording?.recording_type==='aligned_word'
      ||recording?.recording_type==='context_sentence'
      ||recording?.audio_path?.startsWith('audio/mandarin_native/excerpts/')
      ||recording?.audio_path?.startsWith('audio/mandarin_native/context/'));
  }
  function validSourceSegment(segment){
    return segment&&/^audio\/mandarin_native\/context\/[a-f0-9]{64}\.(mp3|m4a)$/.test(segment.audio_path)
      &&/^[a-f0-9]{64}$/.test(segment.sha256||'')
      &&segment.sample_rate===16000
      &&Number.isInteger(segment.start_sample)&&Number.isInteger(segment.end_sample)
      &&segment.start_sample>=0&&segment.end_sample>segment.start_sample;
  }
  function nativeCandidates(words,recordings){
    const byId=new Map(words.map(word=>[word.id,word]));
    const byText=new Map();
    for(const word of words){
      if(!byText.has(word.word))byText.set(word.word,[]);
      byText.get(word.word).push(word);
    }
    const candidates=[];
    for(const recording of recordings){
      if(isSentenceDerived(recording))continue;
      if(!['audio_cmn','mandarin_native'].includes(recording.source)||(recording.language_code||'zh')!=='zh')continue;
      if(recording.source==='mandarin_native'&&!['word_candidate','aligned_word'].includes(recording.recording_type))continue;
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
    if(isSentenceDerived(recording)||isSentenceDerived(assessment))return 'Sentence-extracted audio is not used for tone practice';
    if(recording.review_status==='rejected')return recording.notes||'Explicitly rejected recording';
    if((recording.language_code||'zh')!=='zh')return 'Recording is not indexed as Mandarin';
    if(recording.source==='mandarin_native'&&!['word_candidate','aligned_word'].includes(recording.recording_type)){
      return 'Contextual or unidentified imported audio is not an isolated-word quiz prompt';
    }
    if(recording.recording_type==='aligned_word'&&(!validSourceSegment(recording.source_segment)
      ||recording.alignment_method!=='exact-unprompted-transcript-fa-zh-1')){
      return 'Word excerpt lacks valid source alignment';
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
      ...(recording.source_segment?{source_segment:recording.source_segment}:{}),
    };
  }
  function comparisonDescriptor(key,recording){
    return {kind:'comparison',audio_path:recording.audio_path,key};
  }
  function sourceForPath(path){
    if(path.startsWith('audio/audio_cmn/'))return 'audio_cmn';
    if(path.startsWith('audio/pinyin_public/'))return 'pinyin_public';
    if(path.startsWith('audio/mandarin_native/')&&!isSentenceDerived({audio_path:path}))return 'mandarin_native';
    return null;
  }
  function validateCrossSourceWord(entry){
    const evidence=entry.evidence,reference=evidence.whole_word_reference;
    const bases=entry.pinyin_syllables?.map(base=>base.replace(/ü/g,'v'));
    if(entry.kind!=='native'||bases?.length!==2||entry.surface_pattern.includes('N')
      ||isSentenceDerived(entry)||sourceForPath(entry.audio_path)!=='mandarin_native'||entry.distribution_scope!=='local_only'
      ||evidence.audio_sha256!==entry.sha256||evidence.label_identity!==identity(entry)
      ||evidence.supplied_pattern!==entry.surface_pattern||evidence.blind_pattern!==entry.surface_pattern
      ||!Number.isFinite(evidence.minimum_probability)||evidence.minimum_probability<.95||evidence.minimum_probability>1
      ||!Number.isFinite(evidence.minimum_margin)||evidence.minimum_margin<.25||evidence.minimum_margin>1
      ||evidence.training_overlap!==false||evidence.boundary_stable!==true||evidence.quality_ok!==true
      ||!Number.isInteger(evidence.boundary_variants)||evidence.boundary_variants<3
      ||evidence.independent_gold_accuracy_claimed!==false
      ||!/^[a-f0-9]{64}$/.test(evidence.pipeline_sha256||'')
      ||!/^[a-f0-9]{64}$/.test(evidence.native_model_sha256||'')
      ||!/^[a-f0-9]{64}$/.test(evidence.decoded_sha256||'')
      ||!reference||!nonempty(reference.identity)||sourceForPath(reference.audio_path||'')!=='audio_cmn'
      ||reference.sha256===entry.sha256||reference.decoded_sha256===evidence.decoded_sha256
      ||!/^[a-f0-9]{64}$/.test(reference.sha256||'')||!/^[a-f0-9]{64}$/.test(reference.decoded_sha256||'')
      ||!Number.isFinite(reference.waveform_similarity)||reference.waveform_similarity<0||reference.waveform_similarity>=.995
      ||!Array.isArray(evidence.recognition_checks)||evidence.recognition_checks.length!==2
      ||evidence.recognition_checks[0]?.input!=='raw'||evidence.recognition_checks[1]?.input!=='prepared'
      ||evidence.recognition_checks.some(check=>check.audio_sha256!==entry.sha256||typeof check.transcript!=='string'
        ||(check.decoded_bases!==null&&JSON.stringify(check.decoded_bases)!==JSON.stringify(bases)))
      ||!evidence.recognition_checks.some(check=>JSON.stringify(check.decoded_bases)===JSON.stringify(bases))
      ||!Array.isArray(evidence.comparison_support)||evidence.comparison_support.length!==2){
      throw new Error(`Invalid corroborated whole-word evidence: ${entry.audio_path}`);
    }
  }
  function validateCrossSourceReference(entry){
    if(entry.kind!=='comparison'&&!(entry.kind==='native'&&entry.pinyin_syllables?.length===1&&/^[1-4]$/.test(entry.surface_pattern))){
      throw new Error('Mixed-source reference requires an isolated full-tone syllable');
    }
    const evidence=entry.evidence,check=evidence.cross_fit,peer=evidence.corroboration;
    const key=entry.kind==='comparison'?entry.key:entry.pinyin_syllables[0].replace(/ü/g,'v')+entry.surface_pattern;
    const pattern=key.slice(-1),base=key.slice(0,-1);
    if(isSentenceDerived(entry)
      ||evidence.audio_sha256!==entry.sha256||evidence.label_identity!==identity(entry)
      ||evidence.original_key!==key||evidence.source!==sourceForPath(entry.audio_path)
      ||evidence.independent_gold_accuracy_claimed!==false
      ||!/^[a-f0-9]{64}$/.test(evidence.pipeline_sha256||'')
      ||!/^[a-f0-9]{64}$/.test(evidence.model_bundle_sha256||'')){
      throw new Error(`Invalid mixed-source comparison evidence: ${entry.audio_path}`);
    }
    const validPrediction=(value,path,sha,source)=>{
      const quality=value?.quality;
      if(value?.identity_method==='whisper-small-dual-unprompted-v1'){
        if(!/^[a-f0-9]{64}$/.test(value.identity_model_sha256||'')
          ||!Array.isArray(value.primary_recognition_checks)||value.primary_recognition_checks.length!==2
          ||value.primary_recognition_checks.some(check=>check.audio_sha256!==sha
            ||(Array.isArray(check.decoded_bases)&&check.decoded_bases.length===1&&check.decoded_bases[0]!==base))
          ||value.recognition_checks?.some(check=>!Number.isFinite(check.minimum_log_probability)||check.minimum_log_probability< -1)){
          return false;
        }
      }else if(value?.identity_method&&value.identity_method!=='paraformer-raw-prepared'){
        return false;
      }
      return value?.status==='candidate_supported'&&value.audio_path===path&&value.sha256===sha
        &&value.key===key&&value.expected_tone===pattern&&value.predicted_tone===pattern
        &&value.source===source&&value.source===sourceForPath(path)&&value.training_overlap===false
        &&value.identity_supported===true&&Number.isInteger(value.fold)&&value.fold>=0&&value.fold<5
        &&nonempty(value.family)&&/^[a-f0-9]{64}$/.test(value.model_sha256||'')
        &&/^[a-f0-9]{64}$/.test(value.decoded_sha256||'')
        &&Number.isFinite(value.expected_probability)&&value.expected_probability>=.95&&value.expected_probability<=1
        &&Number.isFinite(value.margin)&&value.margin>=.25&&value.margin<=1
        &&Number.isInteger(quality?.jointly_voiced_frames)&&quality.jointly_voiced_frames>=12
        &&Number.isInteger(quality.usable_trackers)&&quality.usable_trackers>=2&&quality.usable_trackers<=3
        &&Number.isFinite(quality.tracker_difference)&&quality.tracker_difference>=0&&quality.tracker_difference<=2
        &&Number.isFinite(quality.clipped_fraction)&&quality.clipped_fraction>=0&&quality.clipped_fraction<=.01
        &&Array.isArray(value.recognition_checks)&&value.recognition_checks.length===2
        &&value.recognition_checks[0].input==='raw'&&value.recognition_checks[1].input==='prepared'
        &&value.recognition_checks.every(item=>item.audio_sha256===sha&&typeof item.transcript==='string'
          &&(item.decoded_bases===null||JSON.stringify(item.decoded_bases)===JSON.stringify([base])))
        &&value.recognition_checks.some(item=>JSON.stringify(item.decoded_bases)===JSON.stringify([base]));
    };
    if(!validPrediction(check,entry.audio_path,entry.sha256,evidence.source)
      ||!peer||peer.key!==key||peer.source===evidence.source||peer.audio_path===entry.audio_path
      ||peer.sha256===entry.sha256||peer.decoded_sha256===check.decoded_sha256
      ||peer.decoded_sha256!==peer.cross_fit?.decoded_sha256
      ||!Number.isFinite(peer.waveform_similarity)||peer.waveform_similarity<0||peer.waveform_similarity>=.995
      ||!validPrediction(peer.cross_fit,peer.audio_path,peer.sha256,peer.source)){
      throw new Error(`Uncorroborated mixed-source comparison: ${entry.audio_path}`);
    }
    if(entry.kind==='native'&&(!Array.isArray(evidence.comparison_support)
      ||evidence.comparison_support.length!==1
      ||evidence.comparison_support[0]?.audio_path!==peer.audio_path
      ||evidence.comparison_support[0]?.key!==key||evidence.comparison_support[0]?.sha256!==peer.sha256)){
      throw new Error('Mixed-source native example lacks its checked independent comparison');
    }
  }
  function identity(entry){
    if(entry.kind==='native'){
      const values=[
        entry.kind,entry.audio_path,entry.word_id,entry.word,entry.pinyin,
        entry.pinyin_syllables,entry.lexical_pattern,entry.surface_pattern,
      ];
      if(entry.source_segment)values.push(entry.source_segment);
      return JSON.stringify(values);
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
      if(entry.source_segment&&!validSourceSegment(entry.source_segment))throw new Error(`Invalid word excerpt origin: ${entry.audio_path}`);
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
      if(evidence?.method==='cross-source-whole-word-v1'){
        validateCrossSourceWord(entry);
        return;
      }
      if(evidence?.method==='cross-source-native-reference-v1'){
        if(!['local_only','redistributable'].includes(entry.distribution_scope))throw new Error('Invalid mixed-source rights scope');
        validateCrossSourceReference(entry);
        return;
      }
      const expectedBases=entry.kind==='comparison'?[entry.key.slice(0,-1)]:entry.pinyin_syllables.map(base=>base.replace(/ü/g,'v'));
      const tones=entry.kind==='comparison'?[entry.key.slice(-1)]:entry.surface_pattern.split('-');
      if(!['local_only','redistributable'].includes(entry.distribution_scope)
        ||tones.some(tone=>!['1','2','3','4','N'].includes(tone))
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
      if(evidence.recognition_checks!==undefined){
        const checks=evidence.recognition_checks;
        if(!Array.isArray(checks)||checks.length!==2||checks[0].input!=='raw'||checks[1].input!=='prepared'
          ||checks[0].evidence_version!=='paraformer-unprompted-1'
          ||checks[1].evidence_version!=='paraformer-dc70-rms010-trim30-1'){
          throw new Error(`Missing raw/prepared recognition comparison: ${entry.audio_path}`);
        }
        let matched=0;
        for(const check of checks){
          if(check.audio_sha256!==entry.sha256||typeof check.transcript!=='string'
            ||(check.decoded_bases!==null&&JSON.stringify(check.decoded_bases)!==JSON.stringify(expectedBases))){
            throw new Error(`Raw/prepared recognition disagrees: ${entry.audio_path}`);
          }
          if(check.decoded_bases!==null)matched++;
        }
        const preparation=checks[1].preparation;
        if(!matched||!preparation||preparation.sample_rate!==16000
          ||!Number.isFinite(preparation.gain)||preparation.gain<=0
          ||!Number.isFinite(preparation.trim_start_seconds)||preparation.trim_start_seconds<0
          ||!Number.isFinite(preparation.trim_end_seconds)||preparation.trim_end_seconds<=preparation.trim_start_seconds){
          throw new Error(`Invalid recognition preparation evidence: ${entry.audio_path}`);
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
          ||!Number.isFinite(decision.voiced_seconds)||decision.voiced_seconds<(tones[index]==='N'?.08:.12)
          ||decision.voiced_seconds>decision.end-decision.start+.021){
          throw new Error(`Conflicting or incomplete tone evidence: ${entry.audio_path}`);
        }
        if(tones[index]==='N'){
          const prosody=decision.prosody;
          if(index===0||tones[index-1]==='N'||decision.method!=='contextual-neutral-reduction-1'
            ||prosody?.preceding_tone!==tones[index-1]
            ||!Number.isFinite(prosody.duration_ratio)||prosody.duration_ratio<=0||prosody.duration_ratio>.75
            ||!Number.isFinite(prosody.intensity_ratio)||prosody.intensity_ratio<=0||prosody.intensity_ratio>.9){
            throw new Error(`Neutral tone lacks contextual reduction evidence: ${entry.audio_path}`);
          }
          const reading=evidence.neutral_lexical_reading;
          if(evidence.neutral_lexicon!=='CC-CEDICT'||!/^[a-f0-9]{64}$/.test(evidence.neutral_lexicon_sha256||'')
            ||!Array.isArray(reading)||reading.length!==tones.length
            ||reading.some((value,position)=>typeof value!=='string'||!/^[a-zv]+[1-5]$/.test(value)||value.slice(0,-1)!==expectedBases[position]
              ||(value.slice(-1)==='5')!==(tones[position]==='N')
              ||(tones[position]!=='N'&&value.slice(-1)!==entry.lexical_pattern.split('-')[position]))){
            throw new Error(`Neutral reading lacks independent lexical evidence: ${entry.audio_path}`);
          }
          if(Object.values(prosody.lexical_votes||{}).some(vote=>['2','3','4'].includes(vote))){
            throw new Error('Neutral example has a conflicting full-tone contour');
          }
          for(const [method,vote] of Object.entries(votes)){
            if(vote!=='N')continue;
            const ratio=prosody.pitch_ratios?.[method];
            const minimum=prosody.preceding_tone==='3'?.75:.35;
            const maximum=prosody.preceding_tone==='3'?1.2:.85;
            if(!Number.isFinite(ratio)||ratio<minimum||ratio>maximum)throw new Error('Neutral pitch context mismatch');
            const range=prosody.pitch_ranges?.[method];
            if(!Array.isArray(range)||range.length!==2||range.some(value=>!Number.isFinite(value))
              ||range[0]<minimum||range[1]>maximum||range[1]<range[0]
              ||!Object.hasOwn(prosody.lexical_votes||{},method)
              ||!['1',null].includes(prosody.lexical_votes[method])){
              throw new Error('Neutral example has a conflicting full-tone contour');
            }
          }
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
  function applyPracticeSelection(index,selection,acousticLedger,{allowLocalOnly=true}={}){
    if(selection?.version!==1||selection.policy!=='original-label-acoustic-model-agreement-v1'
      ||selection.accuracy_certified!==false||!Array.isArray(selection.entries)
      ||selection.acoustic_pipeline_sha256!==acousticLedger?.pipeline_sha256
      ||selection.minimum_probability!==.9||selection.minimum_margin!==.15
      ||!['native_model_sha256','native_inventory_sha256','native_predictions_sha256'].every(key=>/^[a-f0-9]{64}$/.test(selection[key]||''))){
      throw new Error('Invalid agreement-based practice selection');
    }
    const allowed=new Set(),seen=new Set();
    for(const item of selection.entries){
      if(seen.has(item.identity))throw new Error('Duplicate agreement selection');
      seen.add(item.identity);
      if(!allowLocalOnly&&item.distribution_scope==='local_only')continue;
      const assessment=index.get(item.identity),agreement=item.agreement;
      const expected=assessment?.kind==='native'?assessment.surface_pattern:assessment?.key.slice(-1);
      if(!assessment||item.kind!==assessment.kind||item.sha256!==assessment.sha256||item.audio_path!==assessment.audio_path
        ||item.distribution_scope!==assessment.distribution_scope||item.pattern!==expected
        ||isSentenceDerived(item)||agreement?.supplied_pattern!==expected||agreement.blind_pattern!==expected
        ||!Number.isFinite(agreement.probability)||agreement.probability<.9||agreement.probability>1
        ||!Number.isFinite(agreement.margin)||agreement.margin<.15||agreement.margin>1
        ||agreement.quality_ok!==true||agreement.boundary_stable!==true||agreement.identity_supported!==true
        ||agreement.training_overlap!==false){
        throw new Error(`Recording does not satisfy practice agreement: ${item.audio_path}`);
      }
      allowed.add(item.identity);
    }
    for(const key of index.keys())if(!allowed.has(key))index.delete(key);
  }
  function createIndex(ledger,acousticLedger=null,{allowLocalOnly=true,sourceRecordings=[],sourceWords=[],practiceSelection=null}={}){
    if(ledger?.version!==1||!Array.isArray(ledger.approvals))throw new Error('Invalid audio review ledger');
    const index=new Map();
    for(const entry of ledger.approvals){
      if(entry.assessment==='automated')throw new Error('Automated results must use the acoustic ledger, not human attestations');
      validateApproval(entry);
      if(isSentenceDerived(entry))continue;
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
        if(isSentenceDerived(entry))continue;
        if(entry.kind==='native'&&entry.surface_pattern.split('-').includes('N')
          &&entry.evidence.neutral_lexicon_sha256!==acousticLedger.neutral_lexicon_sha256){
          throw new Error(`Neutral dictionary fingerprint mismatch: ${entry.audio_path}`);
        }
        if(entry.evidence.method==='spectral-consensus-1'&&acousticLedger.recognition_policy==='raw-prepared-no-phonetic-conflict-1'&&!entry.evidence.recognition_checks){
          throw new Error(`Missing prepared recognition evidence: ${entry.audio_path}`);
        }
        const supplemental=acousticLedger.supplemental_pipelines?.[entry.evidence.method];
        if(entry.evidence.method==='cross-source-whole-word-v1'){
          if(!supplemental||supplemental.pipeline_sha256!==entry.evidence.pipeline_sha256
            ||supplemental.native_model_sha256!==entry.evidence.native_model_sha256
            ||supplemental.minimum_probability!==.95||supplemental.minimum_margin!==.25
            ||supplemental.independent_gold_accuracy_claimed!==false)throw new Error('Whole-word pipeline fingerprint mismatch');
        }else if(entry.evidence.method==='cross-source-native-reference-v1'){
          if(!supplemental||supplemental.pipeline_sha256!==entry.evidence.pipeline_sha256
            ||supplemental.model_bundle_sha256!==entry.evidence.model_bundle_sha256
            ||supplemental.minimum_probability!==.95||supplemental.minimum_margin!==.25
            ||supplemental.minimum_distinct_sources!==2||supplemental.independent_gold_accuracy_claimed!==false){
            throw new Error('Mixed-source pipeline fingerprint mismatch');
          }
        }else if(entry.evidence.pipeline_sha256!==acousticLedger.pipeline_sha256)throw new Error('Acoustic pipeline fingerprint mismatch');
        const key=identity(entry);
        if(seen.has(key))throw new Error(`Duplicate acoustic decision: ${entry.audio_path}`);
        seen.add(key);
        if(!allowLocalOnly&&entry.distribution_scope==='local_only')continue;
        if(!index.has(key))index.set(key,entry);
      }
      for(const entry of index.values()){
        if(entry.assessment!=='automated'||entry.kind!=='native')continue;
        if(entry.evidence.method==='cross-source-whole-word-v1'){
          const link=entry.evidence.whole_word_reference,reference=index.get(link.identity);
          if(!reference||reference.kind!=='native'||reference.audio_path!==link.audio_path||reference.sha256!==link.sha256
            ||reference.word_id!==entry.word_id||reference.word!==entry.word||reference.surface_pattern!==entry.surface_pattern
            ||reference.lexical_pattern!==entry.lexical_pattern
            ||JSON.stringify(reference.pinyin_syllables)!==JSON.stringify(entry.pinyin_syllables)
            ||reference.evidence?.method!=='spectral-consensus-1'){
            throw new Error(`Whole-word reference is stale or unrelated: ${entry.audio_path}`);
          }
        }
        entry.evidence.comparison_support.forEach((support,position)=>{
          const tone=entry.surface_pattern.split('-')[position];
          if(tone==='N'){
            if(support!==null)throw new Error('Neutral tone must not reference a standalone tone clip');
            return;
          }
          const expected=entry.pinyin_syllables[position].replace(/ü/g,'v')+tone;
          const comparison=index.get(identity(comparisonDescriptor(support.key,support)));
          if(support.key!==expected||!comparison||comparison.sha256!==support.sha256||support.sha256===entry.sha256){
            throw new Error(`Stale or missing independent tone reference: ${entry.audio_path}`);
          }
        });
      }
    }
    if(practiceSelection!==null)applyPracticeSelection(index,practiceSelection,acousticLedger,{allowLocalOnly});
    const alternatives=new Map();
    for(const entry of index.values()){
      if(entry.kind!=='comparison')continue;
      if(!alternatives.has(entry.key))alternatives.set(entry.key,[]);
      alternatives.get(entry.key).push(entry);
    }
    index.comparisonAlternatives=alternatives;
    index.sourceRecordings=new Map(sourceRecordings.map(recording=>[recording.audio_path,recording]));
    index.sourceWords=new Map(sourceWords.map(word=>[word.id,word]));
    if(index.sourceWords.size!==sourceWords.length)throw new Error('Duplicate source vocabulary identifiers');
    index.neutralReferences=new Map();
    for(const entry of index.values()){
      if(entry.kind!=='native')continue;
      entry.surface_pattern.split('-').forEach((tone,position)=>{
        if(tone!=='N')return;
        const base=entry.pinyin_syllables[position].replace(/ü/g,'v');
        if(!index.neutralReferences.has(base))index.neutralReferences.set(base,[]);
        index.neutralReferences.get(base).push(entry);
      });
    }
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
  function neutralSelection(index,base){
    for(const approval of index.neutralReferences?.get(base.replace(/ü/g,'v'))||[]){
      const recording=index.sourceRecordings.get(approval.audio_path);
      const word=index.sourceWords?.get(approval.word_id);
      if(word&&recording&&nativeApproval(index,word,recording)===approval)return approval;
    }
    return null;
  }
  function correctionSelection(policy,key,quality,recordings,index,preferredSource='pinyin_public'){
    const allowed=approval=>{
      const recording=index.sourceRecordings?.get(approval.audio_path);
      if(approval.audio_path.startsWith('audio/mandarin_native/')){
        return recording?.recording_type==='word_candidate'&&!nativeBlockReason(recording,approval);
      }
      return !recording||!nativeBlockReason(recording,approval);
    };
    if(preferredSource==='mandarin_native'){
      for(const approval of index.comparisonAlternatives?.get(key)||[]){
        if(approval.audio_path.startsWith('audio/mandarin_native/')&&allowed(approval)){
          return {audio_path:approval.audio_path,source:'mandarin_native',enhanced:false,approval};
        }
      }
      preferredSource='pinyin_public';
    }
    const alternate=preferredSource==='audio_cmn'?'pinyin_public':'audio_cmn';
    for(const source of [preferredSource,alternate]){
      const selected=policy.correctionSelection(key,quality,recordings,source);
      if(!selected)continue;
      const approval=comparisonApproval(index,key,selected);
      if(approval&&allowed(approval))return {...selected,approval};
    }
    const excluded=new Set([
      recordings[key]?.audio_path,
      `audio/audio_cmn/syllabs/cmn-${key==='ju4'?'jv4':key}.mp3`,
    ]);
    for(const approval of index.comparisonAlternatives?.get(key)||[]){
      const source=approval.audio_path.startsWith('audio/audio_cmn/')?'audio_cmn'
        :index.sourceRecordings?.get(approval.audio_path)?.recording_type==='word_candidate'?'mandarin_native':null;
      if(!excluded.has(approval.audio_path)&&source&&allowed(approval)){
        return {audio_path:approval.audio_path,source,enhanced:false,approval};
      }
    }
    return null;
  }
  async function verifyBytes(bytes,approval){
    if(!approval)throw new Error('Audio has no qualifying assessment');
    if(isSentenceDerived(approval))throw new Error('Sentence-extracted audio is not used for tone practice');
    validateApproval(approval);
    if(!globalThis.crypto?.subtle)throw new Error('Audio verification requires a secure browser context');
    const digest=await globalThis.crypto.subtle.digest('SHA-256',bytes);
    const actual=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
    if(actual!==approval.sha256)throw new Error(`Audio changed since ${approval.assessment==='automated'?'acoustic screening':'listening review'}: ${approval.audio_path}`);
  }
  return {
    isSentenceDerived,validSourceSegment,nativeCandidates,nativeBlockReason,nativeDescriptor,comparisonDescriptor,identity,validateApproval,applyPracticeSelection,createIndex,
    nativeApproval,comparisonApproval,neutralSelection,correctionSelection,verifyBytes,
  };
});
