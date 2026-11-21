import { h, fill, clock, duration, words, probBar } from './dom.js';

function table(caption, heads, rows) {
  return h('div', { class: 'table-wrap' },
    h('table', null,
      h('caption', null, caption),
      h('thead', null, h('tr', null, heads.map((x) => h('th', { scope: 'col' }, x)))),
      h('tbody', null, rows.map((cells) => h('tr', null, cells.map((c) => h('td', null, c == null || c === '' ? '—' : c))))),
    ));
}

const NO_DECISION_ID = 'no id returned by the Decisions API';
const isDecision = (m) => m.job === 'gate' || /decision/i.test(String(m.model || ''));
const isProbs = (v) => v && typeof v === 'object' && !Array.isArray(v) && Object.values(v).some((x) => typeof x === 'number');
const code = (v) => (v == null || v === '' ? null : h('code', null, String(v)));

export function renderEvidence(shell, body, run) {
  const receipts = run.receipts || {};
  const models = Array.isArray(receipts.models) ? receipts.models : [];
  const sources = Array.isArray(receipts.sources) ? receipts.sources : [];
  const rejected = (run.result && Array.isArray(run.result.rejected)) ? run.result.rejected : [];
  const d = run.decision;
  const any = models.length || sources.length || rejected.length || d;
  shell.hidden = !any;
  if (!any) { body.replaceChildren(); return; }

  const parts = [];
  if (d) {
    const conf = typeof d.confidence === 'number' ? d.confidence.toFixed(2) : '—';
    const thr = typeof d.threshold === 'number' ? d.threshold.toFixed(2) : '—';
    parts.push(h('div', { class: 'gate' },
      h('h3', null, 'Target gate'),
      h('p', { class: 'gate-line' },
        h('span', { class: 'gate-model' }, d.model || 'decision model'), ' chose ',
        h('strong', null, words(d.choice) || '—'), ' with confidence ', h('strong', null, conf),
        ` against a threshold of ${thr} — `, h('strong', null, d.pass ? 'passed' : 'did not pass'),
        typeof d.ms === 'number' ? ` · ${duration(d.ms)}` : ''),
      isProbs(d.probabilities) ? probBar(d.probabilities, d.threshold) : null,
      h('p', { class: 'gate-id' }, d.id ? ['Decision id ', code(d.id)] : NO_DECISION_ID),
    ));
  }
  if (models.length) {
    parts.push(table('Model receipts', ['Job', 'Model', 'Response id', 'Kept by the model service', 'Time'],
      models.map((m) => [words(m.job), m.model,
        code(m.id) || (isDecision(m) ? NO_DECISION_ID : 'no id returned'),
        m.retained === false ? 'not retained' : m.retained === true ? (m.delete_error ? 'retained — delete failed' : 'retained') : null,
        duration(m.ms)])));
  }
  if (sources.length) {
    parts.push(table('Source receipts', ['Source', 'Tool / host', 'Status', 'Read at', 'Time', 'Response hash'],
      sources.map((s) => [words(s.source || s.job), s.tool || s.url_host, [words(s.status), s.error ? ` — ${s.error}` : ''].join('') || null,
        clock(s.read_at), duration(s.ms) || null, code(s.response_hash)])));
  }
  if (rejected.length) {
    parts.push(table('Rejection reasons', ['Seller', 'Reason code', 'In words'],
      rejected.map((r) => [(r.offer && (r.offer.seller_name || r.offer.seller)) || null, code(r.reason), r.sentence])));
  }
  fill(body, parts);
}
