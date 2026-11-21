import { h, fill, icon, postJson, errorText, words } from './dom.js';
import { TERMINAL } from './result.js';

const STATUS = { active: 'Active', preview_only: 'Preview only — not armed', needs_detail: 'Needs one detail', paused: 'Paused' };

function simRow(row) {
  return h('li', { class: 'sim' },
    h('span', { class: 'sim-tag' }, 'simulation'),
    h('span', { class: 'sim-label' }, row.label || ''),
    h('span', { class: `sim-verdict ${row.alerts ? 'sim-yes' : 'sim-no'}` }, icon(row.alerts ? 'check' : 'dash'), row.alerts ? 'Would alert' : 'Stays quiet'),
    row.why ? h('span', { class: 'sim-why' }, row.why) : null);
}

function playground(rows, note) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const first = rows.slice(0, 3);
  const rest = rows.slice(3);
  return h('div', { class: 'playground' },
    h('h3', null, 'Would this alert?'),
    h('p', { class: 'sim-note' }, note || 'Every row below is a simulation, not an observation.'),
    h('ul', { class: 'sims' }, first.map(simRow)),
    rest.length ? h('details', { class: 'sims-more' },
      h('summary', null, `${rest.length} more simulated timeline${rest.length === 1 ? '' : 's'}`),
      h('ul', { class: 'sims' }, rest.map(simRow))) : null,
  );
}

export function renderWatch(root, run, actions) {
  const w = run.watch;
  const offerable = TERMINAL.has(run.state) && run.state !== 'needs_detail' && run.result && run.result.status !== 'needs_identity';
  if (!w && !offerable) { root.replaceChildren(); return; }
  const status = h('p', { class: 'watch-msg', role: 'status' });

  async function send(path, payload, controls, failText) {
    controls.forEach((c) => { c.disabled = true; });
    status.textContent = 'Working…';
    let r;
    try { r = await postJson(path, payload); } catch { r = null; }
    if (r && r.ok) { actions.refresh(); return; }
    controls.forEach((c) => { c.disabled = false; });
    status.textContent = r ? errorText(r, failText) : 'The server could not be reached. Try again.';
  }

  const input = h('input', {
    type: 'text', id: 'watch-command', maxlength: 300, autocomplete: 'off',
    placeholder: 'Optional — e.g. notify me when this seller restocks under $25',
  });
  const go = h('button', { type: 'submit', class: 'btn' }, w ? 'Update watch' : 'Watch this price');
  const form = h('form', { class: 'watch-form', onsubmit: (e) => {
    e.preventDefault();
    const command = input.value.trim();
    send(`/api/search/${encodeURIComponent(run.run_id)}/watch`, command ? { command } : {}, [input, go], 'The watch was not created');
  } },
    h('label', { for: 'watch-command', class: 'visually-hidden' }, 'What to watch for (optional)'),
    input, go);

  function question(watch) {
    const q = watch.question;
    if (!q || !q.text) return null;
    const options = q.kind === 'choice' && Array.isArray(q.options) ? q.options : [];
    const buttons = options.map((o) => h('button', { type: 'button', class: 'btn choice' }, o.label || o.id));
    buttons.forEach((b, i) => b.addEventListener('click', () => send(`/api/search/${encodeURIComponent(run.run_id)}/watch`,
      { command: watch.command || '', answer: { question_id: q.question_id, choice: options[i].id } }, buttons, 'The answer was not accepted')));
    return h('div', { class: 'watch-question' },
      h('p', { class: 'watch-q-text', id: 'watch-q-text' }, q.text),
      buttons.length ? h('div', { class: 'choices', role: 'group', 'aria-labelledby': 'watch-q-text' }, buttons)
        : h('p', { class: 'watch-q-hint' }, 'Answer in your own words in the box below.'));
  }

  const parts = [h('h2', { id: 'watch-title' }, w ? 'Watch' : 'Want to know when this changes?')];
  if (w) {
    const canPause = (w.status === 'active' || w.status === 'preview_only') && typeof w.pause_url === 'string' && w.pause_url.startsWith('/');
    const pause = canPause ? h('button', { type: 'button', class: 'btn' }, w.status === 'active' ? 'Undo — pause this watch' : 'Pause this preview') : null;
    if (pause) pause.addEventListener('click', () => send(w.pause_url, {}, [pause], 'The watch was not paused'));
    parts.push(
      w.scope_sentence ? h('p', { class: 'watch-scope' }, w.scope_sentence) : null,
      h('p', { class: `watch-status watch-${w.status}` }, h('span', { class: 'badge' }, STATUS[w.status] || words(w.status)),
        w.origin ? h('span', { class: 'watch-origin' }, w.origin === 'buyer' ? 'Started by your tap' : `Origin: ${words(w.origin)}`) : null),
      w.preview_reason ? h('p', { class: 'watch-reason' }, w.preview_reason) : null,
      question(w),
      pause,
      playground(w.playground, w.simulation_note),
    );
  } else {
    parts.push(h('p', { class: 'watch-lede' }, 'In-app only and reversible. Nothing is watched unless you ask.'));
  }
  parts.push(form, status);
  fill(root, h('section', { class: `panel watch${w ? ' watch-live' : ''}`, 'aria-labelledby': 'watch-title' }, parts));
}
