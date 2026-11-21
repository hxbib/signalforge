import { $, h, fill, api, words } from './dom.js';

const EMPTY = 'No evaluation run recorded yet';
const SYSTEM_KEYS = ['full', 'system', 'signalforge'];
const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
const prim = (v) => v === null || ['string', 'number', 'boolean'].includes(typeof v);
const show = (v) => (v === null ? '—' : typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v));

function tree(value) {
  if (prim(value)) return show(value);
  if (Array.isArray(value)) {
    if (!value.length) return '—';
    if (value.every(prim)) return value.map(show).join(', ');
    return h('ul', { class: 'files' }, value.map((v) => h('li', null, tree(v))));
  }
  return h('dl', { class: 'kv' }, Object.entries(value).map(([k, v]) => h('div', null, h('dt', null, words(k)), h('dd', null, tree(v)))));
}

function armPair(obj) {
  if (!isObj(obj) || !isObj(obj.baseline)) return null;
  const key = SYSTEM_KEYS.find((k) => isObj(obj[k]));
  return key ? { system: obj[key], baseline: obj.baseline, key } : null;
}

const DENOM = { false_accepts: 'actual_negatives', false_rejects: 'actual_positives' };
const OVER_N = ['wrong', 'abstained', 'errors', 'answered', 'false_matches', 'returned_winner'];
const LABEL = { false_accepts: 'false matches on actual negatives', false_rejects: 'missed matches on actual positives' };

function armCard(name, arm, system) {
  const total = typeof arm.n === 'number' ? arm.n : typeof arm.total === 'number' ? arm.total : null;
  const correct = typeof arm.correct === 'number' ? arm.correct : null;
  const headline = correct !== null && total !== null;
  const used = new Set(headline ? ['correct', 'n', 'total'] : []);
  for (const [k, d] of Object.entries(DENOM)) if (typeof arm[k] === 'number' && typeof arm[d] === 'number') used.add(d);
  const rows = Object.entries(arm).filter(([k]) => !used.has(k)).map(([k, v]) => {
    let text = tree(v);
    if (typeof v === 'number' && DENOM[k] && typeof arm[DENOM[k]] === 'number') text = `${v} / ${arm[DENOM[k]]}`;
    else if (typeof v === 'number' && total !== null && OVER_N.includes(k)) text = `${v} / ${total}`;
    else if (typeof v === 'number' && !Number.isInteger(v)) text = String(Math.round(v * 1000) / 1000);
    return h('div', null, h('dt', null, LABEL[k] || words(k)), h('dd', null, text));
  });
  return h('div', { class: `arm${system ? ' arm-system' : ''}` },
    h('p', { class: 'arm-name' }, name),
    headline ? h('p', { class: 'count' }, String(correct), h('small', null, ` / ${total} correct`)) : null,
    rows.length ? h('dl', { class: 'metrics' }, rows) : null,
  );
}

function experiment(title, obj) {
  const pair = armPair(obj);
  if (!pair) return null;
  const others = Object.entries(obj).filter(([k]) => k !== 'baseline' && k !== pair.key);
  return h('section', { class: 'panel' },
    h('h2', null, title),
    h('div', { class: 'arms' },
      armCard('SignalForge — full system', pair.system, true),
      armCard('Baseline — one model call', pair.baseline, false)),
    others.length ? tree(Object.fromEntries(others)) : null,
  );
}

function strata(list) {
  if (!Array.isArray(list) || !list.length) return null;
  const cell = (a) => (isObj(a) && typeof a.correct === 'number' && typeof a.n === 'number' ? `${a.correct} / ${a.n}` : '—');
  return h('div', { class: 'table-wrap' }, h('table', null,
    h('caption', null, 'Correct by stratum'),
    h('thead', null, h('tr', null, ['Stratum', 'Full system', 'Baseline'].map((x) => h('th', { scope: 'col' }, x)))),
    h('tbody', null, list.map((r) => h('tr', null, h('td', null, show(r.stratum ?? null)), h('td', null, cell(r.full || r.system)), h('td', null, cell(r.baseline))))),
  ));
}

function summarySections(summary) {
  const out = [];
  const leftover = {};
  const src = isObj(summary.arms) && armPair(summary.arms) ? summary : null;
  if (src) {
    const pair = armPair(src.arms);
    out.push(h('section', { class: 'panel' },
      h('h2', null, `Full system vs single-call baseline${typeof src.n === 'number' ? ` — ${src.n} cases, same inputs` : ''}`),
      isObj(src.freeze) && src.freeze.corpus ? h('p', { class: 'proof-lede' }, String(src.freeze.corpus)) : null,
      h('div', { class: 'arms' },
        armCard('SignalForge — full system', pair.system, true),
        armCard('Baseline — one model call', pair.baseline, false)),
      isObj(src.paired) ? [h('h3', null, 'Paired outcome (same case, both arms)'), tree(src.paired)] : null,
      strata(src.by_stratum),
    ));
    if (isObj(src.experiment_b)) {
      const b = src.experiment_b;
      out.push(h('section', { class: 'panel' },
        h('h2', null, `Photo-to-offer experiment — ${words(b.status || 'status not recorded')}`),
        b.statement ? h('p', { class: 'statement' }, String(b.statement)) : null,
        Array.isArray(b.missing) && b.missing.length ? [h('h3', null, 'Missing'), h('ul', { class: 'files bullets' }, b.missing.map((m) => h('li', null, show(prim(m) ? m : JSON.stringify(m)))))] : null,
      ));
    }
    for (const [k, v] of Object.entries(summary)) if (!['arms', 'paired', 'by_stratum', 'experiment_b', 'n'].includes(k)) leftover[k] = v;
    if (Object.keys(leftover).length) out.push(h('details', { class: 'panel evidence' }, h('summary', null, 'Run details — freeze record, versions, model ids'), tree(leftover)));
    return out;
  }
  const top = experiment('Measured result', summary);
  if (top) return [top];
  for (const [k, v] of Object.entries(summary)) {
    const section = experiment(words(k), v);
    if (section) out.push(section);
    else leftover[k] = v;
  }
  if (Object.keys(leftover).length) out.push(h('section', { class: 'panel' }, h('h2', null, out.length ? 'Run details' : 'Summary'), tree(leftover)));
  return out;
}

function failed(row) {
  if (!isObj(row)) return false;
  if (row.failure || row.failed === true || row.error) return true;
  return Object.entries(row).some(([k, v]) => v === false && /correct|pass|ok$/.test(k));
}

function caseTable(caption, rows) {
  const keys = [];
  for (const r of rows) if (isObj(r)) for (const k of Object.keys(r)) if (!keys.includes(k)) keys.push(k);
  if (!keys.length) return tree(rows);
  return h('div', { class: 'table-wrap' }, h('table', null,
    h('caption', null, caption),
    h('thead', null, h('tr', null, keys.map((k) => h('th', { scope: 'col' }, words(k))))),
    h('tbody', null, rows.map((r) => h('tr', null, keys.map((k) => h('td', null, r && r[k] !== undefined ? tree(r[k]) : '—'))))),
  ));
}

function render(doc) {
  const summary = isObj(doc.summary) ? doc.summary : null;
  const cases = Array.isArray(doc.cases) ? doc.cases : isObj(doc.cases) ? Object.entries(doc.cases).map(([id, v]) => (isObj(v) ? { id, ...v } : { id, value: v })) : [];
  const files = Array.isArray(doc.generated_from) ? doc.generated_from : [];
  if ((!summary || !Object.keys(summary).length) && !cases.length) return [h('p', { class: 'proof-empty' }, EMPTY)];

  const parts = summary ? summarySections(summary) : [];
  const listed = Array.isArray(doc.failures) ? doc.failures : summary && Array.isArray(summary.failures) ? summary.failures : null;
  const hasArm = cases.some((c) => isObj(c) && typeof c.arm === 'string');
  const failures = listed || (hasArm ? cases.filter((c) => SYSTEM_KEYS.includes(c.arm) && (c.correct === false || c.error)) : cases.filter(failed));
  parts.push(h('section', { class: 'panel' },
    h('h2', null, hasArm ? `Where the full system failed — ${failures.length} of ${cases.filter((c) => SYSTEM_KEYS.includes(c.arm)).length} cases`
      : `Failures (${failures.length}${cases.length ? ` of ${cases.length} cases` : ''})`),
    failures.length ? caseTable('Wrong answers, abstentions scored as wrong, and errors', failures)
      : h('p', { class: 'proof-lede' }, 'No failing case is marked in the recorded files.')));
  if (cases.length) {
    parts.push(h('details', { class: 'panel evidence' }, h('summary', null, `All ${cases.length} recorded cases`), caseTable('Every case', cases)));
  }
  parts.push(h('section', { class: 'panel' },
    h('h2', null, 'Files these numbers came from'),
    files.length ? h('ul', { class: 'files' }, files.map((f) => h('li', null, h('code', null, typeof f === 'string' ? f : JSON.stringify(f)))))
      : h('p', { class: 'proof-lede' }, 'The evaluation endpoint did not list its source files.')));
  return parts;
}

async function main() {
  const body = $('#proof-body');
  let r;
  try {
    r = await api('/api/proof/eval');
  } catch {
    fill(body, h('p', { class: 'proof-empty' }, 'The evaluation record could not be loaded — the server did not answer.'));
    return;
  }
  if (r.status === 404 || (r.ok && !isObj(r.body))) { fill(body, h('p', { class: 'proof-empty' }, EMPTY)); return; }
  if (!r.ok) { fill(body, h('p', { class: 'proof-empty' }, `The evaluation record could not be loaded (HTTP ${r.status}).`)); return; }
  fill(body, render(r.body));
}

main();
