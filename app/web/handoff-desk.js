try {
  const button = document.getElementById('phone-camera');
  const box = document.getElementById('handoff');
  let timer = null;

  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };

  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
  };

  const closeBox = () => {
    stop();
    box.replaceChildren();
    box.hidden = true;
  };

  const message = (text, again) => {
    stop();
    box.replaceChildren(el('p', text));
    if (again) {
      const retry = el('button', 'Make a new code', 'btn');
      retry.type = 'button';
      retry.addEventListener('click', open);
      box.append(retry);
    }
    const cancel = el('button', 'Cancel', 'btn btn-ghost');
    cancel.type = 'button';
    cancel.addEventListener('click', closeBox);
    box.append(cancel);
    box.hidden = false;
  };

  async function check(token) {
    try {
      const res = await fetch(`/api/handoff/${encodeURIComponent(token)}`);
      if (res.status === 404) return message('That code is no longer valid.', true);
      const body = await res.json();
      if (body.status === 'received' && body.run_id) {
        closeBox();
        location.hash = `#run=${encodeURIComponent(body.run_id)}`;
      } else if (body.status === 'expired') {
        message('That code has expired.', true);
      }
    } catch { /* keep waiting; the next tick tries again */ }
  }

  async function open() {
    try {
      stop();
      const res = await fetch('/api/handoff', { method: 'POST' });
      const body = await res.json();
      if (!res.ok || !body.token) return message((body.error && body.error.message) || 'Could not make a code.', true);
      const img = el('img');
      img.src = body.qr_url;
      img.alt = 'QR code to continue on your phone';
      img.width = 220;
      img.height = 220;
      const link = el('a', body.url);
      link.href = body.url;
      link.target = '_blank';
      link.rel = 'noopener';
      const cancel = el('button', 'Cancel', 'btn btn-ghost');
      cancel.type = 'button';
      cancel.addEventListener('click', closeBox);
      box.replaceChildren(img, el('p', 'Scan with your phone and take the photo there. The search appears here.'), link, cancel);
      box.hidden = false;
      timer = setInterval(() => check(body.token), 1000);
    } catch {
      message('Could not make a code.', true);
    }
  }

  if (button && box) {
    fetch('/api/health')
      .then((res) => res.json())
      .then((body) => {
        if (!body || !body.lanes || body.lanes['app.handoff.routes'] !== true) return;
        button.hidden = false;
        button.addEventListener('click', open);
      })
      .catch(() => {});
  }
} catch { /* the hand-off is optional; the page works without it */ }
