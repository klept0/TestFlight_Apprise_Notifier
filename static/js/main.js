/* ============================================================
   TestFlight Apprise Notifier — Dashboard JS
   ============================================================ */

'use strict';

/* ── CSRF ───────────────────────────────────────────────────── */
/* Echo the csrf_token cookie in the X-CSRF-Token header on every
   state-changing request, so the server's CSRF check passes. Wraps fetch
   once so all existing call sites are covered. */
(function () {
  const _origFetch = window.fetch;
  window.fetch = function (url, opts) {
    opts = opts || {};
    const method = (opts.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
      const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
      if (m) {
        const headers = new Headers(opts.headers || {});
        headers.set('X-CSRF-Token', decodeURIComponent(m[1]));
        opts.headers = headers;
      }
    }
    return _origFetch.call(this, url, opts);
  };
})();

/* ── Theme ──────────────────────────────────────────────────── */
function initTheme() {
  const saved = localStorage.getItem('tf-theme');
  // Fall back to the server-configured default (UI_THEME env var), then 'dark'
  const serverDefault = (typeof SERVER_DEFAULT_THEME !== 'undefined') ? SERVER_DEFAULT_THEME : 'dark';
  setTheme(saved || serverDefault);
}

function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('tf-theme', theme);
  // Keep the mobile browser chrome (status/address bar) matching the theme.
  const meta = document.getElementById('theme-color-meta');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#0d1117' : '#f3f4f6');
  const moon = document.getElementById('theme-icon-moon');
  const sun  = document.getElementById('theme-icon-sun');
  if (theme === 'dark') {
    moon.style.display = 'none';
    sun.style.display  = '';
  } else {
    moon.style.display = '';
    sun.style.display  = 'none';
  }
}

document.getElementById('theme-toggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  setTheme(current === 'dark' ? 'light' : 'dark');
});

/* ── Navigation ─────────────────────────────────────────────── */
const SECTION_TITLES = {
  dashboard: 'Dashboard',
  ids:       'TestFlight IDs',
  urls:      'Apprise URLs',
  settings:  'Settings',
  logs:      'Logs',
};

function navigateTo(sectionId) {
  // Hide all sections
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.remove('active');
    n.removeAttribute('aria-current');
  });

  // Show target
  const section = document.getElementById(`section-${sectionId}`);
  if (section) section.classList.add('active');

  // Mark nav item active
  const navItem = document.querySelector(`.nav-item[data-section="${sectionId}"]`);
  if (navItem) {
    navItem.classList.add('active');
    navItem.setAttribute('aria-current', 'page');
  }

  // Update page title
  document.getElementById('page-title').textContent = SECTION_TITLES[sectionId] || sectionId;

  // Update URL hash (no page reload)
  history.replaceState(null, '', `#${sectionId}`);

  // Lazy load section data on first visit
  onSectionActivated(sectionId);
}

function onSectionActivated(sectionId) {
  if (sectionId === 'dashboard') refreshMetrics();
  if (sectionId === 'ids')       refreshIds();
  if (sectionId === 'urls')      refreshUrls();
  if (sectionId === 'settings')  loadConfig();
  if (sectionId === 'logs')      refreshLogs();
}

// Wire up nav buttons
document.querySelectorAll('.nav-item[data-section]').forEach(btn => {
  btn.addEventListener('click', () => navigateTo(btn.dataset.section));
});

/* ── Toast Notifications ────────────────────────────────────── */
function showToast(message, type = 'info', title = '') {
  const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
  const container = document.getElementById('toast-container');

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span class="toast-icon" aria-hidden="true">${icons[type] || icons.info}</span>
    <div class="toast-body">
      ${title ? `<div class="toast-title">${escHtml(title)}</div>` : ''}
      <div class="toast-msg">${escHtml(message)}</div>
    </div>`;

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('toast-out');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }, 4000);
}

/* ── Confirm Modal ──────────────────────────────────────────── */
let _confirmCallback = null;

function confirmAction(action, title, message) {
  document.getElementById('confirm-title').textContent   = title;
  document.getElementById('confirm-message').textContent = message;

  const okBtn = document.getElementById('confirm-ok');
  okBtn.className = action === 'stop' ? 'btn btn-danger' : 'btn btn-warning';
  okBtn.textContent = action === 'stop' ? 'Stop' : 'Restart';

  _confirmCallback = action === 'stop' ? stopApplication : restartApplication;

  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('confirm-cancel').focus();
}

function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
  _confirmCallback = null;
}

document.getElementById('confirm-ok').addEventListener('click', () => {
  // Capture the callback before closeModal() clears it, otherwise the
  // confirmed action (stop/restart) would never run.
  const cb = _confirmCallback;
  closeModal();
  if (cb) cb();
});

document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

/* ── Application Controls ───────────────────────────────────── */
async function stopApplication() {
  try {
    const res = await fetch('/api/control/stop', { method: 'POST' });
    if (res.ok) {
      showToast('Application is shutting down.', 'warning', 'Stopping');
      document.querySelectorAll('.control-card .btn').forEach(b => b.disabled = true);
    } else {
      showToast('Failed to stop the application.', 'error');
    }
  } catch {
    showToast('Could not reach the application.', 'error');
  }
}

async function restartApplication() {
  try {
    const res = await fetch('/api/control/restart', { method: 'POST' });
    if (res.ok) {
      showToast('Restarting… the page will reconnect shortly.', 'info', 'Restarting');
      document.querySelectorAll('.control-card .btn').forEach(b => b.disabled = true);
      scheduleReconnectPoll();
    } else {
      showToast('Failed to restart the application.', 'error');
    }
  } catch {
    showToast('Could not reach the application.', 'error');
  }
}

function scheduleReconnectPoll() {
  let attempts = 0;
  const poll = setInterval(async () => {
    attempts++;
    try {
      const res = await fetch('/api/health');
      if (res.ok) {
        clearInterval(poll);
        showToast('Application restarted successfully.', 'success', 'Back online');
        document.querySelectorAll('.control-card .btn').forEach(b => b.disabled = false);
        refreshMetrics();
      }
    } catch { /* still restarting */ }
    if (attempts >= 30) clearInterval(poll); // give up after ~60s
  }, 2000);
}

/* ── Metrics ────────────────────────────────────────────────── */
async function refreshMetrics() {
  try {
    const res  = await fetch('/api/metrics');
    const data = await res.json();

    document.getElementById('metric-total').textContent   = fmtNum(data.total_checks);
    document.getElementById('metric-success').textContent = fmtNum(data.successful_checks);
    document.getElementById('metric-cpm').textContent     = data.checks_per_minute.toFixed(2);
    document.getElementById('metric-uptime').textContent  = (data.uptime_seconds / 3600).toFixed(1);
  } catch { /* silent */ }

  try {
    const res  = await fetch('/api/health');
    const data = await res.json();
    const up   = data.uptime_seconds;
    const h    = Math.floor(up / 3600);
    const m    = Math.floor((up % 3600) / 60);
    const s    = up % 60;
    const str  = `${pad(h)}:${pad(m)}:${pad(s)}`;
    document.getElementById('uptime-str').textContent    = str;
    document.getElementById('uptime-display').textContent = str;
  } catch { /* silent */ }
}

/* ── TestFlight IDs ─────────────────────────────────────────── */
async function refreshIds() {
  try {
    const res  = await fetch('/api/testflight-ids/details');
    const data = await res.json();
    renderIds(data.testflight_ids);
    document.getElementById('id-count').textContent = data.testflight_ids.length;
  } catch {
    document.getElementById('ids-list').innerHTML = errorRow('Failed to load IDs.');
  }
}

function renderIds(ids) {
  const container = document.getElementById('ids-list');
  if (!ids.length) {
    container.innerHTML = emptyState('📱', 'No TestFlight IDs configured');
    return;
  }
  container.innerHTML = ids.map(item => {
    const name = item.display_name || item.id;
    const hasSub = item.app_name && item.app_name !== item.id;
    const icon = item.icon_url
      ? `<img class="item-icon" src="${escAttr(item.icon_url)}" alt="" onerror="this.style.display='none'">`
      : `<div class="item-icon-placeholder">📱</div>`;
    return `<div class="item-row">
      ${icon}
      <div class="item-info">
        <div class="item-name">${escHtml(name)}</div>
        ${hasSub ? `<div class="item-sub">${escHtml(item.id)}</div>` : ''}
      </div>
      <div class="item-actions">
        <button class="btn btn-sm btn-danger" onclick="removeId('${escAttr(item.id)}')" aria-label="Remove ${escAttr(name)}">Remove</button>
      </div>
    </div>`;
  }).join('');
}

async function validateAndAddId() {
  const input     = document.getElementById('new-tf-id');
  const statusDiv = document.getElementById('add-id-status');
  const addBtn    = document.getElementById('add-id-btn');
  const tfId      = input.value.trim();

  if (!tfId) { setStatus(statusDiv, 'Please enter a TestFlight ID.', 'error'); return; }

  addBtn.disabled = true;
  setStatus(statusDiv, 'Validating…', 'info');

  try {
    const vRes  = await fetch('/api/testflight-ids/validate', jsonPost({ id: tfId }));
    const vData = await vRes.json();

    if (!vData.valid) {
      setStatus(statusDiv, vData.message, 'error');
      addBtn.disabled = false;
      return;
    }

    let successMsg = 'Valid TestFlight ID';
    if (vData.app_name && vData.app_name !== tfId) {
      successMsg = `Found: <strong>${escHtml(vData.app_name)}</strong>`;
    }
    setStatus(statusDiv, successMsg, 'success');
    await sleep(800);

    setStatus(statusDiv, 'Adding…', 'info');
    const aRes  = await fetch('/api/testflight-ids', jsonPost({ id: tfId }));
    const aData = await aRes.json();

    if (aRes.ok) {
      input.value = '';
      setStatus(statusDiv, aData.message, 'success');
      renderIds(aData.testflight_ids);
      document.getElementById('id-count').textContent = aData.testflight_ids.length;
      showToast(`Added ${tfId}`, 'success');
    } else {
      setStatus(statusDiv, aData.detail || 'Failed to add ID.', 'error');
    }
  } catch (e) {
    setStatus(statusDiv, `Error: ${e.message}`, 'error');
  }
  addBtn.disabled = false;
}

async function removeId(tfId) {
  try {
    const res  = await fetch(`/api/testflight-ids/${encodeURIComponent(tfId)}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok) {
      renderIds(data.testflight_ids);
      document.getElementById('id-count').textContent = data.testflight_ids.length;
      showToast(`Removed ${tfId}`, 'success');
    } else {
      showToast(data.detail || 'Failed to remove ID.', 'error');
    }
  } catch (e) {
    showToast(`Error: ${e.message}`, 'error');
  }
}

/* ── Apprise URLs ───────────────────────────────────────────── */
async function refreshUrls() {
  try {
    const res  = await fetch('/api/apprise-urls');
    const data = await res.json();
    renderUrls(data.apprise_urls);
    document.getElementById('url-count').textContent = data.apprise_urls.length;
  } catch {
    document.getElementById('urls-list').innerHTML = errorRow('Failed to load URLs.');
  }
}

function renderUrls(urls) {
  const container = document.getElementById('urls-list');
  if (!urls.length) {
    container.innerHTML = emptyState('🔔', 'No notification URLs configured');
    return;
  }
  container.innerHTML = urls.map(u => {
    const icon = u.icon_url
      ? `<img class="item-icon" src="${escAttr(u.icon_url)}" alt="${escAttr(u.service_name)}" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'item-icon-placeholder',textContent:'${escAttr(u.emoji || '🔔')}'}))">`
      : `<div class="item-icon-placeholder">${escHtml(u.emoji || '🔔')}</div>`;
    return `<div class="item-row">
      ${icon}
      <div class="item-info">
        <div class="item-name">${escHtml(u.service_name || 'Unknown Service')}</div>
        <div class="item-sub" title="${escAttr(u.display_url)}">${escHtml(u.display_url)}</div>
      </div>
      <div class="item-actions">
        <button class="btn btn-sm btn-danger" onclick="removeUrl('${escAttr(u.id)}')" aria-label="Remove ${escAttr(u.service_name)} URL">Remove</button>
      </div>
    </div>`;
  }).join('');
}

async function validateAndAddUrl() {
  const input     = document.getElementById('new-apprise-url');
  const statusDiv = document.getElementById('add-url-status');
  const addBtn    = document.getElementById('add-url-btn');
  const url       = input.value.trim();

  if (!url) { setStatus(statusDiv, 'Please enter an Apprise URL.', 'error'); return; }

  addBtn.disabled = true;
  setStatus(statusDiv, 'Validating…', 'info');

  try {
    const vRes  = await fetch('/api/apprise-urls/validate', jsonPost({ url }));
    const vData = await vRes.json();

    if (!vData.valid) {
      setStatus(statusDiv, vData.message, 'error');
      addBtn.disabled = false;
      return;
    }

    setStatus(statusDiv, vData.message, 'success');
    await sleep(800);

    setStatus(statusDiv, 'Adding…', 'info');
    const aRes  = await fetch('/api/apprise-urls', jsonPost({ url }));
    const aData = await aRes.json();

    if (aRes.ok) {
      input.value = '';
      setStatus(statusDiv, aData.message, 'success');
      renderUrls(aData.apprise_urls);
      document.getElementById('url-count').textContent = aData.apprise_urls.length;
      showToast('Apprise URL added', 'success');
    } else {
      setStatus(statusDiv, aData.detail || 'Failed to add URL.', 'error');
    }
  } catch (e) {
    setStatus(statusDiv, `Error: ${e.message}`, 'error');
  }
  addBtn.disabled = false;
}

async function removeUrl(urlId) {
  try {
    const res  = await fetch(`/api/apprise-urls/${urlId}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok) {
      renderUrls(data.apprise_urls);
      document.getElementById('url-count').textContent = data.apprise_urls.length;
      showToast('Notification URL removed', 'success');
    } else {
      showToast(data.detail || 'Failed to remove URL.', 'error');
    }
  } catch (e) {
    showToast(`Error: ${e.message}`, 'error');
  }
}

/* ── Settings / Config ──────────────────────────────────────── */
async function loadConfig() {
  const editor    = document.getElementById('config-editor');
  const statusDiv = document.getElementById('config-status');
  setStatus(statusDiv, 'Loading…', 'info');
  try {
    const res  = await fetch('/api/config');
    const data = await res.json();
    if (res.ok) {
      editor.value = data.content;
      setStatus(statusDiv, '', '');
    } else {
      setStatus(statusDiv, data.detail || 'Failed to load config.', 'error');
    }
  } catch (e) {
    setStatus(statusDiv, `Error: ${e.message}`, 'error');
  }
}

async function saveConfig() {
  const editor    = document.getElementById('config-editor');
  const statusDiv = document.getElementById('config-status');
  setStatus(statusDiv, 'Saving…', 'info');
  try {
    const form = new FormData();
    form.append('content', editor.value);
    const res  = await fetch('/api/config', { method: 'POST', body: form });
    const data = await res.json();
    if (res.ok) {
      setStatus(statusDiv, 'Saved. Restart to apply changes.', 'success');
      showToast('Configuration saved', 'success');
    } else {
      setStatus(statusDiv, data.detail || 'Save failed.', 'error');
    }
  } catch (e) {
    setStatus(statusDiv, `Error: ${e.message}`, 'error');
  }
}

async function saveAndRestart() {
  await saveConfig();
  const statusDiv = document.getElementById('config-status');
  const current   = statusDiv.textContent;
  if (!current.toLowerCase().includes('error') && !current.toLowerCase().includes('failed')) {
    await restartApplication();
  }
}

async function saveToggles() {
  const statusDiv = document.getElementById('toggle-status');
  const updateChecker = document.getElementById('toggle-update-checker').checked;
  const alwaysNotify  = document.getElementById('toggle-always-notify').checked;

  setStatus(statusDiv, 'Saving…', 'info');

  const uiTheme = document.getElementById('select-ui-theme').value;

  try {
    // Load current config content
    const res  = await fetch('/api/config');
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to load config');

    let content = data.content;
    content = setEnvVar(content, 'ENABLE_UPDATE_CHECKER', updateChecker ? 'true' : 'false');
    content = setEnvVar(content, 'ALWAYS_NOTIFY_OPEN',   alwaysNotify  ? 'true' : 'false');
    content = setEnvVar(content, 'UI_THEME',             uiTheme);

    const form = new FormData();
    form.append('content', content);
    const saveRes  = await fetch('/api/config', { method: 'POST', body: form });
    const saveData = await saveRes.json();

    if (saveRes.ok) {
      // Refresh editor to show updated content
      document.getElementById('config-editor').value = content;
      setStatus(statusDiv, 'Saved. Restart to apply changes.', 'success');
      showToast('Settings saved — restart to apply', 'success');
    } else {
      setStatus(statusDiv, saveData.detail || 'Save failed.', 'error');
    }
  } catch (e) {
    setStatus(statusDiv, `Error: ${e.message}`, 'error');
  }
}

function setEnvVar(content, key, value) {
  const re   = new RegExp(`^(${key}\\s*=).*$`, 'm');
  const line = `${key}=${value}`;
  return re.test(content)
    ? content.replace(re, line)
    : content + (content.endsWith('\n') ? '' : '\n') + line + '\n';
}

/* ── Logs ───────────────────────────────────────────────────── */
let _logInterval = null;

async function refreshLogs() {
  const limit    = document.getElementById('log-limit').value;
  const container = document.getElementById('logs-container');
  try {
    const res  = await fetch(`/api/logs?limit=${limit}`);
    const data = await res.json();
    renderLogs(data.logs);
  } catch {
    container.innerHTML = errorRow('Failed to load logs.');
  }
}

function renderLogs(logs) {
  const container = document.getElementById('logs-container');
  if (!logs.length) {
    container.innerHTML = `<div class="empty-state"><div class="empty-state-text">No log entries yet.</div></div>`;
    return;
  }
  container.innerHTML = logs.map(e =>
    `<div class="log-entry">
      <span class="log-ts">${escHtml(e.timestamp)}</span>
      <span class="log-level log-level-${escAttr(e.level)}">${escHtml(e.level)}</span>
      <span class="log-msg">${escHtml(e.message)}</span>
    </div>`
  ).join('');
}

function toggleAutoRefreshLogs() {
  const enabled = document.getElementById('auto-refresh-logs').checked;
  if (enabled) {
    _logInterval = setInterval(refreshLogs, 15000);
  } else {
    clearInterval(_logInterval);
    _logInterval = null;
  }
}

/* ── Helpers ────────────────────────────────────────────────── */
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escAttr(str) {
  return String(str ?? '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function setStatus(el, msg, type) {
  el.className = `status-msg${type ? ' ' + type : ''}`;
  el.innerHTML = msg;
}

function jsonPost(body) {
  return {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  };
}

function emptyState(icon, text) {
  return `<div class="empty-state">
    <div class="empty-state-icon">${icon}</div>
    <div class="empty-state-text">${escHtml(text)}</div>
  </div>`;
}

function errorRow(msg) {
  return `<div class="empty-state"><div class="empty-state-text text-muted">${escHtml(msg)}</div></div>`;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function pad(n)    { return String(n).padStart(2, '0'); }
function fmtNum(n) { return n == null ? '—' : n.toLocaleString(); }

/* ── Enter key on inputs ────────────────────────────────────── */
document.getElementById('new-tf-id').addEventListener('keydown', e => {
  if (e.key === 'Enter') validateAndAddId();
});
document.getElementById('new-apprise-url').addEventListener('keydown', e => {
  if (e.key === 'Enter') validateAndAddUrl();
});

/* ── Background polling ─────────────────────────────────────── */
function startPolling() {
  // Metrics refresh every 30s
  setInterval(() => {
    const active = document.querySelector('.section.active');
    if (active?.id === 'section-dashboard') refreshMetrics();
  }, 30000);

  // Log auto-refresh if enabled
  _logInterval = setInterval(() => {
    const active = document.querySelector('.section.active');
    if (active?.id === 'section-logs' && document.getElementById('auto-refresh-logs').checked) {
      refreshLogs();
    }
  }, 15000);
}

/* ── Init ───────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initTheme();

  // Resolve initial section from URL hash
  const hash = window.location.hash.replace('#', '') || 'dashboard';
  const validSections = Object.keys(SECTION_TITLES);
  navigateTo(validSections.includes(hash) ? hash : 'dashboard');

  startPolling();
});
