const EXPIRED = 'This link has expired — make a new code on the other screen';
const token = location.pathname.split('/').filter(Boolean).pop() || '';
const input = document.getElementById('phone-file');
const take = document.getElementById('phone-take');
const status = document.getElementById('phone-status');
const say = (text) => { status.textContent = text; };

function close(text) {
  say(text);
  take.hidden = true;
  input.disabled = true;
}

async function shrink(file) {
  if (file.size <= 4 * 1024 * 1024) return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return (await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9))) || file;
  } catch {
    return file;
  }
}

async function send(file) {
  say('Sending…');
  take.hidden = true;
  try {
    const form = new FormData();
    form.append('image', await shrink(file), 'photo.jpg');
    const res = await fetch(`/api/handoff/${encodeURIComponent(token)}/photo`, { method: 'POST', body: form });
    const body = await res.json().catch(() => null);
    if (res.ok && body && body.ok) return close('Sent — look at the other screen');
    if (res.status === 404) return close(EXPIRED);
    say((body && body.error && body.error.message) || 'The photo did not send. Try again.');
  } catch {
    say('The photo did not send. Check your connection and try again.');
  }
  take.textContent = 'Try again';
  take.hidden = false;
}

input.addEventListener('change', () => {
  const file = input.files && input.files[0];
  input.value = '';
  if (file) send(file);
});

fetch(`/api/handoff/${encodeURIComponent(token)}/open`)
  .then((res) => res.json())
  .then((body) => { if (!body || body.open !== true) close(EXPIRED); })
  .catch(() => {});
