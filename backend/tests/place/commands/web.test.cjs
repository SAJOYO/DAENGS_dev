const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the actual browser script; only DOM and network boundaries are substitutes.
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(x => x.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.text = ''; this.children = nodes; }
  addEventListener() {}
  focus() {}
}

function state(revision, kind = 'cafe') {
  return {
    boundary: 'fixture', model: 'fake', system_prompt: 'short', tools: [], recent: [],
    ui: {
      revision, filters: {kinds: [kind], radius_m: 3000, required: [], preferred: [], any_of: []},
      cards: [{ref: `p${revision}`, name: kind, distance_m: 100, match: {kind}, facts: {parking: true}}],
      selected_ref: `p${revision}`, results_match_filters: true, excluded_places: [], pending_proposal: null,
    },
  };
}

function response(data) { return {ok: true, json: async () => data}; }

async function browser() {
  const elements = new Map();
  const find = selector => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const pending = new Map();
  const context = vm.createContext({
    document: {querySelector: find, createElement: tag => new Element(tag)},
    fetch: async url => url === '/api/state' ? response(state(1)) : new Promise(resolve => pending.set(url, resolve)),
    crypto: {randomUUID: () => 'test-id'}, setInterval: () => 1, clearInterval() {},
  });
  const script = fs.readFileSync(path.resolve(__dirname, '../../../src/daengs_evals/facility_tools/web/app.js'), 'utf8');
  vm.runInContext(script + '\nglobalThis.harness = {send, command, render, getCurrent: () => current};', context);
  await new Promise(setImmediate);
  return {find, pending, ...context.harness};
}

function chat(data, revision = data.ui.revision, result = {}) {
  return {...data, turn: {status: 'ready', revision, answer: 'old answer', model_calls: 2,
    executions: [{result: {changes: {kinds: {before: [], after: ['cafe']}}, ...result}}]}};
}

test('late chat cannot overwrite newer filters, cards or selection', async () => {
  const b = await browser();
  const request = b.send({query: '카페'});
  b.render(state(3, 'restaurant'));
  b.pending.get('/api/chat')(response(chat(state(2))));
  await request;
  assert.equal(b.getCurrent().ui.revision, 3);
  assert.match(b.find('#filters').textContent, /음식점/);
  assert.doesNotMatch(b.find('#filters').textContent, /카페/);
  assert.match(b.find('#places').textContent, /restaurant/);
  assert.match(b.find('#places').children[0].className, /selected/);
  assert.doesNotMatch(b.find('#messages').textContent, /old answer/);
  assert.equal(b.find('#changes').textContent, '');
});

test('same revision chat adds highlights after polling has drawn its state', async () => {
  const b = await browser();
  const request = b.send({query: '카페'});
  b.render(state(2));
  b.pending.get('/api/chat')(response(chat(state(2))));
  await request;
  assert.match(b.find('#filters').children[0].className, /updated/);
  assert.equal(b.find('#changes').textContent, '업종 변경');
});

test('old turn with a fresh state envelope cannot add stale highlights or notice', async () => {
  const b = await browser();
  const request = b.send({query: '다른 곳'});
  b.pending.get('/api/chat')(response(chat(state(3, 'restaurant'), 2, {code: 'no_more_candidates'})));
  await request;
  assert.match(b.find('#filters').textContent, /음식점/);
  assert.doesNotMatch(b.find('#filters').children[0].className, /updated/);
  assert.equal(b.find('#changes').textContent, '');
  assert.equal(b.find('#result-notice').textContent, '');
});

test('exhaustion notice keeps the selected cards through same-revision polling', async () => {
  const b = await browser();
  const cards = b.find('#places').children;
  const request = b.command('next_places', {});
  b.pending.get('/api/command')(response({...state(1), command: {
    status: 'unchanged', code: 'no_more_candidates', changes: {},
  }}));
  await request;
  b.render(state(1));
  assert.equal(b.find('#places').children, cards);
  assert.match(b.find('#places').children[0].className, /selected/);
  assert.match(b.find('#result-notice').textContent, /더 없어요/);
  b.render(state(2, 'restaurant'));
  assert.equal(b.find('#result-notice').textContent, '');
});

test('late manual failure cannot replace a newer change message', async () => {
  const b = await browser();
  const request = b.command('search_places', {});
  b.render(state(3, 'restaurant'), {kinds: {after: ['restaurant']}});
  b.pending.get('/api/command')(response({...state(2), command: {status: 'conflict', changes: {}}}));
  await request;
  assert.equal(b.find('#changes').textContent, '업종 변경');
});
