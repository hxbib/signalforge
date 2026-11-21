import { $ } from './dom.js';

const MAX_UPLOAD = 4 * 1024 * 1024;
const MAX_SIDE = 2000;

async function shrink(blob) {
  const bitmap = await createImageBitmap(blob, { imageOrientation: 'from-image' });
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9));
}

async function fit(blob) {
  if (blob.size <= MAX_UPLOAD) return blob;
  try {
    return (await shrink(blob)) || blob;
  } catch {
    return blob;
  }
}

function imageFrom(list) {
  for (const item of list || []) {
    if (item.kind === 'file' && item.type.startsWith('image/')) return item.getAsFile();
    if (item instanceof File && item.type.startsWith('image/')) return item;
  }
  return null;
}

export function initCapture({ onImage }) {
  const drop = $('#drop');
  const idle = $('#drop-idle');
  const finder = $('#viewfinder');
  const video = $('#video');
  const note = $('#note');
  const error = $('#capture-error');
  let stream = null;
  let depth = 0;

  const say = (text) => { error.textContent = text || ''; };

  async function send(blob) {
    if (!blob) return;
    if (blob.type && !blob.type.startsWith('image/')) {
      say('That file is not an image. Use a JPEG, PNG or WebP photo.');
      return;
    }
    say('');
    stopCamera();
    const text = note.value.trim();
    note.value = '';
    onImage(await fit(blob), text);
  }

  for (const id of ['#file', '#file-camera']) {
    $(id).addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = '';
      send(file);
    });
  }

  document.addEventListener('paste', (e) => {
    const data = e.clipboardData;
    const file = data && (imageFrom(data.items) || imageFrom(data.files));
    if (!file) return;
    e.preventDefault();
    send(file);
  });

  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth += 1;
    document.body.classList.add('dragging');
  });
  window.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  window.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) document.body.classList.remove('dragging');
  });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    document.body.classList.remove('dragging');
    const file = imageFrom(e.dataTransfer.files);
    if (file) send(file);
    else say('That file is not an image. Use a JPEG, PNG or WebP photo.');
  });

  function stopCamera() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    video.srcObject = null;
    finder.hidden = true;
    idle.hidden = false;
    drop.classList.remove('live');
  }

  async function startCamera() {
    say('');
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
      $('#file-camera').click();
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
    } catch {
      stream = null;
      $('#file-camera').click();
      return;
    }
    video.srcObject = stream;
    idle.hidden = true;
    finder.hidden = false;
    drop.classList.add('live');
    try { await video.play(); } catch { /* the shutter still works once frames arrive */ }
    $('#shutter').focus();
  }

  function shoot() {
    if (!stream || !video.videoWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    canvas.toBlob((blob) => send(blob), 'image/jpeg', 0.92);
  }

  $('#camera').addEventListener('click', startCamera);
  $('#shutter').addEventListener('click', shoot);
  $('#camera-cancel').addEventListener('click', () => { stopCamera(); $('#camera').focus(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && stream) { stopCamera(); $('#camera').focus(); }
  });
  note.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') say('Add a photo to search — paste, drop or choose one. The note is optional.');
  });

  return { stopCamera, say };
}
