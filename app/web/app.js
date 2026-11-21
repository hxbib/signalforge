import { $, api, errorText, money } from './dom.js';
import { initCapture } from './capture.js';
import {
  TERMINAL, renderHead, renderOffer, renderIdentity, renderAssumptions, renderQuestion,
  renderProgress, renderWhy, renderCoverage, announcement,
} from './result.js';
import { renderEvidence } from './drawer.js';
import { renderWatch } from './watch.js';

const POLL_MS = 1000;
const view = { capture: $('#capture'), run: $('#run'), shot: $('#shot'), live: $('#live'), conn: $('#conn') };
const state = { runId: null, run: null, token: 0, timer: null, localUrl: null, announced: null, cache: new Map() };

const capture = initCapture({ onImage: submit });

function sync(key, data, render) {
  const sig = JSON.stringify(data ?? null);
  if (state.cache.get(key) === sig) return;
  state.cache.set(key, sig);
  render();
}

function reset() {
  state.token += 1;
  clearTimeout(state.timer);
  state.run = null;
  state.runId = null;
  state.announced = null;
  state.cache.clear();
  view.live.textContent = '';
  view.conn.textContent = '';
  for (const id of ['offer', 'identity', 'assumptions', 'question', 'progress', 'why', 'coverage', 'watch', 'evidence-body', 'shot-note', 'elapsed', 'run-message']) {
    $(`#${id}`).replaceChildren();
  }
  $('#retry').hidden = true;
  $('#evidence').hidden = true;
  $('#evidence').open = false;
  return state.token;
}

function show(which) {
  view.capture.hidden = which !== 'capture';
  view.run.hidden = which !== 'run';
  $('#new-search').hidden = which !== 'run';
  document.body.dataset.view = which;
}

function setLocalImage(blob) {
  if (state.localUrl) URL.revokeObjectURL(state.localUrl);
  state.localUrl = blob ? URL.createObjectURL(blob) : null;
  if (state.localUrl) view.shot.src = state.localUrl;
  else view.shot.removeAttribute('src');
}

function head(eyebrow, title, message) {
  $('#run-eyebrow').textContent = eyebrow;
  $('#run-title').textContent = title;
  $('#run-message').textContent = message || '';
}

function fail(message) {
  clearTimeout(state.timer);
  state.token += 1;
  head('Not searched', 'This photo did not go through', message);
  $('#result').dataset.tone = 'quiet';
  $('#retry').hidden = false;
  view.live.textContent = message;
}

async function submit(blob, note) {
  const token = reset();
  setLocalImage(blob);
  show('run');
  $('#result').dataset.tone = 'working';
  head('Working', 'Sending the photo…', '');
  if (note) $('#shot-note').textContent = `Your note: “${note}”`;
  document.title = 'Searching — SignalForge';
  window.scrollTo(0, 0);
  $('#run-title').focus({ preventScroll: true });

  const form = new FormData();
  form.append('image', blob, blob.name || 'capture.jpg');
  form.append('note', note || '');
  let r;
  try {
    r = await api('/api/search', { method: 'POST', body: form });
  } catch {
    if (token === state.token) fail('The server could not be reached. Check the connection and try again.');
    return;
  }
  if (token !== state.token) return;
  if (!r.ok || !r.body || !r.body.run_id) {
    fail(errorText(r, 'The photo could not be submitted'));
    return;
  }
  state.runId = r.body.run_id;
  history.pushState(null, '', `#run=${encodeURIComponent(state.runId)}`);
  poll(token);
}

async function poll(token) {
  if (token !== state.token || !state.runId) return;
  const again = () => { state.timer = setTimeout(() => poll(token), POLL_MS); };
  let r;
  try {
    r = await api(`/api/search/${encodeURIComponent(state.runId)}`);
  } catch {
    if (token !== state.token) return;
    view.conn.textContent = 'Connection lost — retrying every second.';
    again();
    return;
  }
  if (token !== state.token) return;
  if (r.status === 404) {
    fail('This search is not available in this browser session. Start a new one.');
    return;
  }
  if (!r.ok || !r.body) {
    view.conn.textContent = `${errorText(r, 'The server did not answer')} — retrying.`;
    again();
    return;
  }
  view.conn.textContent = '';
  state.run = r.body;
  render(r.body);
  if (!TERMINAL.has(r.body.state)) again();
}

function refresh() {
  clearTimeout(state.timer);
  state.token += 1;
  poll(state.token);
}

function render(run) {
  const result = run.result || null;
  const done = TERMINAL.has(run.state);
  const actions = { runId: run.run_id, revision: run.revision, refresh };

  if (!state.localUrl && typeof run.image_url === 'string' && run.image_url.startsWith('/') && view.shot.getAttribute('src') !== run.image_url) {
    view.shot.src = run.image_url;
  }
  sync('note', run.note, () => { $('#shot-note').textContent = run.note ? `Your note: “${run.note}”` : ''; });
  sync('head', [run.state, result && result.status, result && result.headline, run.error, !!run.question], () => renderHead(run));
  sync('offer', [result && [result.status, result.price_cents, result.currency, result.basis_label, result.winners, result.item_price_view], run.coverage && run.coverage.checked_at], () => renderOffer($('#offer'), run));
  sync('identity', [run.target, result && result.winners && result.winners[0] && result.winners[0].image_url], () => renderIdentity($('#identity'), run));
  sync('assumptions', run.assumptions, () => renderAssumptions($('#assumptions'), run));
  sync('question', [run.question, run.state, run.revision], () => renderQuestion($('#question'), run, actions));
  renderProgress($('#progress'), run.progress || []);
  $('#elapsed').textContent = typeof run.elapsed_ms === 'number'
    ? `${done ? 'Finished in' : 'Running for'} ${(run.elapsed_ms / 1000).toFixed(1)} s` : '';
  sync('why', result && [result.near_miss, result.rejected, result.leads], () => renderWhy($('#why'), run));
  sync('coverage', run.coverage, () => renderCoverage($('#coverage'), run));
  sync('watch', [run.watch, run.state, result && result.status, run.revision], () => renderWatch($('#watch'), run, actions));
  sync('evidence', [run.receipts, run.decision, result && result.rejected], () => renderEvidence($('#evidence'), $('#evidence-body'), run));

  $('#retry').hidden = !(run.state === 'unavailable' && !result);
  if (done) {
    const key = `${run.run_id}:${run.revision}:${run.state}`;
    if (state.announced !== key) {
      state.announced = key;
      view.live.textContent = announcement(run);
    }
    const price = result && money(result.price_cents, result.currency);
    document.title = `${price ? `${price} · ` : ''}${(result && result.headline) || 'Search finished'} — SignalForge`;
  }
}

function route() {
  const match = /^#run=([^&]+)/.exec(location.hash);
  const id = match ? decodeURIComponent(match[1]) : null;
  if (!id) {
    reset();
    setLocalImage(null);
    show('capture');
    document.title = 'SignalForge — show me the item';
    return;
  }
  if (id === state.runId) return;
  const token = reset();
  setLocalImage(null);
  state.runId = id;
  show('run');
  $('#result').dataset.tone = 'working';
  head('Working', 'Loading this search…', '');
  poll(token);
}

function startOver() {
  history.pushState(null, '', location.pathname + location.search);
  route();
  $('#file').focus();
}
$('#new-search').addEventListener('click', startOver);
$('#retry').addEventListener('click', startOver);
window.addEventListener('popstate', route);
window.addEventListener('hashchange', route);
window.addEventListener('pagehide', () => capture.stopCamera());

route();
