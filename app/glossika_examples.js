(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.GlossikaExamples=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const METHOD='publisher-isolated-drills-v1';
  const HASH=/^[a-f0-9]{64}$/;
  const PATTERN=/^[1-4N](?:-[1-4N])*$/;
  const SOURCE_PATH=/^audio\/glossika\/lessons\/([a-z0-9-]+)\.mp3$/;
  const SOURCE_SAMPLE_RATES=new Set([44100,48000]);
  const DECODER_FRAME_TOLERANCE=512;
  const nonempty=value=>typeof value==='string'&&value.trim().length>0;
  const patternTones=pattern=>pattern.split('-').map(tone=>tone==='N'?0:Number(tone));

  function excludedCatalog(reason='Glossika individual practice data is not installed in this build.'){
    return {
      version:1,source:'glossika',available:false,method:METHOD,
      distribution_scope:'excluded',book_sha256:null,archive_sha256:null,
      index_sha256:null,boundaries_sha256:null,examples:[],
      counts:{total:0,mapped:0,quiz_eligible:0,unresolved:0},
      unavailable_reason:reason,
    };
  }

  function validSegment(segment,example=null){
    return Boolean(segment
      &&SOURCE_SAMPLE_RATES.has(segment.sample_rate)
      &&segment.channels===2
      &&Number.isInteger(segment.start_sample)
      &&Number.isInteger(segment.end_sample)
      &&Number.isInteger(segment.speech_start_sample)
      &&Number.isInteger(segment.speech_end_sample)
      &&Number.isInteger(segment.source_frames)
      &&segment.source_frames>0
      &&segment.start_sample>=0
      &&segment.start_sample<=segment.speech_start_sample
      &&segment.speech_start_sample<segment.speech_end_sample
      &&segment.speech_end_sample<=segment.end_sample
      &&segment.end_sample<=segment.source_frames
      &&(!example||segment.item_id===undefined||segment.item_id===example.id)
      &&(!example||segment.lesson_id===undefined||segment.lesson_id===example.lesson_id));
  }

  function validateExample(example,ids){
    if(!example||!nonempty(example.id)||!/^[a-z0-9-]+$/.test(example.id)||ids.has(example.id)
      ||!nonempty(example.lesson_id)||!Number.isInteger(example.ordinal)||example.ordinal<1
      ||!Number.isInteger(example.printed_page)||example.printed_page<1
      ||!Number.isInteger(example.pdf_page)||example.pdf_page<1
      ||!Array.isArray(example.bounds)||example.bounds.length!==4
      ||example.bounds.some(value=>!Number.isFinite(value))
      ||!nonempty(example.source_pattern)||!nonempty(example.word)||!nonempty(example.pinyin)
      ||!(example.traditional===null||nonempty(example.traditional))
      ||!['syllable_drill','word_drill'].includes(example.kind)
      ||!Array.isArray(example.pinyin_syllables)||!example.pinyin_syllables.length
      ||!example.pinyin_syllables.every(value=>/^[a-zv]+$/.test(value))
      ||!Array.isArray(example.lexical_tones)
      ||example.lexical_tones.length!==example.pinyin_syllables.length
      ||example.lexical_tones.some(tone=>![0,1,2,3,4].includes(tone))
      ||!PATTERN.test(example.lexical_pattern||'')
      ||example.lexical_pattern.split('-').length!==example.pinyin_syllables.length
      ||!PATTERN.test(example.surface_pattern||'')
      ||example.surface_pattern.split('-').length!==example.pinyin_syllables.length
      ||!Array.isArray(example.sandhi_tags)
      ||typeof example.quiz_eligible!=='boolean'
      ||!nonempty(example.source_audio_path)||!SOURCE_PATH.test(example.source_audio_path)
      ||!HASH.test(example.source_audio_sha256||'')
      ||example.source_audio_path!==`audio/glossika/lessons/${example.lesson_id}.mp3`
      ||!example.mapping||!['mapped','unresolved'].includes(example.mapping.status)
      ||typeof example.mapping.intro_verified!=='boolean'
      ||typeof example.mapping.boundary_stable!=='boolean'
      ||typeof example.mapping.full_utterance!=='boolean'
      ||!(example.mapping.reason===null||nonempty(example.mapping.reason))
      ||!(example.block_reason===null||nonempty(example.block_reason))){
      throw new Error('Invalid Glossika practice example');
    }
    const expectedPattern=example.lexical_tones.map(tone=>tone===0?'N':String(tone)).join('-');
    if(expectedPattern!==example.lexical_pattern)throw new Error(`Glossika lexical labels disagree: ${example.id}`);
    const mapped=example.mapping.status==='mapped';
    if(mapped!==Boolean(example.segment))throw new Error(`Glossika mapping and segment disagree: ${example.id}`);
    if(example.segment&&!validSegment(example.segment,example))throw new Error(`Invalid Glossika source segment: ${example.id}`);
    const playable=mapped&&example.mapping.intro_verified&&example.mapping.boundary_stable&&example.mapping.full_utterance;
    if(example.quiz_eligible&&(!playable||example.block_reason!==null
      ||example.source_pattern_matches_lexical===false
      ||example.kind==='syllable_drill'&&example.lexical_pattern==='N'
      ||example.sandhi_tags.some(tag=>/third.*ambiguous|ambiguous.*third/.test(tag)))){
      throw new Error(`Unsafe Glossika quiz admission: ${example.id}`);
    }
    if(!example.quiz_eligible&&!nonempty(example.block_reason)){
      throw new Error(`Listen-only Glossika example lacks a reason: ${example.id}`);
    }
    ids.add(example.id);
  }

  function validateCatalog(data){
    if(data?.version!==1||data.source!=='glossika'||data.method!==METHOD
      ||typeof data.available!=='boolean'||!Array.isArray(data.examples)
      ||!data.counts||typeof data.counts!=='object'){
      throw new Error('Invalid Glossika practice catalog');
    }
    if(!data.available){
      if(data.examples.length||data.distribution_scope!=='excluded'||!nonempty(data.unavailable_reason)
        ||data.book_sha256!==null||data.archive_sha256!==null
        ||data.index_sha256!==null||data.boundaries_sha256!==null){
        throw new Error('Invalid excluded Glossika practice catalog');
      }
      return data;
    }
    if(data.distribution_scope!=='local_only'
      ||![data.book_sha256,data.archive_sha256,data.index_sha256,data.boundaries_sha256].every(value=>HASH.test(value||''))){
      throw new Error('Glossika practice must retain pinned local-only provenance');
    }
    const ids=new Set();
    for(const example of data.examples)validateExample(example,ids);
    const mapped=data.examples.filter(example=>example.mapping.status==='mapped').length;
    const quiz=data.examples.filter(example=>example.quiz_eligible).length;
    const unresolved=data.examples.length-mapped;
    const claimedTotal=data.counts.total??data.counts.examples??data.counts.total_items;
    if(claimedTotal!==undefined&&claimedTotal!==data.examples.length
      ||data.counts.mapped!==undefined&&data.counts.mapped!==mapped
      ||data.counts.quiz_eligible!==undefined&&data.counts.quiz_eligible!==quiz
      ||data.counts.unresolved!==undefined&&data.counts.unresolved!==unresolved){
      throw new Error('Glossika practice counts do not match its examples');
    }
    return data;
  }

  function isPlayableExample(example){
    return Boolean(example?.segment&&validSegment(example.segment,example)
      &&example.mapping?.status==='mapped'
      &&example.mapping.intro_verified===true
      &&example.mapping.boundary_stable===true
      &&example.mapping.full_utterance===true);
  }

  function drillSource(example){
    if(!isPlayableExample(example))return null;
    return {
      audio_path:example.source_audio_path,
      sha256:example.source_audio_sha256,
      hash_scope:'parent_file',
      sample_rate:example.segment.sample_rate,
      start_sample:example.segment.start_sample,
      end_sample:example.segment.end_sample,
      speech_start_sample:example.segment.speech_start_sample,
      speech_end_sample:example.segment.speech_end_sample,
      source_frames:example.segment.source_frames,
      channels:example.segment.channels,
      item_id:example.id,
      lesson_id:example.lesson_id,
    };
  }

  function logicalAudioPath(example){
    return `audio/glossika/examples/${example.id}.wav`;
  }

  function runtimeData(data){
    const catalog=validateCatalog(data);
    const words=[],recordings=[],approvals=[],examplesById=new Map();
    if(!catalog.available){
      return {
        catalog,words,recordings,
        publisherLedger:{version:1,source:'glossika',method:METHOD,assessments:approvals},
        examplesById,
      };
    }
    for(const example of catalog.examples){
      const word={
        id:example.id,source_item_id:example.id,source:'glossika',
        word:example.word,traditional:example.traditional??null,pinyin:example.pinyin,
        pinyin_syllables:[...example.pinyin_syllables],
        lexical_tones:[...example.lexical_tones],lexical_pattern:example.lexical_pattern,
        default_surface_tones:patternTones(example.surface_pattern),
        default_surface_pattern:example.surface_pattern,
        sandhi_tags:[...example.sandhi_tags],definition:'',
        practice_label:example.kind==='syllable_drill'?'Pronunciation exercise':'',
        source_pattern:example.source_pattern,printed_page:example.printed_page,pdf_page:example.pdf_page,
      };
      const source=drillSource(example);
      let recording=null,nativeApproval=null;
      if(source){
        const audio_path=logicalAudioPath(example);
        recording={
          word:example.word,source:'glossika',recording_type:'publisher_drill',
          audio_path,filename:`${example.id}.wav`,hsk_id:example.id,candidate_hsk_ids:[example.id],
          surface_pattern:example.surface_pattern,quiz_eligible:example.quiz_eligible,
          review_status:'source_attested',rights_status:'personal_companion_only',
          distribution_scope:'local_only',source_item_id:example.id,
          block_reason:example.block_reason,drill_source:source,
        };
        nativeApproval={
          kind:'native',audio_path,word_id:word.id,word:word.word,pinyin:word.pinyin,
          pinyin_syllables:word.pinyin_syllables,lexical_pattern:word.lexical_pattern,
          surface_pattern:recording.surface_pattern,drill_source:source,
          source:'glossika',source_item_id:example.id,
          assessment:'publisher_source',status:'source_attested',
          sha256:source.sha256,hash_scope:'parent_file',quiz_eligible:example.quiz_eligible,
          distribution_scope:'local_only',rights_status:'personal_companion_only',
        };
        recordings.push(recording);
        approvals.push(nativeApproval);
        if(example.quiz_eligible&&example.kind==='syllable_drill'&&/^[1-4]$/.test(example.lexical_pattern)){
          approvals.push({
            kind:'comparison',audio_path,key:example.pinyin_syllables[0]+example.lexical_pattern,
            drill_source:source,source:'glossika',source_item_id:example.id,assessment:'publisher_source',
            status:'source_attested',sha256:source.sha256,hash_scope:'parent_file',
            quiz_eligible:true,distribution_scope:'local_only',rights_status:'personal_companion_only',
          });
        }
      }
      words.push(word);
      examplesById.set(example.id,{example,word,recording,nativeApproval});
    }
    return {
      catalog,words,recordings,
      publisherLedger:{version:1,source:'glossika',method:METHOD,assessments:approvals},
      examplesById,
    };
  }

  function mediaAssets(data){
    const catalog=validateCatalog(data),assets=new Map();
    if(!catalog.available)return [];
    for(const example of catalog.examples){
      if(!isPlayableExample(example))continue;
      const previous=assets.get(example.source_audio_path);
      if(previous&&previous.sha256!==example.source_audio_sha256){
        throw new Error(`Glossika parent hash conflict: ${example.source_audio_path}`);
      }
      assets.set(example.source_audio_path,{
        audio_path:example.source_audio_path,sha256:example.source_audio_sha256,
      });
    }
    return [...assets.values()];
  }

  function sliceAudioBuffer(context,decoded,source){
    if(!validSegment(source))throw new Error('Invalid Glossika source segment');
    if(!decoded||decoded.numberOfChannels!==source.channels
      ||!Number.isInteger(decoded.length)||decoded.length<=0
      ||!Number.isFinite(decoded.sampleRate)||decoded.sampleRate<=0){
      throw new Error('Decoded Glossika parent audio does not match its channel metadata');
    }
    const ratio=decoded.sampleRate/source.sample_rate;
    const expectedFrames=Math.round(source.source_frames*ratio);
    const tolerance=Math.ceil(DECODER_FRAME_TOLERANCE*ratio);
    if(Math.abs(decoded.length-expectedFrames)>tolerance){
      throw new Error('Decoded Glossika parent length differs from its source metadata');
    }
    // Canonical 48 kHz sample boundaries are converted by time, never by resampling the source slice.
    const start=Math.round(source.start_sample/source.sample_rate*decoded.sampleRate);
    const end=Math.round(source.end_sample/source.sample_rate*decoded.sampleRate);
    if(start<0||end<=start||end>decoded.length){
      throw new Error('Glossika source segment is outside decoded parent audio');
    }
    const output=context.createBuffer(decoded.numberOfChannels,end-start,decoded.sampleRate);
    for(let channel=0;channel<decoded.numberOfChannels;channel++){
      const selected=decoded.getChannelData(channel).subarray(start,end);
      if(typeof output.copyToChannel==='function')output.copyToChannel(selected,channel);
      else output.getChannelData(channel).set(selected);
    }
    return output;
  }

  function encodeFloatWav(buffer){
    if(!buffer||!Number.isInteger(buffer.numberOfChannels)||buffer.numberOfChannels<1
      ||!Number.isInteger(buffer.length)||buffer.length<1
      ||!Number.isFinite(buffer.sampleRate)||buffer.sampleRate<=0){
      throw new Error('Invalid AudioBuffer for WAV encoding');
    }
    const channels=Array.from(
      {length:buffer.numberOfChannels},
      (_,channel)=>buffer.getChannelData(channel),
    );
    if(channels.some(channel=>channel.length!==buffer.length
      ||Array.from(channel).some(sample=>!Number.isFinite(sample)))){
      throw new Error('Invalid PCM samples for WAV encoding');
    }
    const bytesPerSample=4;
    const dataBytes=buffer.length*buffer.numberOfChannels*bytesPerSample;
    const output=new ArrayBuffer(44+dataBytes);
    const view=new DataView(output);
    const ascii=(offset,value)=>{
      for(let index=0;index<value.length;index++)view.setUint8(offset+index,value.charCodeAt(index));
    };
    ascii(0,'RIFF');view.setUint32(4,36+dataBytes,true);ascii(8,'WAVE');
    ascii(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,3,true);
    view.setUint16(22,buffer.numberOfChannels,true);view.setUint32(24,buffer.sampleRate,true);
    view.setUint32(28,buffer.sampleRate*buffer.numberOfChannels*bytesPerSample,true);
    view.setUint16(32,buffer.numberOfChannels*bytesPerSample,true);view.setUint16(34,32,true);
    ascii(36,'data');view.setUint32(40,dataBytes,true);
    let offset=44;
    for(let frame=0;frame<buffer.length;frame++){
      for(let channel=0;channel<buffer.numberOfChannels;channel++){
        view.setFloat32(offset,channels[channel][frame],true);
        offset+=bytesPerSample;
      }
    }
    return output;
  }

  return {
    METHOD,DECODER_FRAME_TOLERANCE,excludedCatalog,validateCatalog,validSegment,
    isPlayableExample,drillSource,logicalAudioPath,runtimeData,mediaAssets,
    sliceAudioBuffer,encodeFloatWav,
  };
});
