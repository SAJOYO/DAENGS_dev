export const kinds = {cafe:'카페',restaurant:'음식점',hospital:'동물병원',pharmacy:'약국',pet_shop:'펫샵',shopping:'일반 쇼핑',grooming:'미용',boarding:'위탁',travel:'여행지',leisure:'레저',museum:'박물관',gallery:'미술관',arts_center:'문예회관',culture:'문화시설',pension:'펜션',hotel:'호텔',stay:'숙박',etc:'기타'};
export const categorySymbol = kind => ({'':'▦',cafe:'☕',restaurant:'🍽',hospital:'✚',pharmacy:'💊',pet_shop:'🐾',grooming:'✂',shopping:'🛍',etc:'⋯'})[kind]||'⌂';
export const keyOf = p => `${p.key.source}:${p.key.ref}`;
export const canonical = x => JSON.stringify(x, (_, v) => v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))) : v);
export function localDate() {
  const now=new Date();return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
}
export function snapshots(profiles, today=localDate()) {
  return profiles.filter(d=>d.selected && d.farewell_on==null).map(d=>{
    const weight=d.weight_kg==null?null:Number(d.weight_kg);
    const age=d.birth_date_kind==='birthday' && d.birth_date ? (Date.parse(today)-Date.parse(d.birth_date))/86400000/365.2425 : null;
    return {ref:d.id,revision:d.updated_at??null,dog_size:null,dog_weight_kg:weight>0&&weight<=200?weight:null,dog_age_years:age!==null&&age>=0&&age<=40?age:null};
  });
}
// Receive the account's existing PetListResponse, as PlaceProfiles.receive does.
export function receiveProfiles(previous, payload, sameOwner) {
  if(!Array.isArray(payload.pets))throw Error('반려견 목록 응답을 확인할 수 없어요.');
  const selected=new Set(sameOwner?previous.filter(d=>d.selected).map(d=>d.id):[]);
  return payload.pets.filter(d=>d.farewell_on==null).map(d=>({...d,selected:selected.has(d.id)}));
}
export function dogLabel(e) {
  const access={weight_allowed:'체중 조건 충족',weight_exceeded:'체중 제한 초과',weight_boundary_unknown:'체중 경계 기준 확인 필요',size_allowed:'크기 조건 충족',size_exceeded:'크기 제한 불일치',dog_disallowed:'강아지 동반 불가로 등록',missing_dog_size:'크기 정보 확인 필요',missing_dog_weight:'체중 정보 확인 필요'};
  const extra={age_denied:'나이 제한 불일치',size_denied:'추가 크기 제한 불일치',species_denied:'강아지 동반 제한',missing_dog_age:'나이 정보 확인 필요'};
  const reason=e.restrictions?.reason;
  return [access[e.dog_access?.reason]||'시설 조건 확인 필요',e.restrictions && reason!=='no_blocking_condition'?(extra[reason]||'추가 조건 확인 필요'):null].filter(Boolean).join(' · ');
}
export function validateAi(data, request) {
  if(data.contract_version!=='facility-discovery-v1'||canonical(data.request)!==canonical(request)) throw Error('검색 조건과 서버 응답이 일치하지 않아요. 서버 버전을 확인해 주세요.');
  for(const lens of data.lenses) validateDogs(lens.search, request.dogs);
  return data;
}
export function validateDogs(search,dogs) {
  if(canonical(search.dogs||[])!==canonical(dogs)) throw Error('반려견 평가 기준이 요청과 달라요. 서버 버전을 확인해 주세요.');
  if(dogs.length && (!search.evaluated_at || search.groups.some(g=>g.results.some(h=>canonical((h.evaluations?.dogs||[]).map(e=>e.ref))!==canonical(dogs.map(d=>d.ref)))))) throw Error('반려견별 평가가 누락됐어요.');
}
export function validateAction(data, action, previous) {
  validateAi(data,previous.request);
  if(data.search_id!==previous.search_id || data.revision!==previous.revision+1 || canonical(data.action_request)!==canonical(action))throw Error('선택 요청과 검색 응답이 일치하지 않아요. 다시 검색해 주세요.');
  return data;
}
export class LatestSearch {
  version=0; controller=null;
  cancel(){this.version++;this.controller?.abort();this.controller=null;}
  start(){this.cancel();this.controller=new AbortController();return {version:this.version,signal:this.controller.signal};}
  current(v){return v===this.version;}
}
