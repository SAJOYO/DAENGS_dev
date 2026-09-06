import {kinds,categorySymbol,keyOf,canonical,snapshots,receiveProfiles,localDate,dogLabel,validateAi,validateAction,validateDogs,LatestSearch} from './state.mjs';
const $=id=>document.getElementById(id);
const add=(parent,tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;parent.append(n);return n;};
const state={ai:false,kind:null,radius:3000,parking:false,center:{lat:37.4979,lng:127.0276},result:null,lens:0,expanded:null,searched:false,liveReady:false,actionBusy:false,actionRetry:null};
let profiles=[],profileOwner=null,profilesReady=false,profileMessage='',snapshotDate=localDate();
const profilePending=new LatestSearch();
const selectedDogs=()=>profilesReady?snapshots(profiles,snapshotDate):[];
const pending=new LatestSearch();
let map,markers,circle;
if(window.L){
  map=L.map('map',{zoomControl:false}).setView([state.center.lat,state.center.lng],14);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap contributors'}).on('tileerror',()=>{$('map-warning').hidden=false;}).addTo(map);
  markers=L.layerGroup().addTo(map);
}else $('map-warning').hidden=false;

function message(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
function clearResults(){state.result=null;state.expanded=null;$('cards').replaceChildren();$('directions').replaceChildren();$('continuation-actions').replaceChildren();$('signals').replaceChildren();$('notices').replaceChildren();$('count').textContent='검색 전';markers?.clearLayers();circle?.remove();circle=null;}
function invalidate(note='조건이 바뀌었어요. 검색을 실행해 주세요.'){pending.cancel();state.actionBusy=false;state.actionRetry=null;clearResults();$('retry').hidden=true;message(note);$('search-form').removeAttribute('aria-busy');}
function mode(ai){state.ai=ai;$('ai').setAttribute('aria-pressed',String(ai));$('query').placeholder=ai?'원하는 동반 조건':'장소명 검색';$('mode-note').textContent=ai?'AI가 해석한 조건과 적용하지 못한 조건을 함께 보여줘요.':'장소명으로 검색해요.';invalidate();}
function updateSource(){invalidate();$('trace').textContent='아직 검색하지 않았어요.';state.searched=false;profiles=[];profileOwner=null;profilesReady=false;const fixture=$('source').value==='fixture';$('data-badge').textContent=fixture?'저장 표본 · AI/DB 호출 없음':'실제 서버';$('connection').textContent=fixture?'저장 표본과 고정 해석을 사용해요. 반려견 표본도 기존 프로필 형식으로 함께 불러와요.':state.liveReady?'개발 서버 설정이 있어요. 같은 계정의 기존 반려견 목록을 불러와요.':'개발 API 연결 설정이 필요해요. 저장 표본 모드에서 화면과 계약을 먼저 검증할 수 있어요.';loadProfiles();}
function renderCategories(){const root=$('categories');root.replaceChildren();for(const [id,label] of [['','전체보기'],...Object.entries(kinds)]){const b=add(root,'button');b.type='button';b.setAttribute('aria-label',label);add(b,'span',categorySymbol(id),'category-icon').setAttribute('aria-hidden','true');add(b,'span',label,'category-name');b.setAttribute('aria-pressed',String(state.kind===(id||null)));b.onclick=()=>{state.kind=id||null;renderCategories();changed();};}}
function changed(){const run=state.searched;invalidate();if(run)submit();}

async function post(path,body,signal){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal});let data;try{data=await response.json();}catch{throw Error('검색 서버에서 올바른 응답을 받지 못했어요.');}if(!response.ok){let msg=typeof data.detail==='string'?data.detail:data.detail?.message;if(response.status===401)msg='로그인이 만료됐어요. 개발 계정 토큰을 갱신해 주세요.';if(!msg)msg=`검색 요청을 처리하지 못했어요. (${response.status})`;throw Object.assign(Error(msg),{status:response.status});}return data;}
async function submit(){
  state.actionRetry=null;state.actionBusy=false;
  if(!profilesReady&&profiles.some(d=>d.selected)){invalidate('반려견 목록을 확인한 뒤 다시 검색해 주세요.');return;}
  if(state.ai&&!$('query').value.trim()){invalidate('원하는 장소를 문장으로 입력해 주세요.');return;}
  const ticket=pending.start();clearResults();$('retry').hidden=true;message(state.ai?'문장을 해석하고 시설을 찾고 있어요…':'시설을 찾고 있어요…');$('search-form').setAttribute('aria-busy','true');state.searched=true;
  const ai=state.ai;const source=$('source').value;const dogs=selectedDogs();
  const spatial={...state.center,radius_m:state.radius};
  const common={dogs,preferences:{parking:state.parking}};
  let request;
  const deadline=setTimeout(()=>pending.current(ticket.version)&&pending.controller?.abort('timeout'),60000);
  try{
    if(ai){
      request={client_request_id:crypto.randomUUID(),query:$('query').value,spatial,kinds:state.kind?[state.kind]:[],...common};
      const data=validateAi(await post(`/api/${source}/ai`,request,ticket.signal),request);
      if(!pending.current(ticket.version))return;
      state.result=data;
    }else{
      const chosen=state.kind?[state.kind]:Object.keys(kinds);
      request={...spatial,kinds:chosen,name_query:$('query').value.trim(),limit_per_kind:50,...common};
      const groups=[];
      // Same six-kind batching as the app; never silently claim partial success.
      for(let i=0;i<chosen.length;i+=6){const response=await post(`/api/${source}/normal`,{...request,kinds:chosen.slice(i,i+6)},ticket.signal);validateDogs(response,dogs);if((response.name_query||'')!==request.name_query)throw Error('이름 검색을 지원하지 않는 서버 응답이에요.');groups.push(...response.groups);}
      if(!pending.current(ticket.version))return;
      state.result={outcome:groups.some(g=>g.results.length)?'results':'empty',request,lenses:[{id:'normal',label:'일반 검색',note:'',applied:{kinds:chosen,parking:common.preferences.parking},search:{groups,dogs},presentations:[]}],signals:[],notices:[]};
    }
    state.lens=0;
    $('trace').textContent=JSON.stringify({request,response:state.result},null,2);
    const labels={results:'검색 결과를 확인해 주세요.',empty:'이 범위에서 결과를 찾지 못했어요.',needs_clarification:'검색 조건을 더 구체적으로 알려 주세요.',unsupported:'현재 지원하지 않는 검색 조건이 있어요.'};
    message(labels[state.result.outcome]||'검색 결과를 확인해 주세요.');render();
  }catch(error){if(!pending.current(ticket.version))return;clearResults();message(ticket.signal.aborted?'검색 시간이 초과됐어요. 다시 시도해 주세요.':error.message,true);$('retry').hidden=false;$('trace').textContent=JSON.stringify({request,error:error.message},null,2);}
  finally{clearTimeout(deadline);if(pending.current(ticket.version))$('search-form').removeAttribute('aria-busy');}
}

async function applyAction(choice, retry=null){
  if(state.actionBusy||!state.result||!state.ai)return;
  const previous=retry?.previous||state.result;
  const action=retry?.action||{client_request_id:crypto.randomUUID(),search_id:previous.search_id,expected_revision:previous.revision,action:choice};
  const source=$('source').value,ticket=pending.start();
  state.actionBusy=true;state.actionRetry=null;$('retry').hidden=true;render();message('선택한 조건으로 이어서 검색하고 있어요…');
  $('search-form').setAttribute('aria-busy','true');
  const deadline=setTimeout(()=>pending.current(ticket.version)&&pending.controller?.abort('timeout'),60000);
  try{
    const data=validateAction(await post(`/api/${source}/actions`,action,ticket.signal),action,previous);
    if(!pending.current(ticket.version))return;
    state.result=data;state.lens=Math.max(0,data.lenses.findIndex(l=>l.id===previous.lenses[state.lens]?.id));state.expanded=null;
    $('trace').textContent=JSON.stringify({action,request:data.request,response:data},null,2);
    message(data.outcome==='results'?'선택한 조건의 검색 결과예요.':data.outcome==='empty'?'선택한 조건으로 찾은 결과가 없어요.':'추가 검색 조건을 확인해 주세요.');
  }catch(error){
    if(!pending.current(ticket.version))return;
    message(ticket.signal.aborted?'응답을 확인하지 못했어요. 다시 시도해 주세요.':error.message,true);
    if([409,410].includes(error.status)){clearResults();state.actionRetry=null;}
    else state.actionRetry={action,previous};
    $('retry').hidden=error.status===422;
  }finally{
    clearTimeout(deadline);
    if(pending.current(ticket.version)){state.actionBusy=false;$('search-form').removeAttribute('aria-busy');render();}
  }
}

function render(){
  const data=state.result;if(!data)return;
  $('notices').replaceChildren();for(const notice of data.notices)add($('notices'),'p',notice.message);
  $('signals').replaceChildren();for(const signal of data.signals){
    add($('signals'),'p',`${signal.label} · ${signal.options.find(o=>o.id===signal.selected_option_id)?.note||signal.note}`);
    for(const option of signal.options){
      const selected=signal.selected_option_id===option.id;
      const b=add($('signals'),'button',option.label+(selected?' ✓':option.availability==='unavailable'?' (미지원)':''));
      b.disabled=state.actionBusy||option.availability!=='proxy'||signal.state==='resolved';b.title=option.note;b.setAttribute('aria-pressed',String(selected));
      if(option.availability==='unavailable')add($('signals'),'p',option.note);
      b.onclick=()=>applyAction({type:'refine',signal_id:signal.id,option_id:option.id});
    }
  }
  $('directions').replaceChildren();data.lenses.forEach((lens,index)=>{const b=add($('directions'),'button',lens.label);b.setAttribute('aria-pressed',String(index===state.lens));b.onclick=()=>{state.lens=index;state.expanded=null;render();};});
  const lens=data.lenses[state.lens];const hits=lens?.search.groups.flatMap(g=>g.results)||[];
  $('continuation-actions').replaceChildren();
  if(state.ai&&lens&&data.revision){const confirmed=data.confirmed_lens_id===lens.id;const b=add($('continuation-actions'),'button',confirmed?'선택한 검색 방향 ✓':'이 방향으로 검색');b.disabled=confirmed||state.actionBusy;b.onclick=()=>applyAction({type:'confirm',lens_id:lens.id});}
  const unique=[...new Map(hits.map(h=>[keyOf(h.place),h])).values()];
  $('count').textContent=`${unique.length}곳${lens?.search.groups.some(g=>g.truncated)?' · 일부 표시':''}`;
  if(lens&&state.ai){add($('notices'),'p',`적용: ${lens.applied.kinds.map(k=>kinds[k]||k).join(' · ')}${lens.applied.parking?' / 주차 우선':''}`);if(lens.note)add($('notices'),'p',lens.note);}
  $('cards').replaceChildren();unique.forEach(hit=>renderCard(hit,lens));renderMap(unique);
}

function renderCard(hit,lens){
  const p=hit.place,key=keyOf(p),f=p.facts||{},access=f.pet_access;
  const allowed=access?.dog_ok===false?false:access?.allowed;
  const label=allowed===true?'동반 가능 등록':allowed===false?'동반 불가 등록':'동반 여부 확인 필요';
  const card=add($('cards'),'article',undefined,'card'+(state.expanded===key?' selected':''));card.dataset.key=key;
  const head=add(card,'button',undefined,'card-head');head.setAttribute('aria-expanded',String(state.expanded===key));head.setAttribute('aria-label',`${p.name} 상세 ${state.expanded===key?'접기':'펼치기'}`);
  add(head,'span',allowed===true?'✓':allowed===false?'×':'?','access '+(allowed===true?'yes':allowed===false?'no':''));const title=add(head,'div');add(title,'strong',p.name);add(title,'div',label,'meta');
  head.onclick=()=>{state.expanded=state.expanded===key?null:key;render();if(state.expanded)map?.panTo([p.lat,p.lng]);};
  add(card,'div',`${kinds[p.match.kind]||p.match.kind} · ${p.distance_m>=1000?(p.distance_m/1000).toFixed(1)+'km':p.distance_m+'m'} · ${p.key.source}`,'meta');
  add(card,'div',f.address||'주소 정보 없음','meta');
  add(card,'div',f.parking===true?'주차 가능 등록':f.parking===false?'주차 불가 등록':'주차 정보 확인 필요','fact');
  if(state.expanded!==key)return;
  for(const d of selectedDogs()){const e=(hit.evaluations?.dogs||[]).find(v=>v.ref===d.ref);const name=profiles.find(v=>v.id===d.ref)?.name||d.ref;add(card,'div',`${name} · ${e?dogLabel(e):'평가 정보 없음'}`,'dog-evaluation');}
  const presentation=lens.presentations?.find(item=>`${item.place_key.source}:${item.place_key.ref}`===key);
  for(const item of [...(presentation?.core_items||[]),...(presentation?.promoted_items||[])])add(card,'p',`${item.label} · ${item.display_text}`,'fact');
  const detail=add(card,'details');add(detail,'summary','시설 조건·출처 확인');
  if(f.restrictions?.raw)add(detail,'p',f.restrictions.raw);
  for(const chip of f.restrictions?.chips||[])add(detail,'p',typeof chip==='string'?chip:chip.label||chip.raw||JSON.stringify(chip));
  if(access?.raw)for(const [k,v] of Object.entries(access.raw))if(v)add(detail,'p',`${({size:'크기',allowed:'동반',exclusive:'전용',restrictions:'제한'})[k]||k}: ${v}`);
  for(const item of presentation?.detail_items||[])add(detail,'p',`${item.label} · ${item.display_text}`);
  for(const item of presentation?.notices||[])add(detail,'p',item.message);
  add(detail,'p',`출처 ${p.key.source} · ${p.key.ref}`);
  if(f.hours_text)add(detail,'p',`운영시간: ${f.hours_text}`);
  const actions=add(card,'div',undefined,'actions');
  if(f.phone&&/^[0-9+()\s-]+$/.test(f.phone)){const a=add(actions,'a','전화');a.href='tel:'+f.phone.replace(/[()\s]/g,'');}
  const link=add(actions,'a','지도에서 보기');link.href=`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.lat+','+p.lng)}`;link.target='_blank';link.rel='noopener noreferrer';
}
function renderMap(hits){if(!map)return;markers.clearLayers();circle?.remove();circle=L.circle([state.center.lat,state.center.lng],{radius:state.radius,color:'#db8790',weight:1,fillOpacity:.03}).addTo(map);hits.forEach((h,i)=>{const p=h.place;const marker=L.marker([p.lat,p.lng],{icon:L.divIcon({className:'',html:`<div class="marker">${i+1}</div>`,iconSize:[27,27],iconAnchor:[13,13]})}).addTo(markers);const text=document.createElement('span');text.textContent=p.name;marker.bindPopup(text);marker.on('click',()=>{state.expanded=keyOf(p);render();const card=[...$('cards').children].find(n=>n.dataset.key===keyOf(p));card?.scrollIntoView({block:'nearest',inline:'center'});});});}

$('search-form').onsubmit=e=>{e.preventDefault();submit();};$('ai').onclick=()=>mode(!state.ai);$('query').oninput=()=>{state.searched=false;invalidate('검색어를 입력한 뒤 검색해 주세요.');};
$('source').onchange=updateSource;$('parking').onclick=()=>{state.parking=!state.parking;$('parking').textContent=state.parking?'주차 우선 ✓':'주차 우선';$('parking').setAttribute('aria-pressed',String(state.parking));changed();};$('retry').onclick=()=>state.actionRetry?applyAction(null,state.actionRetry):submit();
$('where').onchange=()=>{const [lat,lng]=$('where').value.split(',').map(Number);state.center={lat,lng};map?.setView([lat,lng],14);changed();};
$('here').onclick=()=>{if(map){const c=map.getCenter();if(c.lat<32||c.lat>40||c.lng<123||c.lng>133){invalidate('현재 검색을 지원하는 지도 범위를 벗어났어요.');return;}state.center={lat:c.lat,lng:c.lng};}submit();};
$('locate').onclick=()=>{if(!navigator.geolocation){message('현재 위치 기능을 사용할 수 없어요.',true);return;}navigator.geolocation.getCurrentPosition(p=>{const {latitude:lat,longitude:lng}=p.coords;if(lat<32||lat>40||lng<123||lng>133){message('현재 위치는 지원 지역 밖이에요.',true);return;}state.center={lat,lng};map?.setView([lat,lng],14);invalidate('현재 위치로 이동했어요. 검색을 실행해 주세요.');},()=>message('위치 권한을 확인하거나 지도에서 위치를 선택해 주세요.',true));};
document.querySelectorAll('[data-example]').forEach(b=>b.onclick=()=>{mode(true);$('query').value=b.dataset.example;submit();});
function renderProfiles(){
  $('dogs-button').textContent='🐾 '+(profiles.filter(d=>d.selected).map(d=>d.name).join('·')||'반려견')+' ▾';
  $('profile-message').textContent=profileMessage;
  $('dog-inputs').replaceChildren();
  for(const dog of profiles){
    const label=add($('dog-inputs'),'label',undefined,'dog-row');
    const checked=add(label,'input');checked.type='checkbox';checked.checked=dog.selected;checked.disabled=!profilesReady;checked.dataset.ref=dog.id;
    add(label,'span',dog.name+(profiles.filter(d=>d.name===dog.name).length>1?' · '+dog.id.slice(-4):''));
    checked.onchange=()=>{dog.selected=checked.checked;renderProfiles();changed();};
  }
}
async function loadProfiles(){
  const ticket=profilePending.start(),source=$('source').value;
  const before=canonical(selectedDogs());
  profilesReady=false;profileMessage='반려견을 불러오는 중…';renderProfiles();
  // A refresh cannot leave an old evaluation visible while its profile is unresolved.
  if(profiles.some(d=>d.selected))invalidate('반려견 목록을 확인하고 있어요.');
  try{
    const response=await fetch('/api/'+source+'/profiles',{signal:ticket.signal});
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'반려견 목록을 불러오지 못했어요.');
    if(!profilePending.current(ticket.version))return;
    profiles=receiveProfiles(profiles,data,profileOwner===data.owner_context);
    profileOwner=data.owner_context;profilesReady=true;snapshotDate=localDate();
    profileMessage=profiles.length?(source==='fixture'?'저장 표본의 반려견 목록이에요.':''):'함께 갈 반려견을 프로필에 등록해 주세요.';
    renderProfiles();
    if(before!==canonical(selectedDogs())||(state.searched&&!state.result&&profiles.some(d=>d.selected)))changed();
  }catch(error){
    if(!profilePending.current(ticket.version))return;
    profilesReady=false;profileMessage=error.message;renderProfiles();
    if(profiles.some(d=>d.selected))invalidate('반려견 목록을 확인하지 못했어요. 목록을 새로고침해 주세요.');
  }
}
$('dogs-button').onclick=()=>{renderProfiles();$('dogs-dialog').showModal();if($('source').value==='live')loadProfiles();};
$('refresh-profiles').onclick=loadProfiles;
function showFilters(){
  $('radius-options').replaceChildren();
  for(const radius of [1000,3000,5000,10000,20000]){
    const b=add($('radius-options'),'button',radius/1000+'km'+(state.radius===radius?' ✓':''));b.type='button';b.setAttribute('aria-label',radius/1000+'km');
    b.onclick=()=>{state.radius=radius;$('radius').textContent=radius/1000+'km ▾';$('filters-dialog').close();changed();};
  }
  $('filters-dialog').showModal();
}
$('radius').onclick=showFilters;$('filters-button').onclick=showFilters;
renderCategories();
fetch('/api/config').then(r=>r.json()).then(c=>{state.liveReady=c.live_ready;updateSource();}).catch(()=>{$('connection').textContent='검증 서버 설정을 불러오지 못했어요.';});
