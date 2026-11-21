export const $ = (sel, root = document) => root.querySelector(sel);

function append(node, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(node, c);
    else node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(node, children);
  return node;
}

export function fill(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

const NS = 'http://www.w3.org/2000/svg';
const PATHS = {
  check: 'M4 10.5l4 4 8-9',
  cross: 'M5 5l10 10M15 5L5 15',
  dash: 'M5 10h10',
  out: 'M7 13l6-6M8 7h5v5',
  alert: 'M10 4.5v6.5M10 14.6v.4',
};

export function icon(name) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'icon');
  const path = document.createElementNS(NS, 'path');
  path.setAttribute('d', PATHS[name] || PATHS.dash);
  svg.append(path);
  return svg;
}

export function safeUrl(value) {
  if (typeof value !== 'string' || !value) return null;
  try {
    const u = new URL(value, location.origin);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch {
    return null;
  }
}

export function money(cents, currency) {
  if (typeof cents !== 'number' || !Number.isFinite(cents)) return null;
  if (typeof currency !== 'string' || !currency) return `${(cents / 100).toFixed(2)} (currency not stated)`;
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

export function clock(seconds) {
  if (typeof seconds !== 'number' || seconds <= 0) return null;
  const d = new Date(seconds * 1000);
  const text = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  return h('time', { datetime: d.toISOString() }, text);
}

export function duration(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return '';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function words(value) {
  return String(value ?? '').replace(/_/g, ' ');
}

export async function api(path, opts) {
  const res = await fetch(path, { credentials: 'same-origin', ...opts });
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { ok: res.ok, status: res.status, body };
}

export function postJson(path, payload) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  });
}

export function errorText(r, fallback) {
  const b = r && r.body;
  const msg = (b && b.error && b.error.message) || (b && b.detail && b.detail.error && b.detail.error.message) ||
    (b && typeof b.detail === 'string' ? b.detail : null);
  return msg || `${fallback} (HTTP ${r ? r.status : '—'})`;
}

export function prob(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value.toFixed(2) : null;
}

const PROB_ORDER = ['single', 'multiple', 'other'];

export function probBar(probs, threshold) {
  if (!probs || typeof probs !== 'object' || Array.isArray(probs)) return null;
  const keys = [...PROB_ORDER.filter((k) => typeof probs[k] === 'number'),
    ...Object.keys(probs).filter((k) => !PROB_ORDER.includes(k) && typeof probs[k] === 'number')];
  const total = keys.reduce((sum, k) => sum + Math.max(0, probs[k]), 0);
  if (!keys.length || !(total > 0)) return null;
  const text = keys.map((k) => `${words(k)} ${probs[k].toFixed(2)}`).join(' · ');
  const hasTick = typeof threshold === 'number' && threshold > 0 && threshold < 1;
  return h('div', { class: 'pbar-wrap' },
    h('div', { class: 'pbar', role: 'img', 'aria-label': `Decision 1 probabilities: ${text}${hasTick ? `; pass threshold ${threshold.toFixed(2)}` : ''}` },
      keys.map((k, i) => h('span', { class: `pseg pseg-${i}`, style: `width:${(Math.max(0, probs[k]) / total * 100).toFixed(2)}%` })),
      hasTick ? h('span', { class: 'ptick', style: `left:${(threshold * 100).toFixed(2)}%` }) : null),
    h('p', { class: 'pbar-legend' }, text, hasTick ? ` · threshold ${threshold.toFixed(2)}` : ''));
}

export function screenChip(offer) {
  const value = offer && prob(offer.screen_p);
  if (!value) return null;
  const label = 'Decision 1 probability that this listing is the pictured item';
  return h('span', { class: 'screen-chip', title: label, 'aria-label': `${label}: ${value}` }, `Decision 1 · ${value}`);
}
