const { app, BrowserWindow, ipcMain, shell, dialog, Menu, globalShortcut, screen, Notification, systemPreferences } = require('electron');
const path = require('path');
const { spawn, execFile } = require('child_process');
const http = require('http');
const net = require('net');
const fs = require('fs');
const os = require('os');

const PROTOCOL = 'brainsquared';
const isDev = !app.isPackaged;

// File-based secret storage (avoids keychain ACL prompts on every dev rebuild —
// same workaround as UserDefaultsAuthStorage on the Swift side).
function secretsFile() { return path.join(app.getPath('userData'), 'secrets.json'); }
function loadSecrets() {
  try { return JSON.parse(fs.readFileSync(secretsFile(), 'utf8')); } catch { return {}; }
}
function saveSecrets(obj) {
  fs.mkdirSync(path.dirname(secretsFile()), { recursive: true });
  fs.writeFileSync(secretsFile(), JSON.stringify(obj, null, 2), { mode: 0o600 });
}
function secretsGet(account) { return loadSecrets()[account] || null; }
function secretsSet(account, value) { const s = loadSecrets(); s[account] = value; saveSecrets(s); }
function secretsDelete(account) { const s = loadSecrets(); delete s[account]; saveSecrets(s); }

let mainWindow = null;
let serverProcess = null;
let serverPort = 3000;
let pendingCallbackUrl = null;
let pendingSignOut = false;

// ── Custom URL scheme registration (brainsquared://) ─────────────────────────
if (isDev && process.argv.length >= 2) {
  app.setAsDefaultProtocolClient(PROTOCOL, process.execPath, [path.resolve(process.argv[1])]);
} else {
  app.setAsDefaultProtocolClient(PROTOCOL);
}

// Single-instance: brainsquared:// URL on macOS comes via open-url; on
// Windows/Linux it'd come as argv to a second instance. Route to the running
// instance instead of starting a fresh one.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
  return;
}

app.on('second-instance', (_event, argv) => {
  const url = argv.find((arg) => arg.startsWith(`${PROTOCOL}://`));
  if (url) routeAuthCallback(url);
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.on('open-url', (event, url) => {
  event.preventDefault();
  console.log('[auth] open-url received:', url);
  routeAuthCallback(url);
});

function routeAuthCallback(url) {
  console.log('[auth] routing callback to renderer:', url);
  if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isLoading()) {
    mainWindow.webContents.send('auth-callback', url);
  } else {
    pendingCallbackUrl = url; // window not ready yet; deliver once it is
    console.log('[auth] window not ready; queued callback');
  }
}

// ── Python server lifecycle ──────────────────────────────────────────────────
async function findOpenPort(start) {
  for (let p = start; p < start + 20; p++) {
    const ok = await new Promise((resolve) => {
      const srv = net.createServer();
      srv.unref();
      srv.on('error', () => resolve(false));
      srv.listen(p, '127.0.0.1', () => srv.close(() => resolve(true)));
    });
    if (ok) return p;
  }
  throw new Error(`No port available starting at ${start}`);
}

function brainServerBinary() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'BrainServer', 'BrainServer');
  }
  return path.join(__dirname, 'resources', 'BrainServer', 'BrainServer');
}

async function startServer({ vaultPath, userId }) {
  if (serverProcess) stopServer();

  const binPath = brainServerBinary();
  if (!fs.existsSync(binPath)) {
    throw new Error(`BrainServer binary not found at ${binPath}. Run pyinstaller brain.spec from the repo root.`);
  }

  serverPort = await findOpenPort(3000);

  const env = { ...process.env };
  env.PATH = ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', env.PATH || ''].join(':');
  if (userId) env.BRAIN_USER_ID = userId;

  const anthropicKey = secretsGet('anthropic_api_key');
  if (anthropicKey) env.ANTHROPIC_API_KEY = anthropicKey;
  const openaiKey = secretsGet('openai_api_key');
  if (openaiKey) env.OPENAI_API_KEY = openaiKey;

  serverProcess = spawn(binPath, ['--vault', vaultPath, '--port', String(serverPort)], { env });
  serverProcess.stdout.on('data', (d) => process.stdout.write(`[srv] ${d}`));
  serverProcess.stderr.on('data', (d) => process.stderr.write(`[srv] ${d}`));
  serverProcess.on('exit', (code) => {
    console.log(`[srv] exited with code ${code}`);
    serverProcess = null;
  });

  // Poll readiness
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (await ping(serverPort)) return `http://127.0.0.1:${serverPort}`;
    await sleep(300);
  }
  throw new Error('BrainServer failed to become ready within 30s');
}

function stopServer() {
  if (serverProcess) {
    try { serverProcess.kill('SIGTERM'); } catch (_) {}
    serverProcess = null;
  }
}

function ping(port) {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${port}/`, (res) => {
      resolve(res.statusCode === 200);
      res.resume();
    });
    req.on('error', () => resolve(false));
    req.setTimeout(800, () => { req.destroy(); resolve(false); });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Quick capture (screenshot → todo) ────────────────────────────────────────
let captureWindow = null;

function triggerQuickCapture() {
  if (process.platform === 'darwin' && systemPreferences.getMediaAccessStatus('screen') !== 'granted') {
    promptForScreenRecordingPermission();
    return;
  }
  const tmpPath = path.join(os.tmpdir(), `brain-capture-${Date.now()}.png`);
  execFile('/usr/sbin/screencapture', ['-i', tmpPath], () => {
    // User pressed Esc during selection → no file written; treat as a silent no-op.
    if (!fs.existsSync(tmpPath)) return;
    openCaptureWindow(tmpPath);
  });
}

function promptForScreenRecordingPermission() {
  dialog
    .showMessageBox(mainWindow, {
      type: 'info',
      title: 'Screen Recording access needed',
      message: 'BrainSquared needs Screen Recording access to capture screenshots for Quick Capture.',
      detail:
        "Quick Capture (⌘⇧K) takes a screenshot and adds it to today's note as a todo. " +
        'To let it capture your screen, open System Settings → Privacy & Security → Screen Recording ' +
        'and enable it for BrainSquared, then try the shortcut again.',
      buttons: ['Open System Settings', 'Not Now'],
      defaultId: 0,
      cancelId: 1,
    })
    .then(({ response }) => {
      if (response === 0) {
        shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture');
      }
    });
}

function openCaptureWindow(imagePath) {
  if (captureWindow) {
    try { captureWindow.close(); } catch (_) {}
  }
  captureWindow = new BrowserWindow({
    width: 420,
    height: 420,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    show: false,
    center: true,
    backgroundColor: '#00000000',
    transparent: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload-capture.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  captureWindow.imagePath = imagePath;
  captureWindow.once('ready-to-show', () => captureWindow.show());
  captureWindow.loadFile(path.join(__dirname, 'renderer', 'capture.html'));
}

function postCapture(text, imageBase64) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ text, image_base64: imageBase64 });
    const req = http.request(
      {
        host: '127.0.0.1',
        port: serverPort,
        path: '/api/capture',
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      },
      (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) resolve(JSON.parse(data));
          else reject(new Error(`Save failed (${res.statusCode}): ${data}`));
        });
      }
    );
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

ipcMain.handle('capture-get-image', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const imagePath = win && win.imagePath;
  if (!imagePath || !fs.existsSync(imagePath)) return null;
  const data = fs.readFileSync(imagePath);
  return `data:image/png;base64,${data.toString('base64')}`;
});

ipcMain.handle('capture-submit', async (event, text) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const imagePath = win && win.imagePath;
  if (!imagePath || !fs.existsSync(imagePath)) throw new Error('No screenshot to submit.');
  const caption = (text || '').trim() || 'Screenshot';
  const imageBase64 = fs.readFileSync(imagePath).toString('base64');
  await postCapture(caption, imageBase64);
  try { fs.unlinkSync(imagePath); } catch (_) {}

  if (Notification.isSupported()) {
    const notification = new Notification({
      title: 'Added to BrainSquared',
      body: caption === 'Screenshot' ? "Saved to today's note." : caption,
    });
    notification.on('click', () => {
      if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
    });
    notification.show();
  }

  // Leave the popup open a beat so the renderer can show its own success state,
  // then close it regardless of whether the renderer is still around to ask.
  if (win) setTimeout(() => { try { win.close(); } catch (_) {} }, 900);
  return true;
});

ipcMain.handle('capture-cancel', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const imagePath = win && win.imagePath;
  if (imagePath) { try { fs.unlinkSync(imagePath); } catch (_) {} }
  if (win) win.close();
});

// ── Window ───────────────────────────────────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 650,
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#FAFAF7',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  if (isDev && process.env.BRAIN_DEVTOOLS) mainWindow.webContents.openDevTools({ mode: 'detach' });

  mainWindow.webContents.on('did-finish-load', () => {
    if (pendingSignOut) {
      pendingSignOut = false;
      mainWindow.webContents.send('sign-out');
    }
    if (pendingCallbackUrl) {
      mainWindow.webContents.send('auth-callback', pendingCallbackUrl);
      pendingCallbackUrl = null;
    }
  });

  // External links (everything not on 127.0.0.1) opens in default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith('http://127.0.0.1')) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });
}

// ── IPC: bridge between renderer and main ────────────────────────────────────
ipcMain.handle('start-server', async (_e, payload) => {
  const url = await startServer(payload);
  return { url, port: serverPort };
});

ipcMain.handle('stop-server', async () => { stopServer(); return true; });

ipcMain.handle('open-external', async (_e, url) => { await shell.openExternal(url); return true; });

// Open Google/Supabase OAuth inside an Electron window. Reliably captures
// the brainsquared:// redirect via will-redirect, no OS protocol registration
// dance required.
ipcMain.handle('start-oauth', async (_e, authUrl) => {
  return new Promise((resolve, reject) => {
    const authWin = new BrowserWindow({
      width: 500,
      height: 700,
      modal: true,
      parent: mainWindow,
      title: 'Sign in to brain²',
      webPreferences: { nodeIntegration: false, contextIsolation: true, partition: 'auth' },
    });
    let settled = false;
    const finish = (fn) => { if (!settled) { settled = true; try { authWin.close(); } catch {} fn(); } };

    const inspect = (event, url) => {
      if (url && url.startsWith(`${PROTOCOL}://`)) {
        event.preventDefault();
        console.log('[auth] captured oauth redirect:', url);
        finish(() => resolve(url));
      }
    };

    authWin.webContents.on('will-redirect', inspect);
    authWin.webContents.on('will-navigate', inspect);
    // Some browsers/Supabase emit a navigation that fails to a custom scheme
    authWin.webContents.on('did-fail-load', (_e, _ec, _ed, validatedURL) => {
      if (validatedURL && validatedURL.startsWith(`${PROTOCOL}://`)) {
        finish(() => resolve(validatedURL));
      }
    });
    authWin.on('closed', () => { if (!settled) { settled = true; reject(new Error('Sign-in window closed before completing.')); } });

    authWin.loadURL(authUrl);
  });
});

ipcMain.handle('pick-vault', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'createDirectory'],
    buttonLabel: 'Select',
    message: 'Choose a folder for your brain² vault.',
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('keychain-get', async (_e, account) => secretsGet(account));
ipcMain.handle('keychain-set', async (_e, { account, value }) => secretsSet(account, value));
ipcMain.handle('keychain-delete', async (_e, account) => secretsDelete(account));

ipcMain.handle('load-main-ui', async (_e, url) => {
  if (mainWindow) mainWindow.loadURL(url);
});

ipcMain.handle('load-onboarding', async () => {
  if (mainWindow) mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
});

// ── App lifecycle ────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  createWindow();
  buildMenu();
  globalShortcut.register('CommandOrControl+Shift+K', triggerQuickCapture);
});

app.on('window-all-closed', () => {
  stopServer();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', () => stopServer());

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        {
          label: 'Sign Out',
          accelerator: 'Cmd+Shift+Q',
          click: () => {
            stopServer();
            if (mainWindow) {
              pendingSignOut = true;
              mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
            }
          },
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    }] : []),
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
