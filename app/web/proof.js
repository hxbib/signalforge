import { $, h, fill, api, words } from './dom.js';

const EMPTY = 'No evaluation run recorded yet';
const SYSTEM_KEYS = ['full', 'system', 'signalforge'];
const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
const prim = (v) => v === null || ['string', 'number', 'boolean'].includes(typeof v);
const show = (v) => (v === null ? '—' : typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v));

function tree(value) {
  if (prim(value)) return show(value);
  if (isObj(value) && !Object.keys(value).length) return 'none';
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
const OVER_N = ['wrong', 'abstained', 'errors', 'answered', 'false_matches', 'returned_winner', 'false_accepts', 'cheapest_agreement'];
const LABEL = { false_accepts: 'false matches on actual negatives', false_rejects: 'missed matches on actual positives' };

function armCard(name, arm, system, fallbackTotal) {
  const total = typeof arm.n === 'number' ? arm.n : typeof arm.total === 'number' ? arm.total
    : typeof fallbackTotal === 'number' ? fallbackTotal : null;
  const correct = typeof arm.correct === 'number' ? arm.correct : null;
  const headline = correct !== null && total !== null;
  const used = new Set(headline ? ['correct', 'n', 'total'] : []);
  for (const [k, d] of Object.entries(DENOM)) if (typeof arm[k] === 'number' && typeof arm[d] === 'number') used.add(d);
  const rows = Object.entries(arm).filter(([k]) => !used.has(k)).map(([k, v]) => {
    let text = tree(v);
    if (typeof v === 'number' && DENOM[k] && typeof arm[DENOM[k]] === 'number') text = `${v} / ${arm[DENOM[k]]}`;
    else if (typeof v === 'number' && total !== null && OVER_N.includes(k)) text = `${v} / ${total}`;
    else if (typeof v === 'number' && !Number.isInteger(v)) text = String(Math.round(v * 1000) / 1000);
    const label = LABEL[k] && typeof arm[DENOM[k]] === 'number' ? LABEL[k] : words(k);
    if (isObj(v) || Array.isArray(v)) {
      const empty = isObj(v) ? !Object.keys(v).length : !v.length;
      return h('div', { class: 'metric-block' }, h('dt', null, label), h('dd', null, empty ? 'none' : text));
    }
    return h('div', null, h('dt', null, label), h('dd', null, text));
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

const SECTION_TITLE = { e2e: 'End-to-end photo to offer (e2e)' };

function strList(value) {
  return Array.isArray(value) ? value.filter((x) => typeof x === 'string') : typeof value === 'string' ? [value] : [];
}

function runBlock(run, headline) {
  const pair = isObj(run) ? armPair(run.arms) : null;
  if (!pair) return null;
  const flags = [run.in_sample === true ? 'IN-SAMPLE' : null, run.held_out === true ? 'held-out' : null,
    typeof run.draw === 'string' ? `draw ${run.draw}` : null, headline ? 'headline run' : null].filter(Boolean);
  const reading = strList(run.reading);
  return h('div', { class: 'run-block' },
    h('h3', null, `${run.label || run.name || 'run'}${flags.length ? ` — ${flags.join(' · ')}` : ''}${typeof run.n === 'number' ? ` — ${run.n} cases` : ''}`),
    typeof run.baseline_source === 'string' ? h('p', { class: 'proof-note' }, `Baseline source: ${run.baseline_source}`) : null,
    h('div', { class: 'arms' },
      armCard('SignalForge — full system', pair.system, true, run.n),
      armCard('Baseline — one model call', pair.baseline, false, run.n)),
    isObj(run.paired) ? h('details', { class: 'more-detail' }, h('summary', null, 'Paired outcome (same case, both arms)'), tree(run.paired)) : null,
    reading.length ? h('ul', { class: 'files bullets reading' }, reading.map((line) => h('li', null, line))) : null,
  );
}

function armSection(k, v) {
  const pair = isObj(v) ? armPair(v.arms) : null;
  const runs = isObj(v) && Array.isArray(v.runs) ? v.runs.map((r) => runBlock(r, typeof v.headline_run === 'string' && r && r.name === v.headline_run)).filter(Boolean) : [];
  if (!pair && !runs.length) return null;
  const notes = strList(v.notes);
  const shown = new Set(runs.length ? v.runs.flatMap((r) => strList(r && r.reading)) : []);
  const reading = strList(v.reading).filter((line) => !shown.has(line));
  const skip = ['arms', 'notes', 'paired', 'reading', 'runs'];
  const rest = Object.fromEntries(Object.entries(v).filter(([key]) => !skip.includes(key)));
  return h('section', { class: 'panel' },
    h('h2', null, `${SECTION_TITLE[k] || words(k)} — full system vs baseline${!runs.length && typeof v.n === 'number' ? ` — ${v.n} cases` : ''}`),
    runs.length ? runs : [
      h('div', { class: 'arms' },
        armCard('SignalForge — full system', pair.system, true, v.n),
        armCard('Baseline — one model call', pair.baseline, false, v.n)),
      isObj(v.paired) ? [h('h3', null, 'Paired outcome (same case, both arms)'), tree(v.paired)] : null,
    ],
    reading.length ? [h('h3', null, 'How to read it — as recorded'), h('ul', { class: 'files bullets reading' }, reading.map((line) => h('li', null, line)))] : null,
    notes.length ? [h('h3', null, 'How this run was set up — as recorded'), h('ul', { class: 'files bullets reading' }, notes.map((n) => h('li', null, n)))] : null,
    Object.keys(rest).length ? h('details', { class: 'more-detail' }, h('summary', null, 'Details of this run'), tree(rest)) : null,
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
      typeof src.experiment === 'string' ? h('p', { class: 'proof-lede' }, src.experiment) : null,
      typeof src.headline_run === 'string' ? h('p', { class: 'proof-run' }, 'Headline run: ', h('code', null, src.headline_run)) : null,
      isObj(src.freeze) && src.freeze.corpus ? h('p', { class: 'proof-lede' }, String(src.freeze.corpus)) : null,
      h('div', { class: 'arms' },
        armCard('SignalForge — full system', pair.system, true),
        armCard('Baseline — one model call', pair.baseline, false)),
      isObj(src.paired) ? [h('h3', null, 'Paired outcome (same case, both arms)'), tree(src.paired)] : null,
      strata(src.by_stratum),
      typeof src.latency_note === 'string' ? h('p', { class: 'proof-note' }, src.latency_note) : null,
    ));
    const reading = Array.isArray(src.reading) ? src.reading.filter((x) => typeof x === 'string') : typeof src.reading === 'string' ? [src.reading] : [];
    if (reading.length) {
      out.push(h('section', { class: 'panel' },
        h('h2', null, 'Every recorded run, in order'),
        h('ul', { class: 'files bullets reading' }, reading.map((line) => h('li', null, line)))));
    }
    if (isObj(src.experiment_b)) {
      const b = src.experiment_b;
      out.push(h('section', { class: 'panel' },
        h('h2', null, `Photo-to-offer experiment — ${words(b.status || 'status not recorded')}`),
        b.statement ? h('p', { class: 'statement' }, String(b.statement)) : null,
        Array.isArray(b.missing) && b.missing.length ? [h('h3', null, 'Missing'), h('ul', { class: 'files bullets' }, b.missing.map((m) => h('li', null, show(prim(m) ? m : JSON.stringify(m)))))] : null,
      ));
    }
    for (const [k, v] of Object.entries(summary)) {
      if (['arms', 'paired', 'by_stratum', 'experiment_b', 'n', 'reading', 'latency_note', 'experiment', 'headline_run'].includes(k) || v === null) continue;
      const section = armSection(k, v);
      if (section) out.push(section);
      else leftover[k] = v;
    }
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

function caseTable(caption, rows, only) {
  let keys = [];
  for (const r of rows) if (isObj(r)) for (const k of Object.keys(r)) if (!keys.includes(k)) keys.push(k);
  if (only && only.some((k) => keys.includes(k))) keys = only.filter((k) => keys.includes(k));
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
  for (const [k, v] of Object.entries(doc)) {
    if (['generated_from', 'summary', 'cases', 'failures'].includes(k)) continue;
    const section = armSection(k, v);
    if (section) parts.push(section);
  }
  const listed = Array.isArray(doc.failures) ? doc.failures : summary && Array.isArray(summary.failures) ? summary.failures : null;
  const hasArm = cases.some((c) => isObj(c) && typeof c.arm === 'string');
  const headRun = summary && typeof summary.headline_run === 'string' && cases.some((c) => isObj(c) && c.run === summary.headline_run)
    ? summary.headline_run : null;
  const inHead = (c) => !headRun || c.run === headRun;
  const systemCases = cases.filter((c) => isObj(c) && SYSTEM_KEYS.includes(c.arm));
  const bad = (c) => c.correct === false || !!c.error;
  const failures = listed || (hasArm ? systemCases.filter((c) => inHead(c) && bad(c)) : cases.filter(failed));
  const cols = ['id', 'category', 'gold', 'verdict', 'abstained', 'error', 'ms', 'wdc_pair_id'];
  const other = hasArm && headRun && !listed ? systemCases.filter((c) => !inHead(c)) : [];
  const otherRuns = [...new Set(other.map((c) => c.run))];
  parts.push(h('section', { class: 'panel' },
    h('h2', null, hasArm ? `Where the full system failed — ${failures.length} of ${systemCases.filter(inHead).length} cases${headRun ? ` (${headRun})` : ''}`
      : `Failures (${failures.length}${cases.length ? ` of ${cases.length} cases` : ''})`),
    failures.length ? caseTable('Wrong answers, abstentions scored as wrong, and errors', failures, hasArm ? cols : null)
      : h('p', { class: 'proof-lede' }, 'No failing case is marked in the recorded files.'),
    otherRuns.length ? h('details', { class: 'more-detail' },
      h('summary', null, `Failures in the other recorded runs — ${otherRuns.map((r) => `${r}: ${other.filter((c) => c.run === r && bad(c)).length} of ${other.filter((c) => c.run === r).length}`).join(' · ')}`),
      caseTable('Full-system failures in the other runs', other.filter(bad), ['run', ...cols])) : null,
  ));
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
