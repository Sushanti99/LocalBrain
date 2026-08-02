// Onboarding flow: sign in → pick vault → API keys → start server → main UI.
// State persists in localStorage so reopening the app skips completed steps.

const STEPS = ['signIn', 'vault', 'keys', 'launching'];
const cfg = window.supabaseConfig;
const supabase = window.supabase.createClient(cfg.url, cfg.anonKey, {
  auth: { persistSession: true, storageKey: 'brainsquared-auth' },
});

const state = {
  step: 'signIn',
  user: null,
  vaultPath: localStorage.getItem('vaultPath') || '',
  anthropicKey: '',
};

const content = document.getElementById('content');
const dots = document.getElementById('dots');

function renderDots() {
  const idx = STEPS.indexOf(state.step);
  dots.innerHTML = STEPS.map((_, i) => {
    const cls = i < idx ? 'dot done' : i === idx ? 'dot active' : 'dot';
    return `<div class="${cls}"></div>`;
  }).join('');
}

function show(html, after) {
  content.innerHTML = html;
  renderDots();
  if (after) after();
}

// ── Step 1: Sign in ─────────────────────────────────────────────────────────
function renderSignIn() {
  state.step = 'signIn';
  show(`
    <div class="logo">brain²</div>
    <h1>Sign in to brain²</h1>
    <p>Sign in with your Google account so your integrations stay connected across devices.</p>
    <div class="actions center">
      <button class="primary google-btn" id="signInBtn">
        <span class="google-icon"></span> Sign in with Google
      </button>
    </div>
    <div id="msg"></div>
  `, () => {
    document.getElementById('signInBtn').onclick = signIn;
  });
}

async function signIn() {
  const msg = document.getElementById('msg');
  const btn = document.getElementById('signInBtn');
  btn.disabled = true;
  msg.className = 'info';
  msg.textContent = 'Opening sign-in window…';

  try {
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: cfg.oauthRedirectUrl, skipBrowserRedirect: true },
    });
    if (error) throw error;

    msg.textContent = 'Waiting for Google sign-in…';
    const callbackUrl = await window.electron.startOAuth(data.url);
    await handleAuthCallbackUrl(callbackUrl);
  } catch (err) {
    btn.disabled = false;
    msg.className = 'err';
    msg.textContent = err.message || 'Sign-in failed.';
  }
}

async function handleAuthCallbackUrl(url) {
  const msg = document.getElementById('msg');
  try {
    const u = new URL(url);
    const params = new URLSearchParams(u.hash ? u.hash.slice(1) : u.search);
    const code = params.get('code');
    if (code) {
      const { data, error } = await supabase.auth.exchangeCodeForSession(code);
      if (error) throw error;
      state.user = data.user;
    } else if (params.get('access_token')) {
      const { data, error } = await supabase.auth.setSession({
        access_token: params.get('access_token'),
        refresh_token: params.get('refresh_token'),
      });
      if (error) throw error;
      state.user = data.user;
    } else if (params.get('error')) {
      throw new Error(params.get('error_description') || params.get('error'));
    } else {
      throw new Error('No auth code or token in callback URL.');
    }
    if (msg) { msg.className = 'ok'; msg.textContent = 'Signed in! Continuing…'; }
    await recordSignIn(state.user);
    advance();
  } catch (err) {
    if (msg) { msg.className = 'err'; msg.textContent = err.message || 'Sign-in failed.'; }
    const btn = document.getElementById('signInBtn');
    if (btn) btn.disabled = false;
  }
}

// Also handle URL-scheme callbacks (in case the user finishes sign-in
// outside the in-app window — e.g. the browser intercepted brainsquared:// first).
window.electron.onAuthCallback(handleAuthCallbackUrl);

async function recordSignIn(user) {
  try {
    await supabase.from('install_signins').insert({
      user_id: user.id,
      email: user.email,
      app_version: '1.0.0',
      device_name: navigator.platform || 'Mac',
    });
  } catch (err) {
    console.error('Failed to record sign-in:', err);
  }
}

// ── Step 2: Vault picker ─────────────────────────────────────────────────────
function renderVault() {
  state.step = 'vault';
  show(`
    <h1>Choose your vault</h1>
    <p>Pick an existing folder or create a new one — brain² will set itself up inside it.</p>
    <div class="row">
      <div class="vault-path" id="vp">${state.vaultPath || 'No folder selected'}</div>
      <button class="secondary" id="pickBtn">Choose…</button>
    </div>
    <div class="actions">
      <button class="secondary" id="signOutBtn">Sign out</button>
      <button class="primary" id="continueBtn" ${state.vaultPath ? '' : 'disabled'}>Continue</button>
    </div>
  `, () => {
    document.getElementById('pickBtn').onclick = async () => {
      const picked = await window.electron.pickVault();
      if (picked) {
        state.vaultPath = picked;
        localStorage.setItem('vaultPath', picked);
        document.getElementById('vp').textContent = picked;
        document.getElementById('continueBtn').disabled = false;
      }
    };
    document.getElementById('continueBtn').onclick = advance;
    document.getElementById('signOutBtn').onclick = signOut;
  });
}

// ── Step 3: API keys ─────────────────────────────────────────────────────────
async function renderKeys() {
  state.step = 'keys';
  const existingAnthropic = await window.electron.keychain.get('anthropic_api_key');
  show(`
    <h1>Add your API keys</h1>
    <p>Stored in your macOS Keychain — never sent anywhere.</p>
    <label style="font-weight:500;font-size:13px;">Anthropic API key (required)</label>
    <input class="field" type="password" id="anthropic" placeholder="sk-ant-…" value="${existingAnthropic || ''}" style="margin:6px 0 18px">
    <label style="font-weight:500;font-size:13px;">OpenAI API key (optional, for Codex)</label>
    <input class="field" type="password" id="openai" placeholder="sk-…" style="margin:6px 0 0">
    <div class="actions">
      <button class="secondary" id="backBtn">Back</button>
      <button class="primary" id="continueBtn">Continue</button>
    </div>
    <div id="keyMsg"></div>
  `, () => {
    document.getElementById('backBtn').onclick = () => renderVault();
    document.getElementById('continueBtn').onclick = async () => {
      const anth = document.getElementById('anthropic').value.trim();
      const oai = document.getElementById('openai').value.trim();
      const msg = document.getElementById('keyMsg');
      if (!anth) {
        msg.className = 'err';
        msg.textContent = 'Anthropic key is required.';
        return;
      }
      await window.electron.keychain.set('anthropic_api_key', anth);
      if (oai) await window.electron.keychain.set('openai_api_key', oai);
      advance();
    };
  });
}

// ── Step 4: Launching ────────────────────────────────────────────────────────
async function renderLaunching() {
  state.step = 'launching';
  show(`
    <h1>Starting brain²…</h1>
    <p id="status">Spinning up the server…</p>
  `);
  try {
    const { url } = await window.electron.startServer({
      vaultPath: state.vaultPath,
      userId: state.user?.id,
    });
    document.getElementById('status').textContent = 'Loading…';
    await window.electron.loadMainUI(url);
  } catch (err) {
    show(`
      <h1>Could not start brain²</h1>
      <p class="err">${err.message}</p>
      <div class="actions center">
        <button class="primary" id="retry">Retry</button>
      </div>
    `, () => { document.getElementById('retry').onclick = advance; });
  }
}

// ── Flow control ─────────────────────────────────────────────────────────────
function advance() {
  if (!state.user) return renderSignIn();
  if (!state.vaultPath) return renderVault();
  return renderLaunching();
}

async function signOut() {
  await supabase.auth.signOut();
  await window.electron.stopServer();
  state.user = null;
  renderSignIn();
}

window.electron.onSignOut(signOut);

// Reads the persisted session straight out of localStorage (no network call),
// so a returning signed-in user skips straight to the vault/launch steps
// instead of waiting on a session refresh round-trip before rendering anything.
function peekCachedUser() {
  try {
    const raw = localStorage.getItem('brainsquared-auth');
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.user || parsed?.currentSession?.user || null;
  } catch (_) {
    return null;
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────
(async () => {
  const cachedUser = peekCachedUser();
  if (cachedUser) {
    state.user = cachedUser;
    advance();
    // Validate/refresh the session quietly in the background — if it turns out
    // to be stale, the next real auth call will surface that; we just don't
    // want a slow or unreachable network check blocking app startup.
    supabase.auth.getSession().then(({ data }) => {
      if (data.session?.user) state.user = data.session.user;
    }).catch(() => {});
    return;
  }

  const { data } = await supabase.auth.getSession();
  if (data.session?.user) {
    state.user = data.session.user;
    advance();
  } else {
    renderSignIn();
  }
})();
