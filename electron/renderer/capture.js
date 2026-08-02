const thumb = document.getElementById('thumb');
const captionInput = document.getElementById('captionInput');
const submitBtn = document.getElementById('submitBtn');
const cancelBtn = document.getElementById('cancelBtn');
const msgEl = document.getElementById('msg');

async function init() {
  const dataUrl = await window.captureAPI.getImage();
  if (dataUrl) thumb.src = dataUrl;
  captionInput.focus();
}

async function submit() {
  const text = captionInput.value.trim();
  submitBtn.disabled = true;
  cancelBtn.disabled = true;
  captionInput.disabled = true;
  msgEl.classList.remove('err');
  msgEl.textContent = '';
  try {
    await window.captureAPI.submit(text);
    submitBtn.textContent = 'Added ✓';
    msgEl.classList.add('ok');
    msgEl.textContent = 'Saved to today\'s note.';
  } catch (err) {
    submitBtn.disabled = false;
    cancelBtn.disabled = false;
    captionInput.disabled = false;
    msgEl.classList.add('err');
    msgEl.textContent = err && err.message ? err.message : 'Failed to save capture.';
  }
}

function cancel() {
  window.captureAPI.cancel();
}

submitBtn.addEventListener('click', submit);
cancelBtn.addEventListener('click', cancel);
captionInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') submit();
  if (event.key === 'Escape') cancel();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') cancel();
});

init();
