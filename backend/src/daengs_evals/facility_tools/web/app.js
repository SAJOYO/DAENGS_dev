const $ = (selector) => document.querySelector(selector);
const kinds = {cafe: '카페', restaurant: '음식점', hospital: '동물병원', pharmacy: '동물약국'};
let current = null, lastRequest = null, lastTurn = null, busy = false, manualBusy = false;
let displayedRevision = -1;
const manualDirty = new Set();

function node(tag, text = '', className = '') {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '요청을 처리하지 못했어요.');
  return result;
}

function bubble(role, text, error = false) {
  const element = node('div', '', `message ${role}${error ? ' error' : ''}`);
  element.append(node('small', role === 'user' ? '나' : 'DAENGS'), node('div', text));
  $('#messages').append(element);
  $('#messages').scrollTop = $('#messages').scrollHeight;
}

function atomLabel(atom) {
  if (atom.attribute === 'operations.parking') return atom.value ? '주차 필수' : '주차 불가';
  if (atom.attribute === 'pet_access.exclusive') return atom.value ? '반려동물 전용' : '전용 시설 제외';
  return (atom.value || []).map((value) => kinds[value] || value).join(' · ');
}

function chips(filters, container, changes = {}) {
  const values = filters.kinds.map((value) => ['kinds', kinds[value] || value]);
  values.push(['radius_m', `검색 중심에서 ${filters.radius_m / 1000}km`]);
  filters.required.forEach((atom) => values.push(['required', atomLabel(atom)]));
  filters.preferred.forEach(() => values.push(['preferred', '주차 가능 우선']));
  filters.any_of.forEach((branch, index) => values.push(['any_of', `${index ? '또는 ' : ''}${branch.map(atomLabel).join(' + ')}`]));
  if (filters.name_query) values.push(['name_query', `이름: ${filters.name_query}`]);
  container.replaceChildren(...values.map(([key, text]) => node('span', text, `chip${changes[key] ? ' updated' : ''}`)));
}

function render(data, changes = {}, notice) {
  if (current && data.ui.revision < current.ui.revision) return false;
  current = data;
  $('#boundary').textContent = data.boundary;
  $('#model').textContent = `${data.model} · 실험 화면의 수동 조작과 같은 명령 계층 · 회원 찜·운영 Redis 미연결`;
  $('#prompt-title').textContent = `시스템 지침 · ${data.system_prompt.length}자`;
  $('#prompt').textContent = data.system_prompt;
  $('#tools-title').textContent = `사용 가능한 도구 · ${data.tools.length}개`;
  $('#tools').textContent = JSON.stringify(data.tools, null, 2);
  const ui = data.ui;
  if (!manualDirty.has('category')) {
    const category = ui.filters.kinds.length === 1 ? ui.filters.kinds[0] : [...ui.filters.kinds].sort().join(',') === 'cafe,restaurant' ? 'both' : 'current';
    $('#category').value = category;
  }
  if (!manualDirty.has('parking')) {
    const parking = ui.filters.required.find((atom) => atom.attribute === 'operations.parking');
    $('#parking').value = parking ? (parking.value ? 'required' : 'forbidden') : ui.filters.preferred.length ? 'preferred' : 'clear';
  }
  $('#count').textContent = `${ui.cards.length}곳`;
  $('#outdated').hidden = ui.results_match_filters;
  if (ui.revision !== displayedRevision) {
    displayedRevision = ui.revision;
    chips(ui.filters, $('#filters'), changes);
    $('#changes').textContent = '';
    $('#result-notice').textContent = '';
    $('#places').replaceChildren();
    ui.cards.forEach((place, index) => {
      const card = node('article', '', `place${ui.selected_ref === place.ref ? ' selected' : ''}`);
      card.append(node('div', `${index + 1} · ${kinds[place.match.kind] || place.match.kind} · ${place.distance_m}m`, 'meta'));
      card.append(node('h3', place.name));
      const parking = place.facts.parking === true ? '가능' : place.facts.parking === false ? '불가' : '정보 없음';
      card.append(node('div', `주차 ${parking} · 등록 정보 기준`, 'facts'));
      const actions = node('div', '', 'actions');
      const select = node('button', ui.selected_ref === place.ref ? '선택됨' : '선택');
      select.addEventListener('click', () => command('select_place', {place_ref: place.ref}));
      const exclude = node('button', '제외');
      exclude.addEventListener('click', () => command('set_place_excluded', {place_refs: [place.ref], excluded: true}));
      actions.append(select, exclude); card.append(actions); $('#places').append(card);
    });
    if (!ui.cards.length) $('#places').append(node('p', '이 조건에 맞는 후보가 더 없어요.', 'fixture-note'));
    $('#excluded-list').replaceChildren(...ui.excluded_places.map((place) => {
      const button = node('button', `${place.name} 다시 포함`);
      button.addEventListener('click', () => command('set_place_excluded', {place_refs: [place.ref], excluded: false}));
      return button;
    }));
  }
  if (Object.keys(changes).length) {
    // Polling may have rendered this revision before the chat response arrived.
    chips(ui.filters, $('#filters'), changes);
    const names = {kinds: '업종 변경', required: '필수 조건 변경', preferred: '우선 조건 변경', radius_m: '검색 반경 변경', name_query: '이름 조건 변경', selection: '장소 선택', excluded: '제외 상태 변경', known: '아는 장소 반영', any_of: '대안 조건 변경'};
    $('#changes').textContent = Object.keys(changes).map((key) => names[key] || key).join(' · ');
  }
  if (notice !== undefined) $('#result-notice').textContent = notice;
  $('#proposal').hidden = !ui.pending_proposal;
  if (ui.pending_proposal) {
    chips(ui.pending_proposal.filters, $('#proposed-filters'));
    $('#accept').textContent = ui.pending_proposal.apply_to === 'filters_only' ? '조건만 변경' : '이 조건으로 검색';
    $('#unavailable').textContent = ui.pending_proposal.unavailable.length ? `확인할 수 없는 조건: ${ui.pending_proposal.unavailable.join(', ')}` : '아직 적용되지 않은 제안이에요.';
  }
  return true;
}

function resultNotice(results) {
  return results.some((result) => result.code === 'no_more_candidates')
    ? '아직 안 보여준 후보는 더 없어요. 보고 있던 장소는 그대로 유지했어요.' : '';
}

async function command(name, arguments_, formEdit = false) {
  if (!current || manualBusy) return;
  manualBusy = true;
  try {
    const data = await api('/api/command', {request_id: crypto.randomUUID(), expected_revision: current.ui.revision, name, arguments: arguments_});
    if (data.ui.revision < current.ui.revision) return;
    if (formEdit && ['applied', 'unchanged', 'empty'].includes(data.command.status)) manualDirty.clear();
    render(data, data.command.changes, resultNotice([data.command]));
    if (['failed', 'conflict', 'unsupported'].includes(data.command.status)) $('#changes').textContent = '화면이 바뀌었거나 요청을 적용하지 못했어요.';
  } catch (error) { $('#changes').textContent = error.message; }
  finally { manualBusy = false; }
}

function working(value) {
  busy = value;
  $('#send').disabled = value;
  $('#query').disabled = value;
  $('#retry').hidden = true;
  $('#status').textContent = value ? '요청한 도구를 실행하고 있어요…' : 'Enter로 전송 · Shift+Enter로 줄바꿈';
}

async function send(request, retry = false) {
  if (busy) return;
  working(true);
  if (!retry) bubble('user', request.query);
  const poll = setInterval(async () => { try { render(await api('/api/state')); } catch {} }, 750);
  try {
    const data = await api('/api/chat', request);
    const turn = data.turn;
    lastTurn = turn;
    const results = turn.executions.map((execution) => execution.result);
    const isCurrentTurn = turn.revision === data.ui.revision;
    const changes = isCurrentTurn ? Object.assign({}, ...results.map((result) => result.changes)) : {};
    render(data, changes, isCurrentTurn ? resultNotice(results) : undefined);
    $('#executions').textContent = JSON.stringify(turn.executions, null, 2);
    $('#latency').textContent = `${((turn.latency_ms || 0) / 1000).toFixed(1)}초 · 모델 ${turn.model_calls}회`;
    $('#raw-trace').hidden = true;
    if (turn.status === 'ready' && turn.revision === current.ui.revision) bubble('assistant', turn.answer);
    else {
      const message = turn.status === 'conflict' ? '화면이 바뀌어서 이전 요청을 멈췄어요.' : '답변을 끝내지 못했어요. 적용된 결과는 오른쪽에서 확인할 수 있어요.';
      bubble('assistant', message, true);
    }
  } catch (error) { bubble('assistant', error.message, true); }
  finally {
    clearInterval(poll); working(false); $('#query').focus();
    $('#retry').hidden = !lastTurn || !['provider_error', 'timeout'].includes(lastTurn.status);
  }
}

$('#chat').addEventListener('submit', (event) => {
  event.preventDefault();
  const query = $('#query').value.trim();
  if (!query || !current || busy) return;
  lastRequest = {request_id: crypto.randomUUID(), expected_revision: current.ui.revision, query};
  $('#query').value = ''; send(lastRequest);
});
$('#query').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#chat').requestSubmit(); }
});
$('#examples').addEventListener('click', (event) => { if (event.target.tagName === 'BUTTON' && !busy) { $('#query').value = event.target.textContent; $('#query').focus(); } });
['category', 'parking'].forEach((id) => $(`#${id}`).addEventListener('change', () => manualDirty.add(id)));
$('#manual').addEventListener('click', () => {
  const changes = {};
  if (manualDirty.has('category') && $('#category').value !== 'current') changes.category = {operation: 'set', values: $('#category').value === 'both' ? ['cafe', 'restaurant'] : [$('#category').value]};
  if (manualDirty.has('parking')) changes.parking = $('#parking').value;
  command('search_places', changes, true);
});
$('#next').addEventListener('click', () => command('next_places', {}));
$('#refresh').addEventListener('click', () => command('search_places', {}));
$('#accept').addEventListener('click', () => command('resolve_search_proposal', {accept: true}));
$('#reject').addEventListener('click', () => command('resolve_search_proposal', {accept: false}));
$('#retry').addEventListener('click', () => { if (lastRequest) send(lastRequest, true); });
$('#load-trace').addEventListener('click', async () => {
  if (!lastTurn) return;
  try { $('#raw-trace').textContent = JSON.stringify(await api(`/api/trace/${lastTurn.request_id}`), null, 2); $('#raw-trace').hidden = false; }
  catch (error) { $('#raw-trace').hidden = false; $('#raw-trace').textContent = error.message; }
});
api('/api/state').then((data) => {
  render(data);
  data.recent.forEach((turn) => { bubble('user', turn.query); bubble('assistant', turn.answer); });
  if (!data.recent.length) bubble('assistant', '어떤 곳을 찾아드릴까요, 멍? 🐾');
}).catch((error) => bubble('assistant', error.message, true));
