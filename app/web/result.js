import { $, h, fill, icon, safeUrl, money, clock, duration, words, postJson, errorText, prob, probBar, screenChip } from './dom.js';

export const TERMINAL = new Set(['complete', 'partial', 'needs_detail', 'unavailable']);

const STATE_TITLE = {
  reading: 'Reading the photo…',
  searching: 'Finding the exact product…',
  verifying: 'Checking merchants…',
  needs_detail: 'One detail needed',
  unavailable: 'No result this time',
  partial: 'Partial result',
  complete: 'Search finished',
};

const OFFER_BASIS = {
  merchant_barcode: 'barcode published by the merchant',
  catalog_product: 'same Shop catalog product',
  model_number: 'brand + model number in the listing',
  visual_match: 'two independent model judgments (Decision 1 photo screen and Carbon)',
  buyer_choice: 'the catalog product you chose',
};
const TARGET_BASIS = {
  barcode: 'barcode read from the photo',
  merchant_barcode: 'barcode published by the merchant',
  catalog_product: 'same Shop catalog product',
  model_number: 'brand + model number printed on the item',
  buyer_choice: 'the catalog product you chose',
  visual_match: 'two independent model judgments',
};
const STOCK = { in_stock: 'In stock', sold_out: 'Sold out', orderable: 'Orderable', unknown: 'Stock unknown' };
const SOURCE = {
  shop_native: 'Shop catalog',
  merchant_api: "the merchant's own storefront",
  merchant_page: "the merchant's own page",
  discovery: 'discovery only',
};
const SOURCE_STATUS = {
  ok: 'responded', failed: 'failed', timeout: 'timed out', limited: 'limited results', not_connected: 'not connected',
};

const sellerName = (o) => (o && (o.seller_name || o.seller)) || 'Unnamed seller';
const stockLabel = (v) => STOCK[v] || words(v) || 'Stock unknown';

export function renderHead(run) {
  const result = run.result;
  const done = TERMINAL.has(run.state);
  const box = $('#result');
  let eyebrow = 'Working';
  let title = (result && result.headline) || STATE_TITLE[run.state] || words(run.state);
  let message = '';
  let tone = 'working';

  if (done) {
    tone = result && Array.isArray(result.winners) && result.winners.length ? 'verified' : 'quiet';
    eyebrow = run.state === 'complete' ? 'Result' : run.state === 'partial' ? 'Partial result — some coverage is missing'
      : run.state === 'needs_detail' ? 'Waiting on one answer' : 'Unavailable';
    if (run.state === 'needs_detail' && tone === 'quiet') title = STATE_TITLE.needs_detail;
    if (run.state === 'unavailable' && !result) title = STATE_TITLE.unavailable;
  } else if (result && result.headline) {
    eyebrow = `${STATE_TITLE[run.state] || words(run.state)}`;
  }
  if (run.error && run.error.message) message = run.error.message;

  box.dataset.tone = tone;
  $('#run-eyebrow').textContent = eyebrow;
  $('#run-title').textContent = title;
  $('#run-message').textContent = message;
}

function confirmedLine(offer) {
  const by = Array.isArray(offer.confirmed_by) ? offer.confirmed_by : [];
  if (!by.length) return h('li', { class: 'fact fact-open' }, icon('dash'), 'Not confirmed by the merchant’s own storefront');
  return h('li', { class: 'fact fact-yes' }, icon('check'), `Confirmed by ${by.map((s) => SOURCE[s] || words(s)).join(' and ')}`);
}

function cta(offer, label) {
  const url = safeUrl(offer.url);
  if (!url) return h('span', { class: 'no-link' }, 'No merchant link provided');
  return h('a', { class: 'btn btn-primary cta', href: url, target: '_blank', rel: 'noopener noreferrer' },
    label, icon('out'), h('span', { class: 'visually-hidden' }, ' (opens the merchant in a new tab)'));
}

function winnerCard(offer, many) {
  const read = clock(offer.read_at);
  const basis = offer.identity_basis ? OFFER_BASIS[offer.identity_basis] || words(offer.identity_basis) : null;
  return h('li', { class: 'winner' },
    h('div', { class: 'winner-main' },
      h('p', { class: 'seller' }, h('span', { class: 'seller-pre' }, many ? 'Tied seller' : 'Sold by'), ' ', sellerName(offer)),
      offer.title ? h('p', { class: 'listing-title' }, offer.title) : null,
      h('ul', { class: 'facts' },
        h('li', { class: `fact ${offer.stock === 'in_stock' ? 'fact-yes' : 'fact-open'}` },
          icon(offer.stock === 'in_stock' ? 'check' : 'dash'), stockLabel(offer.stock)),
        confirmedLine(offer),
        basis ? h('li', { class: 'fact' }, icon('check'), `Same item: ${basis}`) : null,
        prob(offer.screen_p) ? h('li', { class: 'fact fact-chip' }, screenChip(offer)) : null,
        h('li', { class: 'fact' }, icon('dash'), h('span', null, 'Price read ', read || 'at an unrecorded time',
          offer.source ? ` from ${SOURCE[offer.source] || words(offer.source)}` : '')),
      ),
    ),
    cta(offer, `Open at ${sellerName(offer)}`),
  );
}

function caution(c) {
  const sentence = c && typeof c.sentence === 'string' ? c.sentence.trim() : '';
  if (!sentence) return null;
  return h('p', { class: 'caution', role: 'note' }, icon('alert'), h('span', null, sentence));
}

function runnerUp(offer) {
  if (!offer || typeof offer !== 'object') return null;
  const price = money(offer.item_cents, offer.currency);
  if (!price) return null;
  const url = safeUrl(offer.url);
  const name = sellerName(offer);
  return h('p', { class: 'runner-up' }, 'Next verified offer: ', h('strong', null, price), ' at ',
    url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, name, icon('out'),
      h('span', { class: 'visually-hidden' }, ' (opens the merchant in a new tab)')) : name);
}

function itemView(view) {
  if (!view || !Array.isArray(view.winners) || !view.winners.length) return null;
  const price = money(view.price_cents, view.currency);
  const many = view.winners.length > 1;
  return h('section', { class: 'item-view', 'aria-labelledby': 'item-view-title' },
    h('h2', { id: 'item-view-title', class: 'item-view-title' }, view.heading || 'Verified item prices'),
    price ? h('p', { class: 'price price-second' }, price) : null,
    view.basis_label ? h('p', { class: 'basis' }, view.basis_label) : null,
    h('ul', { class: `winners${many ? ' winners-tied' : ''}` }, view.winners.map((o) => winnerCard(o, many))),
  );
}

export function renderOffer(root, run) {
  const result = run.result;
  if (!result) { root.replaceChildren(); return; }
  const winners = Array.isArray(result.winners) ? result.winners : [];
  const price = money(result.price_cents, result.currency);
  const checked = clock(run.coverage && run.coverage.checked_at);
  const many = winners.length > 1;
  fill(root,
    price && winners.length ? h('p', { class: 'price' }, price) : null,
    result.basis_label && winners.length ? h('p', { class: 'basis' }, result.basis_label) : null,
    caution(result.caution),
    many ? h('p', { class: 'tie-note' }, `${winners.length} sellers are tied at this price — listed in no particular order.`) : null,
    winners.length ? h('ul', { class: `winners${many ? ' winners-tied' : ''}` }, winners.map((o) => winnerCard(o, many))) : null,
    runnerUp(result.runner_up),
    checked && winners.length ? h('p', { class: 'checked' }, 'Stores checked at ', checked) : null,
    itemView(result.item_price_view),
  );
}

export function renderIdentity(root, run) {
  const t = run.target;
  if (!t || (!t.bound && !t.name && !t.title && !t.brand && !t.mpn)) { root.replaceChildren(); return; }
  const first = run.result && run.result.winners && run.result.winners[0];
  const img = first && safeUrl(first.image_url);
  const name = (t.bound ? t.title || t.name : t.name || t.title) || 'Unnamed item';
  const basis = t.identity_basis ? TARGET_BASIS[t.identity_basis] || words(t.identity_basis) : null;
  const details = Array.isArray(t.variant_details) ? t.variant_details.filter(Boolean) : [];
  const photoRead = t.bound ? [t.name && t.name !== name ? t.name : null, ...details].filter(Boolean) : [];
  fill(root, h('div', { class: `identity${t.bound ? '' : ' identity-open'}` },
    img ? h('img', { class: 'identity-img', src: img, alt: `Merchant image for ${name}`, referrerpolicy: 'no-referrer',
      onerror: (e) => { e.target.hidden = true; } }) : null,
    h('div', { class: 'identity-text' },
      h('p', { class: 'identity-kicker' }, t.bound ? 'Exact product' : 'Read from the photo — not bound to one exact product'),
      h('h2', { class: 'identity-name' }, name),
      h('dl', { class: 'ids' },
        t.brand ? h('div', null, h('dt', null, 'Brand'), h('dd', null, t.brand)) : null,
        t.mpn ? h('div', null, h('dt', null, 'Model'), h('dd', null, t.mpn)) : null,
        h('div', null, h('dt', null, 'GTIN'), h('dd', { class: t.gtin ? '' : 'unknown' }, t.gtin || 'none published')),
        details.length && !t.bound ? h('div', null, h('dt', null, 'Variant'), h('dd', null, details.join(' · '))) : null,
      ),
      t.bound && basis ? h('p', { class: 'identity-basis' }, icon('check'), h('span', null,
        t.identity_basis === 'visual_match'
          ? (prob(t.screen_p)
            ? `Two independent model judgments agree: Decision 1 scored this catalog product ${prob(t.screen_p)} from the photo and Carbon chose it`
            : 'Two independent model judgments agree: the Decision 1 photo screen and Carbon chose the same catalog product')
          : `Identified by ${basis}`,
        t.identity_source ? ` · ${t.identity_source}` : '')) : null,
      photoRead.length ? h('p', { class: 'identity-photo' }, h('span', { class: 'identity-photo-label' }, 'Read from the photo'),
        ' ', photoRead.join(' · ')) : null,
    ),
  ));
}

export function renderAssumptions(root, run) {
  const list = Array.isArray(run.assumptions) ? run.assumptions : [];
  if (!list.length) { root.replaceChildren(); return; }
  fill(root,
    h('p', { class: 'chips-label', id: 'assume-label' }, 'Assumed'),
    h('ul', { class: 'chips', 'aria-labelledby': 'assume-label' }, list.map((a) => h('li', { class: 'chip' }, a.label || words(a.id)))),
  );
}

export function renderProgress(list, rows, decision) {
  const seen = new Set();
  rows.forEach((row, i) => {
    const id = `p-${row.id ?? i}`;
    seen.add(id);
    let li = Array.from(list.children).find((n) => n.dataset.key === id);
    const probs = row.id === 'gate' && decision && decision.probabilities ? decision.probabilities : null;
    const sig = JSON.stringify([row, probs, probs && decision.threshold]);
    if (li && li.dataset.sig === sig) return;
    const fresh = !li;
    if (fresh) {
      li = h('li', { class: 'step step-new' });
      li.dataset.key = id;
      list.append(li);
    }
    li.dataset.sig = sig;
    li.dataset.status = row.status || '';
    const mark = row.status === 'running' ? h('span', { class: 'spin', 'aria-hidden': 'true' })
      : icon(row.status === 'done' ? 'check' : row.status === 'failed' ? 'cross' : 'dash');
    fill(li,
      h('span', { class: 'step-mark' }, mark),
      h('span', { class: 'step-body' },
        h('span', { class: 'step-label' }, row.label || words(row.id)),
        row.detail ? h('span', { class: 'step-detail' }, row.detail) : null,
        probs ? probBar(probs, decision.threshold) : null),
      h('span', { class: 'step-ms' }, row.status === 'running' ? 'running' : row.status === 'failed' ? `failed${row.ms != null ? ` · ${duration(row.ms)}` : ''}`
        : row.status === 'skipped' ? 'skipped' : duration(row.ms)),
    );
  });
  for (const li of Array.from(list.children)) if (!seen.has(li.dataset.key)) li.remove();
}

function offerLine(offer, sentence, cls, tag) {
  const url = safeUrl(offer && offer.url);
  const price = offer && money(offer.item_cents, offer.currency);
  const name = sellerName(offer);
  return h('li', { class: cls },
    h('div', { class: 'line-top' },
      url ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'line-seller' }, name, icon('out'))
        : h('span', { class: 'line-seller' }, name),
      h('span', { class: 'line-price' }, price || 'price unknown'),
    ),
    offer && offer.title ? h('p', { class: 'line-title' }, offer.title) : null,
    tag ? h('p', { class: 'line-tag' }, tag) : null,
    sentence ? h('p', { class: 'line-why' }, sentence) : null,
    offer && prob(offer.screen_p) ? h('p', { class: 'line-screen' }, screenChip(offer)) : null,
  );
}

function groupLeads(leads, result) {
  const groups = new Map();
  for (const o of leads) {
    if (!o || typeof o !== 'object') continue;
    const key = JSON.stringify([o.seller || o.seller_name, o.title, o.item_cents, o.currency]);
    const g = groups.get(key);
    if (g) g.count += 1;
    else groups.set(key, { offer: o, count: 1 });
  }
  const floor = result && Array.isArray(result.winners) && result.winners.length && typeof result.price_cents === 'number' ? result.price_cents : null;
  const list = [...groups.values()];
  for (const g of list) {
    g.cheaper = floor !== null && typeof g.offer.item_cents === 'number' && g.offer.item_cents < floor
      && typeof g.offer.currency === 'string' && g.offer.currency === result.currency;
  }
  return [...list.filter((g) => g.cheaper), ...list.filter((g) => !g.cheaper)];
}

const leadLine = (g) => offerLine(g.offer, typeof g.offer.lead_reason === 'string' ? words(g.offer.lead_reason) : '', 'line line-lead',
  `${g.cheaper ? 'Priced below the verified offer, but unverified' : 'Unverified'} — not checked${g.count > 1 ? ` · ${g.count} identical listings` : ''}`);

export function renderWhy(root, run) {
  const result = run.result;
  if (!result) { root.replaceChildren(); return; }
  const near = result.near_miss;
  const rejected = Array.isArray(result.rejected) ? result.rejected : [];
  const leads = groupLeads(Array.isArray(result.leads) ? result.leads : [], result);
  const parts = [];

  if (near || rejected.length) {
    parts.push(h('section', { class: 'panel why', 'aria-labelledby': 'why-title' },
      h('h2', { id: 'why-title' }, near ? 'Why a cheaper listing lost' : 'Listings that were ruled out'),
      near ? h('div', { class: 'near' },
        h('p', { class: 'near-sentence' }, near.sentence || words(near.reason)),
        near.offer ? h('ul', { class: 'lines' }, offerLine(near.offer, near.reason ? `Reason: ${words(near.reason)}` : '', 'line line-near')) : null,
      ) : null,
      rejected.length ? h('details', { class: 'rejected' },
        h('summary', null, `${rejected.length} listing${rejected.length === 1 ? '' : 's'} ruled out — see each reason`),
        h('ul', { class: 'lines' }, rejected.map((r) => offerLine(r.offer, r.sentence || words(r.reason), 'line line-rejected'))),
      ) : null,
    ));
  }
  if (leads.length) {
    const shown = Math.max(3, leads.filter((g) => g.cheaper).length);
    parts.push(h('section', { class: 'panel leads', 'aria-labelledby': 'leads-title' },
      h('h2', { id: 'leads-title' }, 'Unverified leads — not checked'),
      h('p', { class: 'leads-note' }, 'Found during discovery. Identity, price and stock were not verified, so none of these were ranked.'),
      h('ul', { class: 'lines' }, leads.slice(0, shown).map(leadLine)),
      leads.length > shown ? h('details', { class: 'leads-more' },
        h('summary', null, `${leads.length - shown} more unverified lead${leads.length - shown === 1 ? '' : 's'}`),
        h('ul', { class: 'lines' }, leads.slice(shown).map(leadLine))) : null,
    ));
  }
  fill(root, parts);
}

function screenLine(sc) {
  if (!sc || typeof sc !== 'object') return null;
  const hasCount = typeof sc.listings === 'number';
  return h('p', { class: 'screen-line' },
    h('strong', null, 'Decision 1'),
    hasCount ? ` screened ${sc.listings} listing${sc.listings === 1 ? '' : 's'} in one forward pass` : ' listing screen',
    typeof sc.ms === 'number' ? ` · ${duration(sc.ms)}` : '',
    sc.note ? ` — ${sc.note}` : '');
}

export function renderCoverage(root, run) {
  const c = run.coverage;
  if (!c) { root.replaceChildren(); return; }
  const sources = Array.isArray(c.sources) ? c.sources : [];
  const n = (v) => (typeof v === 'number' ? v : '—');
  const checked = clock(c.checked_at);
  fill(root, h('section', { class: 'panel coverage', 'aria-labelledby': 'coverage-title' },
    h('h2', { id: 'coverage-title' }, 'Coverage'),
    h('p', { class: 'coverage-line' },
      h('strong', null, `${n(c.responded)} of ${n(c.attempted)}`), ' sources responded · ',
      h('strong', null, n(c.sellers_seen)), ' sellers seen · ',
      h('strong', null, n(c.sellers_eligible)), ' eligible',
      checked ? [' · checked ', checked] : null),
    screenLine(run.result && run.result.screen),
    sources.length ? h('ul', { class: 'sources' }, sources.map((s) => {
      const ok = s.status === 'ok';
      const bits = [];
      if (typeof s.offers === 'number') bits.push(`${s.offers} offer${s.offers === 1 ? '' : 's'}`);
      if (typeof s.eligible === 'number') bits.push(`${s.eligible} eligible`);
      if (typeof s.ms === 'number') bits.push(duration(s.ms));
      return h('li', { class: `source ${ok ? 'source-ok' : 'source-off'}` },
        h('span', { class: 'source-top' }, icon(ok ? 'check' : 'alert'),
          h('span', { class: 'source-name' }, s.name || words(s.id)),
          h('span', { class: 'source-status' }, SOURCE_STATUS[s.status] || words(s.status))),
        bits.length ? h('span', { class: 'source-meta' }, bits.join(' · ')) : null,
        s.note ? h('span', { class: 'source-note' }, s.note) : null);
    })) : null,
  ));
}

export function renderQuestion(root, run, actions) {
  const q = run.question;
  if (!q || run.state !== 'needs_detail') { root.replaceChildren(); return; }
  const status = h('p', { class: 'q-status', role: 'status' });
  let busy = false;

  async function answer(payload, controls) {
    if (busy) return;
    busy = true;
    controls.forEach((c) => { c.disabled = true; });
    status.textContent = 'Sending your answer…';
    let r;
    try {
      r = await postJson(`/api/search/${encodeURIComponent(run.run_id)}/answer`,
        { revision: run.revision, question_id: q.question_id, ...payload });
    } catch {
      r = null;
    }
    if (r && (r.ok || r.status === 409)) {
      status.textContent = r.ok ? 'Answer received — continuing.' : 'This question changed — loading the current one.';
      actions.refresh();
      return;
    }
    busy = false;
    controls.forEach((c) => { c.disabled = false; });
    status.textContent = r ? errorText(r, 'The answer was not accepted') : 'The server could not be reached. Try again.';
  }

  const titleId = 'question-title';
  let body;
  if (q.kind === 'choice' && Array.isArray(q.options) && q.options.length) {
    const buttons = q.options.map((o, i) => h('button', { type: 'button', class: 'btn choice' },
      h('span', { class: 'choice-label' }, o.label || o.id),
      prob(o.p) ? h('span', { class: 'choice-meta' },
        h('span', { class: 'screen-chip', title: 'Decision 1 probability that this product is the pictured item',
          'aria-label': `Decision 1 probability that this product is the pictured item: ${prob(o.p)}` }, `Decision 1 · ${prob(o.p)}`),
        i === 0 && o.p >= 0.75 ? h('span', { class: 'choice-likely' }, 'most likely') : null) : null));
    buttons.forEach((b, i) => b.addEventListener('click', () => answer({ choice: q.options[i].id }, buttons)));
    body = h('div', { class: 'choices', role: 'group', 'aria-labelledby': titleId }, buttons);
  } else {
    const zip = q.kind === 'zip';
    const input = h('input', {
      type: 'text', id: 'q-input', class: 'q-input', autocomplete: zip ? 'postal-code' : 'off', maxlength: zip ? 5 : 200,
      inputmode: zip ? 'numeric' : null, pattern: zip ? '[0-9]{5}' : null, required: true,
      placeholder: zip ? '5-digit ZIP' : 'Type a short answer', 'aria-labelledby': titleId,
    });
    const send = h('button', { type: 'submit', class: 'btn btn-primary' }, 'Answer');
    body = h('form', { class: 'q-form', onsubmit: (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      answer({ text }, [input, send]);
    } }, input, send);
  }
  fill(root, h('section', { class: 'panel question', 'aria-labelledby': titleId },
    h('p', { class: 'eyebrow' }, 'One question'),
    h('h2', { id: titleId }, q.text || 'One detail is needed'),
    body, status,
    run.result && Array.isArray(run.result.winners) && run.result.winners.length
      ? h('p', { class: 'q-foot' }, 'The result so far stays usable if you skip this.') : null,
  ));
}

export function announcement(run) {
  const r = run.result;
  if (run.state === 'needs_detail' && run.question) return `One question: ${run.question.text || ''}`;
  if (!r) return (run.error && run.error.message) || STATE_TITLE[run.state] || 'Search finished';
  const winners = Array.isArray(r.winners) ? r.winners : [];
  const price = money(r.price_cents, r.currency);
  const parts = [r.headline || 'Search finished'];
  if (winners.length && price) parts.push(`${price} at ${winners.map(sellerName).join(', ')}`);
  if (r.near_miss && r.near_miss.sentence) parts.push(`A cheaper listing lost: ${r.near_miss.sentence}`);
  return `${parts.join('. ')}.`;
}
