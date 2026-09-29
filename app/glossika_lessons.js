(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.GlossikaLessons=api;
})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  function validateCatalog(data){
    if(data?.version!==1||data.source!=='glossika'||typeof data.available!=='boolean'
      ||!Array.isArray(data.lessons)||!Array.isArray(data.pages))throw new Error('Invalid Glossika companion catalog');
    if(!data.available){
      if(data.lessons.length||data.pages.length||data.book!==null)throw new Error('Excluded companion contains assets');
      return data;
    }
    if(data.distribution_scope!=='local_only'||data.rights_status!=='personal_companion_only'
      ||data.license!=='All rights reserved'||data.book?.audio_path!=='audio/glossika/book.pdf'
      ||data.book.page_count!==175||data.lessons.length!==117)throw new Error('Invalid personal companion scope');
    const assets=[data.book,...data.pages,...data.lessons],paths=new Set();
    for(const asset of assets){
      if(!/^audio\/glossika\/(?:book\.pdf|pages\/\d{3}\.png|lessons\/[a-z0-9-]+\.mp3)$/.test(asset.audio_path||'')
        ||!/^[a-f0-9]{64}$/.test(asset.sha256||'')||!Number.isInteger(asset.byte_length)||asset.byte_length<=0
        ||paths.has(asset.audio_path))throw new Error('Invalid or duplicate companion asset');
      paths.add(asset.audio_path);
    }
    const pages=new Map();
    for(const page of data.pages){
      if(!Number.isInteger(page.printed_page)||page.printed_page<3||page.printed_page>162
        ||!Number.isInteger(page.pdf_page)||page.pdf_page<1||page.pdf_page>175
        ||page.audio_path!==`audio/glossika/pages/${String(page.printed_page).padStart(3,'0')}.png`
        ||pages.has(page.printed_page))throw new Error('Invalid companion book page');
      pages.set(page.printed_page,page);
    }
    const ids=new Set(),counts={consonants:0,syllables:0,'two-tone':0,'three-tone':0};
    for(const lesson of data.lessons){
      const match=/^(consonant-([12])|vowel-part-(\d+)|tone-([1-4]{2,3}))$/.exec(lesson.id||'');
      const category=match?.[2]?'consonants':match?.[3]?'syllables':match?.[4]?.length===2?'two-tone':'three-tone';
      if(!match||match[3]&&(Number(match[3])<1||Number(match[3])>35)
        ||lesson.category!==category||ids.has(lesson.id)||lesson.audio_path!==`audio/glossika/lessons/${lesson.id}.mp3`
        ||lesson.recording_type!=='book_lesson'||lesson.quiz_eligible!==false
        ||typeof lesson.title!=='string'||!lesson.title.trim()
        ||!Array.isArray(lesson.printed_pages)||!lesson.printed_pages.length
        ||lesson.printed_pages.some(page=>!pages.has(page)))throw new Error('Invalid complete book lesson');
      ids.add(lesson.id);counts[category]++;
    }
    if(counts.consonants!==2||counts.syllables!==35||counts['two-tone']!==16||counts['three-tone']!==64){
      throw new Error('Incomplete companion lesson groups');
    }
    return data;
  }
  function assetsFor(data){
    validateCatalog(data);
    return data.available?[data.book,...data.pages,...data.lessons]:[];
  }
  function excludedCatalog(){
    return {version:1,source:'glossika',available:false,book:null,pages:[],lessons:[]};
  }
  function validatePracticeLinks(catalog,practiceCatalog){
    if(!practiceCatalog?.available)return;
    const lessons=new Map(catalog.lessons.map(lesson=>[lesson.id,lesson]));
    for(const example of practiceCatalog.examples){
      const lesson=lessons.get(example.lesson_id);
      if(!lesson||example.source_audio_path!==lesson.audio_path
        ||example.source_audio_sha256!==lesson.sha256
        ||!lesson.printed_pages.includes(example.printed_page)){
        throw new Error(`Glossika example does not match its complete lesson: ${example.id}`);
      }
    }
  }
  function createController({
    get,fetch,hashBytes,playbackGain,stopOthers,canPlay,
    practiceCatalog=null,examplesById=new Map(),playExample=null,practiceExample=null,
    canPracticeExample=()=>false,createElement=tag=>document.createElement(tag),
  }){
    let catalog=null,current=null,generation=0,pageGeneration=0,pagePosition=0;
    let audioURL=null,pageURL=null,loading=false,bookVerified=false;
    const panel=get('glossikaLessons'),player=get('lessonPlayer'),status=get('lessonStatus');
    const category=get('lessonCategory'),choice=get('lessonChoice'),image=get('lessonPage');
    const exampleChoice=get('lessonExampleChoice'),exampleDetails=get('lessonExampleDetails');
    const playExampleButton=get('playLessonExample'),practiceExampleButton=get('practiceLessonExample');
    const report=error=>{
      console.error('Glossika companion failed:',error);
      status.textContent=`Cannot open this companion lesson: ${error.message}`;
    };
    const revokeAudio=()=>{
      player.pause();player.removeAttribute('src');player.load();
      if(audioURL)URL.revokeObjectURL(audioURL);
      audioURL=null;
    };
    function stop(){
      if(!catalog?.available)return;
      generation++;pageGeneration++;player.pause();
      if(loading){loading=false;get('openLesson').disabled=false;status.textContent='Lesson loading cancelled.';}
    }
    async function bytesFor(asset){
      const response=await fetch(`../${asset.audio_path}`);
      if(!response.ok)throw new Error(`Missing companion asset (HTTP ${response.status}); run npm run download:glossika`);
      const bytes=await response.arrayBuffer();
      if(bytes.byteLength!==asset.byte_length||await hashBytes(bytes)!==asset.sha256){
        throw new Error(`Companion source file changed: ${asset.audio_path}`);
      }
      return bytes;
    }
    async function showPage(position,token=generation){
      const request=++pageGeneration,printed=current.printed_pages[position];
      const page=catalog.pages.find(item=>item.printed_page===printed);
      const bytes=await bytesFor(page);
      if(token!==generation||request!==pageGeneration)return false;
      if(pageURL)URL.revokeObjectURL(pageURL);
      pageURL=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));
      image.src=pageURL;image.alt=`Original Glossika book, printed page ${printed}`;
      image.hidden=false;pagePosition=position;
      get('lessonPageNumber').textContent=`Book page ${printed} (${position+1} of ${current.printed_pages.length})`;
      get('previousLessonPage').disabled=position===0;
      get('nextLessonPage').disabled=position===current.printed_pages.length-1;
      return true;
    }
    function selectedLesson(){
      return catalog?.lessons.find(item=>item.id===choice.value)||null;
    }
    function selectedExample(){
      return practiceCatalog?.examples?.find(item=>item.id===exampleChoice.value)||null;
    }
    function renderExample(){
      const example=selectedExample();
      const runtime=example&&examplesById.get(example.id);
      playExampleButton.disabled=!runtime?.nativeApproval;
      practiceExampleButton.disabled=!example||!example.quiz_eligible||!canPracticeExample(example);
      if(!example){
        exampleDetails.textContent=practiceCatalog?.available
          ?'This lesson has no indexed drill items.'
          :practiceCatalog?.unavailable_reason||'Individual examples are unavailable in this build.';
        return;
      }
      const lexical=example.lexical_pattern;
      const surface=example.surface_pattern;
      const distinction=!example.quiz_eligible
        ?`Source/lexical pattern ${lexical}.`
        :surface===lexical?`Tone pattern ${surface}.`
          :`Lexical ${lexical}; heard here as ${surface}.`;
      const label=example.kind==='syllable_drill'?'Pronunciation exercise. ':'';
      const availability=!runtime?.nativeApproval
        ?` Individual playback unavailable: ${example.mapping.reason||example.block_reason||'no verified interval'}.`
        :example.quiz_eligible?' Ready for graded practice.'
          :` Listen only: ${example.block_reason||'not admitted for grading'}.`;
      exampleDetails.textContent=`${label}${example.word} · ${example.pinyin}. ${distinction}${availability}`;
    }
    async function showSelectedExamplePage(){
      const lesson=selectedLesson(),example=selectedExample();
      if(!lesson||!example)return false;
      current=lesson;
      const position=lesson.printed_pages.indexOf(example.printed_page);
      if(position<0)throw new Error('Indexed example page is outside its lesson');
      return showPage(position);
    }
    function populateExamples(){
      exampleChoice.innerHTML='';
      const lesson=selectedLesson();
      const examples=practiceCatalog?.available&&lesson
        ?practiceCatalog.examples.filter(item=>item.lesson_id===lesson.id)
          .sort((a,b)=>a.ordinal-b.ordinal)
        :[];
      if(!examples.length){
        const option=createElement('option');
        option.value='';option.textContent='No indexed examples';exampleChoice.appendChild(option);
        exampleChoice.disabled=true;playExampleButton.disabled=true;practiceExampleButton.disabled=true;
        renderExample();
        return;
      }
      for(const example of examples){
        const option=createElement('option');
        option.value=example.id;
        option.textContent=`${example.ordinal}. ${example.word} · ${example.pinyin}`;
        exampleChoice.appendChild(option);
      }
      exampleChoice.value=examples[0].id;
      exampleChoice.disabled=false;
      renderExample();
    }
    function populateLessons(){
      stop();revokeAudio();current=null;choice.innerHTML='';image.hidden=true;
      get('lessonPageNumber').textContent='';
      get('previousLessonPage').disabled=true;get('nextLessonPage').disabled=true;
      for(const lesson of catalog.lessons.filter(item=>item.category===category.value)
        .sort((a,b)=>a.id.localeCompare(b.id,undefined,{numeric:true}))){
        const option=createElement('option');option.value=lesson.id;option.textContent=lesson.title;
        choice.appendChild(option);
      }
      if(choice.children.length)choice.value=choice.children[0].value;
      populateExamples();
      status.textContent='Open a complete lesson or choose one of its indexed examples.';
    }
    async function openLesson(){
      stop();revokeAudio();const token=generation;
      current=catalog.lessons.find(item=>item.id===choice.value);
      if(!current){report(new Error('Choose an available lesson'));return;}
      loading=true;get('openLesson').disabled=true;image.hidden=true;
      status.textContent='Verifying the complete recording and accompanying book...';
      try{
        if(!bookVerified){await bytesFor(catalog.book);bookVerified=true;}
        if(token!==generation)return;
        const bytes=await bytesFor(current);
        const gain=await playbackGain(bytes,current.audio_path+':'+current.sha256);
        if(token!==generation)return;
        await showPage(0,token);
        if(token!==generation)return;
        audioURL=URL.createObjectURL(new Blob([bytes],{type:'audio/mpeg'}));
        player.src=audioURL;player.volume=gain;player.playbackRate=1;
        status.textContent=`${current.title}: complete recording, ready to play.${current.category==='consonants'?' Book pages are associated by section order, not an explicit track number.':''}`;
      }catch(error){if(token===generation)report(error);}
      finally{if(token===generation){loading=false;get('openLesson').disabled=false;}}
    }
    async function initialize(){
      try{
        const response=await fetch('../data/glossika_recordings.json');
        if(!response.ok)throw new Error(`Companion catalog failed: HTTP ${response.status}`);
        catalog=validateCatalog(await response.json());
        panel.hidden=!catalog.available;
        if(!catalog.available)return;
        validatePracticeLinks(catalog,practiceCatalog);
        category.value='two-tone';populateLessons();
        category.onchange=populateLessons;
        choice.onchange=()=>{
          stop();revokeAudio();current=null;image.hidden=true;
          get('previousLessonPage').disabled=true;get('nextLessonPage').disabled=true;
          get('lessonPageNumber').textContent='';
          populateExamples();
          status.textContent='Press Open lesson to load this recording and its pages.';
        };
        exampleChoice.onchange=async()=>{
          renderExample();
          try{return await showSelectedExamplePage()}
          catch(error){report(error);return false}
        };
        playExampleButton.onclick=async()=>{
          const example=selectedExample(),runtime=example&&examplesById.get(example.id);
          if(!runtime?.nativeApproval||!playExample){
            status.textContent=`Cannot play this item: ${example?.mapping?.reason||example?.block_reason||'no verified interval'}.`;
            return;
          }
          if(!canPlay()){
            status.textContent='Stop recording your voice before playing an example.';
            return;
          }
          try{
            if(!await showSelectedExamplePage())return false;
            await playExample(runtime,example);
            return true;
          }catch(error){report(error);return false}
        };
        practiceExampleButton.onclick=async()=>{
          const example=selectedExample(),runtime=example&&examplesById.get(example.id);
          if(!runtime||!practiceExample||!canPracticeExample(example)){
            status.textContent=`This item is listen-only: ${example?.block_reason||'graded comparison coverage is incomplete'}.`;
            return;
          }
          try{
            if(!await showSelectedExamplePage())return false;
            return await practiceExample(runtime,example);
          }catch(error){report(error);return false}
        };
        get('openLesson').onclick=openLesson;
        get('previousLessonPage').onclick=()=>showPage(pagePosition-1).catch(report);
        get('nextLessonPage').onclick=()=>showPage(pagePosition+1).catch(report);
        get('zoomLessonPage').onclick=()=>{
          const zoomed=get('lessonPageViewport').classList.toggle('zoomed');
          get('zoomLessonPage').setAttribute('aria-pressed',String(zoomed));
        };
        player.onplay=()=>{
          if(!canPlay()){player.pause();status.textContent='Stop recording your voice before playing a book lesson.';return;}
          stopOthers();status.textContent=`Playing ${current.title}, with its original book pages.`;
        };
        player.onended=()=>{status.textContent=`${current.title} finished.`;};
        player.onerror=()=>report(new Error('The browser could not decode this complete lesson'));
        panel.ontoggle=()=>{if(!panel.open)stop();};
      }catch(error){panel.hidden=false;get('openLesson').disabled=true;report(error);}
    }
    return {initialize,stop,populateExamples,refreshExample:renderExample,selectedExample};
  }
  return {validateCatalog,validatePracticeLinks,assetsFor,excludedCatalog,createController};
});
