// Backend API Base URL
const API_BASE = 'http://localhost:8000';

let currentModel = 'meta/llama-3.2-11b-vision-instruct';
let chatHistory = [];
let isGenerating = false;
let phoneConnected = false;
let pipInterval = null;

// DOM Elements
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebar-toggle');
const sidebarOpenBtn = document.getElementById('sidebar-open-btn');
const newChatBtn = document.getElementById('new-chat-btn');
const chatViewport = document.getElementById('chat-viewport');
const emptyState = document.getElementById('empty-state');
const messageThread = document.getElementById('message-thread');
const promptTextarea = document.getElementById('prompt-textarea');
const sendBtn = document.getElementById('send-btn');

// Models
const modelSelectorBtn = document.getElementById('model-selector-btn');
const modelDropdownMenu = document.getElementById('model-dropdown-menu');
const currentModelDisplay = document.getElementById('current-model-display');

// Permission Selector (Below Textbox)
let currentPermissionMode = localStorage.getItem('os_agent_permission_mode') || 'full_access';
const permissionSelect = document.getElementById('permission-select');
const permDot = document.getElementById('perm-dot');
const permStatusText = document.getElementById('perm-status-text');
const permStatusIndicator = document.getElementById('perm-status-indicator');

// Status & Remote
const phoneStatusDot = document.getElementById('phone-status-dot');
const phoneStatusBadge = document.getElementById('phone-status-badge');
const footerApiStatus = document.getElementById('footer-api-status');
const btnOpenQr = document.getElementById('btn-open-qr');
const btnTopQr = document.getElementById('btn-top-qr');
const qrModal = document.getElementById('qr-modal');
const remoteUrlDisplay = document.getElementById('remote-url-display');
const btnCopyUrl = document.getElementById('btn-copy-url');

// Settings
const settingsModal = document.getElementById('settings-modal');
const btnOpenSettings = document.getElementById('btn-open-settings');
const btnSettingsFooter = document.getElementById('btn-settings-footer');
const inputApiKey = document.getElementById('input-api-key');
const btnToggleKeyVis = document.getElementById('btn-toggle-key-vis');
const selectDefaultModel = document.getElementById('select-default-model');
const rangeStreamFps = document.getElementById('range-stream-fps');
const valStreamFps = document.getElementById('val-stream-fps');
const rangeStreamQuality = document.getElementById('range-stream-quality');
const valStreamQuality = document.getElementById('val-stream-quality');
const btnSaveSettings = document.getElementById('btn-save-settings');

// PiP Screen
const btnToggleScreenPip = document.getElementById('btn-toggle-screen-pip');
const screenPipPanel = document.getElementById('screen-pip-panel');
const pipScreenImg = document.getElementById('pip-screen-img');
const pipCloseBtn = document.getElementById('pip-close-btn');

// Quick Tools
const btnQuickScreenshot = document.getElementById('btn-quick-screenshot');
const btnQuickWin = document.getElementById('btn-quick-win');

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
  initEventListeners();
  checkBackendStatus();
  loadSettings();
  initRemotePolling();
});

function initEventListeners() {
  // Sidebar toggle
  sidebarToggle.addEventListener('click', () => {
    sidebar.classList.add('collapsed');
    sidebarOpenBtn.style.display = 'flex';
  });

  sidebarOpenBtn.addEventListener('click', () => {
    sidebar.classList.remove('collapsed');
    sidebarOpenBtn.style.display = 'none';
  });

  newChatBtn.addEventListener('click', () => {
    resetChat();
  });

  // Prompt Auto-resize & submit
  promptTextarea.addEventListener('input', () => {
    promptTextarea.style.height = 'auto';
    promptTextarea.style.height = Math.min(promptTextarea.scrollHeight, 180) + 'px';
    sendBtn.disabled = !promptTextarea.value.trim() || isGenerating;
  });

  promptTextarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) {
        handleSendMessage();
      }
    }
  });

  sendBtn.addEventListener('click', handleSendMessage);

  // Permission Dropdown below textbox (Grant once)
  if (permissionSelect) {
    permissionSelect.value = currentPermissionMode;
    updatePermStatusUI(currentPermissionMode);
    permissionSelect.addEventListener('change', (e) => {
      currentPermissionMode = e.target.value;
      localStorage.setItem('os_agent_permission_mode', currentPermissionMode);
      updatePermStatusUI(currentPermissionMode);
    });
  }

  // Model Dropdown
  modelSelectorBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    modelDropdownMenu.classList.toggle('show');
  });

  document.querySelectorAll('.dropdown-item').forEach(item => {
    item.addEventListener('click', () => {
      document.querySelectorAll('.dropdown-item').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      currentModel = item.dataset.model;
      const title = item.querySelector('.item-title').textContent;
      currentModelDisplay.textContent = title;
      modelDropdownMenu.classList.remove('show');
    });
  });

  document.addEventListener('click', () => {
    modelDropdownMenu.classList.remove('show');
  });

  // QR Modal triggers
  btnOpenQr.addEventListener('click', showQrModal);
  btnTopQr.addEventListener('click', showQrModal);
  btnCopyUrl.addEventListener('click', copyRemoteUrl);

  // Settings triggers
  btnOpenSettings.addEventListener('click', () => openModal('settings-modal'));
  btnSettingsFooter.addEventListener('click', () => openModal('settings-modal'));
  btnSaveSettings.addEventListener('click', saveSettings);

  btnToggleKeyVis.addEventListener('click', () => {
    if (inputApiKey.type === 'password') {
      inputApiKey.type = 'text';
      btnToggleKeyVis.textContent = 'Hide';
    } else {
      inputApiKey.type = 'password';
      btnToggleKeyVis.textContent = 'Show';
    }
  });

  rangeStreamFps.addEventListener('input', (e) => {
    valStreamFps.textContent = e.target.value;
  });
  rangeStreamQuality.addEventListener('input', (e) => {
    valStreamQuality.textContent = e.target.value;
  });

  // Screen PiP
  btnToggleScreenPip.addEventListener('click', toggleScreenPip);
  pipCloseBtn.addEventListener('click', () => {
    screenPipPanel.style.display = 'none';
    if (pipInterval) clearInterval(pipInterval);
  });

  // Quick Tools
  btnQuickScreenshot.addEventListener('click', () => {
    applySuggestion('Take a screenshot of my desktop and check what is currently open');
  });

  btnQuickWin.addEventListener('click', () => {
    fetch(`${API_BASE}/api/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'press', params: { key: 'win' } })
    });
  });
}

// --- Status & Settings ---
async function checkBackendStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (res.ok) {
      const data = await res.json();
      if (data.has_api_key) {
        footerApiStatus.textContent = 'NVIDIA NIM Active';
        footerApiStatus.style.color = 'var(--accent)';
      } else {
        footerApiStatus.textContent = 'API Key Required';
        footerApiStatus.style.color = 'var(--warning)';
      }
    }
  } catch (err) {
    footerApiStatus.textContent = 'Backend Offline';
    footerApiStatus.style.color = 'var(--danger)';
  }
}

async function loadSettings() {
  try {
    const res = await fetch(`${API_BASE}/api/settings`);
    if (res.ok) {
      const data = await res.json();
      if (data.has_api_key) {
        inputApiKey.placeholder = `Configured (${data.masked_key})`;
      }
      if (data.default_model) {
        selectDefaultModel.value = data.default_model;
      }
      if (data.stream_fps) {
        rangeStreamFps.value = data.stream_fps;
        valStreamFps.textContent = data.stream_fps;
      }
      if (data.stream_quality) {
        rangeStreamQuality.value = data.stream_quality;
        valStreamQuality.textContent = data.stream_quality;
      }
    }
  } catch (err) {
    console.error('Error loading settings', err);
  }
}

async function saveSettings() {
  const payload = {
    default_model: selectDefaultModel.value,
    stream_fps: parseInt(rangeStreamFps.value),
    stream_quality: parseInt(rangeStreamQuality.value)
  };

  const keyVal = inputApiKey.value.trim();
  if (keyVal) {
    payload.nvidia_api_key = keyVal;
  }

  try {
    btnSaveSettings.disabled = true;
    btnSaveSettings.textContent = 'Saving...';
    const res = await fetch(`${API_BASE}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (res.ok) {
      closeModal('settings-modal');
      checkBackendStatus();
      alert('Settings updated successfully!');
      inputApiKey.value = '';
    }
  } catch (err) {
    alert('Failed to save settings: ' + err.message);
  } finally {
    btnSaveSettings.disabled = false;
    btnSaveSettings.textContent = 'Save Settings';
  }
}

// --- QR Code & Phone Remote Pairing ---
async function showQrModal() {
  openModal('qr-modal');
  try {
    const res = await fetch(`${API_BASE}/api/network-info`);
    if (res.ok) {
      const data = await res.json();
      remoteUrlDisplay.textContent = data.remote_url;

      // Render QR Code using QRious
      if (window.QRious) {
        new QRious({
          element: document.getElementById('qr-canvas'),
          value: data.remote_url,
          size: 220,
          background: '#ffffff',
          foreground: '#000000',
          level: 'H'
        });
      }
    }
  } catch (err) {
    remoteUrlDisplay.textContent = 'http://localhost:8000/remote';
  }
}

function copyRemoteUrl() {
  const text = remoteUrlDisplay.textContent;
  navigator.clipboard.writeText(text).then(() => {
    btnCopyUrl.textContent = 'Copied!';
    setTimeout(() => { btnCopyUrl.textContent = 'Copy'; }, 2000);
  });
}

function openRemoteInBrowser() {
  window.open(remoteUrlDisplay.textContent, '_blank');
}

function initRemotePolling() {
  // Connect via WebSocket to monitor remote phone connection state
  try {
    const ws = new WebSocket(`ws://localhost:8000/ws/remote`);
    ws.onopen = () => {
      console.log('[Desktop App] Connected to WebSocket monitor');
    };
    ws.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d.type === 'status' && d.connected) {
          updatePhoneStatus(true);
        }
      } catch (err) {}
    };
    ws.onclose = () => {
      setTimeout(initRemotePolling, 3000);
    };
  } catch (e) {
    setTimeout(initRemotePolling, 3000);
  }
}

function updatePhoneStatus(connected) {
  phoneConnected = connected;
  if (connected) {
    phoneStatusDot.classList.add('connected');
    phoneStatusBadge.classList.add('connected');
    phoneStatusBadge.textContent = 'Connected';
    const modalText = document.getElementById('modal-conn-status-text');
    if (modalText) modalText.textContent = '📱 Phone Remote Active & Synced!';
  } else {
    phoneStatusDot.classList.remove('connected');
    phoneStatusBadge.classList.remove('connected');
    phoneStatusBadge.textContent = 'Standby';
  }
}

// --- PiP Screen Mirror ---
function toggleScreenPip() {
  if (screenPipPanel.style.display === 'none') {
    screenPipPanel.style.display = 'block';
    refreshPipFrame();
    pipInterval = setInterval(refreshPipFrame, 800);
  } else {
    screenPipPanel.style.display = 'none';
    if (pipInterval) clearInterval(pipInterval);
  }
}

function refreshPipFrame() {
  fetch(`${API_BASE}/api/screenshot?scale=0.4&quality=50`)
    .then(r => r.blob())
    .then(blob => {
      pipScreenImg.src = URL.createObjectURL(blob);
    })
    .catch(() => {});
}

// --- Chat & LLM Interaction ---
function applySuggestion(text) {
  promptTextarea.value = text;
  promptTextarea.dispatchEvent(new Event('input'));
  handleSendMessage();
}

function resetChat() {
  chatHistory = [];
  messageThread.innerHTML = '';
  emptyState.style.display = 'flex';
  promptTextarea.value = '';
  promptTextarea.style.height = 'auto';
  sendBtn.disabled = true;
}

async function handleSendMessage() {
  const text = promptTextarea.value.trim();
  if (!text || isGenerating) return;

  // Hide empty state
  emptyState.style.display = 'none';

  // Append user message
  appendMessage('user', text);
  chatHistory.push({ role: 'user', content: text });

  promptTextarea.value = '';
  promptTextarea.style.height = 'auto';
  sendBtn.disabled = true;
  isGenerating = true;

  // Append assistant message container with thinking indicator
  const assistantBubble = appendMessage('assistant', 'Thinking...');
  assistantBubble.classList.add('thinking');

  try {
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: chatHistory,
        model: currentModel,
        permission_mode: currentPermissionMode
      })
    });

    const data = await res.json();
    assistantBubble.classList.remove('thinking');

    if (data.error) {
      assistantBubble.textContent = `❌ Error: ${data.error}`;
      chatHistory.push({ role: 'assistant', content: data.error });
    } else {
      let content = data.content || '';
      assistantBubble.innerHTML = formatMarkdown(content);

      // Render interactive PPT cards if recent presentations are returned
      if (data.ppt_files && data.ppt_files.length > 0) {
        const pptContainer = document.createElement('div');
        pptContainer.className = 'ppt-cards-container';
        data.ppt_files.forEach(f => {
          const card = document.createElement('div');
          card.className = 'ppt-card';
          card.innerHTML = `
            <div class="ppt-card-left">
              <div class="ppt-icon">📊</div>
              <div class="ppt-meta">
                <div class="ppt-title" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
                <div class="ppt-details">
                  <span>🕒 ${escapeHtml(f.modified_display)}</span>
                  <span>💾 ${escapeHtml(f.size_display)}</span>
                  ${f.slide_count !== null ? `<span>📑 ${f.slide_count} slides</span>` : ''}
                </div>
                <div class="ppt-path">${escapeHtml(f.path)}</div>
              </div>
            </div>
            <button class="btn btn-primary btn-sm ppt-select-btn">Select & Edit</button>
          `;
          card.querySelector('.ppt-select-btn').addEventListener('click', () => {
            selectPptFile(f.path, f.name);
          });
          pptContainer.appendChild(card);
        });
        assistantBubble.appendChild(pptContainer);
      }


      // Display rich OS action cards and widgets if any were executed
      if (data.actions && data.actions.length > 0) {
        const actionContainer = document.createElement('div');
        actionContainer.style.marginTop = '8px';
        renderOsActions(data.actions, actionContainer);
        assistantBubble.appendChild(actionContainer);
      }

      chatHistory.push({ role: 'assistant', content: content });
    }
  } catch (err) {
    assistantBubble.classList.remove('thinking');
    assistantBubble.textContent = `❌ Network Error: Could not connect to OS Agent backend (${err.message}). Is the FastAPI server running?`;
  } finally {
    isGenerating = false;
    promptTextarea.focus();
    chatViewport.scrollTop = chatViewport.scrollHeight;
  }
}

function appendMessage(role, content) {
  const row = document.createElement('div');
  row.className = `msg-row ${role}`;

  if (role === 'assistant') {
    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = 'AI';
    row.appendChild(avatar);
  }

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.innerHTML = role === 'user' ? escapeHtml(content) : formatMarkdown(content);
  row.appendChild(bubble);

  messageThread.appendChild(row);
  chatViewport.scrollTop = chatViewport.scrollHeight;
  return bubble;
}

function formatMarkdown(text) {
  // Simple clean markdown parser for code blocks and bold
  let safe = escapeHtml(text);
  safe = safe.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
  safe = safe.replace(/`([^`]+)`/g, '<code>$1</code>');
  safe = safe.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  safe = safe.replace(/\*([^*]+)\*/g, '<i>$1</i>');
  safe = safe.replace(/\n/g, '<br>');
  return safe;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Modal Helpers
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'flex';
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}


function selectPptFile(path, name) {
  promptTextarea.value = `Select PPT: ${name}`;
  handleSendMessage();
}

function updatePermStatusUI(mode) {
  if (!permDot || !permStatusText) return;
  permDot.className = 'perm-dot';
  if (mode === 'full_access') {
    permDot.classList.add('active');
    permStatusText.textContent = 'Granted Once (Full Access)';
    if (permStatusIndicator) permStatusIndicator.style.color = 'var(--accent)';
  } else if (mode === 'balanced') {
    permDot.classList.add('balanced');
    permStatusText.textContent = 'Balanced (Auto-run safe)';
    if (permStatusIndicator) permStatusIndicator.style.color = '#38bdf8';
  } else if (mode === 'ask_permission') {
    permDot.classList.add('ask');
    permStatusText.textContent = 'Ask Every Step';
    if (permStatusIndicator) permStatusIndicator.style.color = '#f59e0b';
  } else if (mode === 'read_only') {
    permDot.classList.add('readonly');
    permStatusText.textContent = 'Read-Only Mode';
    if (permStatusIndicator) permStatusIndicator.style.color = '#94a3b8';
  }
}

function renderOsActions(actions, container) {
  actions.forEach(act => {
    const actType = (act.type || '').toLowerCase();

    // 1. Telemetry Widget
    if ((actType === 'system_info' || act.system_info) && act.system_info) {
      const si = act.system_info;
      const card = document.createElement('div');
      card.className = 'os-card';
      const cpu = si.cpu || {};
      const mem = si.memory || {};
      const disks = si.disks || [];
      const net = si.network || {};

      let disksHtml = '';
      disks.forEach(d => {
        disksHtml += `
          <div class="os-meter-row">
            <span class="os-meter-label">Drive ${escapeHtml(d.mountpoint)}</span>
            <div class="os-meter-bar"><div class="os-meter-fill" style="width: ${d.percent_used}%; background: ${d.percent_used > 85 ? '#ef4444' : '#10a37f'};"></div></div>
            <span class="os-meter-val">${d.free_gb} GB free</span>
          </div>
        `;
      });

      card.innerHTML = `
        <div class="os-card-header">
          <span>🖥️ Windows System Telemetry</span>
          <span class="os-badge-pill">Uptime: ${escapeHtml(si.uptime || 'Active')}</span>
        </div>
        <div class="os-card-body">
          <div class="os-meter-row">
            <span class="os-meter-label">CPU Usage</span>
            <div class="os-meter-bar"><div class="os-meter-fill" style="width: ${cpu.percent || 0}%; background: ${cpu.percent > 80 ? '#ef4444' : '#38bdf8'};"></div></div>
            <span class="os-meter-val">${cpu.percent || 0}%</span>
          </div>
          <div class="os-meter-row">
            <span class="os-meter-label">RAM (${mem.used_gb || 0}G/${mem.total_gb || 0}G)</span>
            <div class="os-meter-bar"><div class="os-meter-fill" style="width: ${mem.percent || 0}%; background: ${mem.percent > 85 ? '#f59e0b' : '#a855f7'};"></div></div>
            <span class="os-meter-val">${mem.percent || 0}%</span>
          </div>
          ${disksHtml}
          <div style="margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap;">
            <span class="os-badge-pill">Host: ${escapeHtml(net.hostname || 'localhost')}</span>
            <span class="os-badge-pill">IP: ${escapeHtml(net.local_ip || '127.0.0.1')}</span>
            ${si.battery ? `<span class="os-badge-pill">Battery: ${si.battery.percent}% (${si.battery.power_plugged ? 'Plugged' : 'Battery'})</span>` : ''}
          </div>
        </div>
      `;
      container.appendChild(card);
    }

    // 2. Process Manager Widget
    else if ((actType === 'get_processes' || act.processes) && act.processes && act.processes.length > 0) {
      const card = document.createElement('div');
      card.className = 'os-card';
      let rows = '';
      act.processes.slice(0, 10).forEach(p => {
        rows += `
          <tr>
            <td><b>${escapeHtml(p.name)}</b></td>
            <td><code>${p.pid}</code></td>
            <td><b>${p.memory_mb} MB</b></td>
            <td><button class="os-proc-kill-btn" onclick="applySuggestion('kill process ${escapeHtml(p.name)}')">Kill</button></td>
          </tr>
        `;
      });
      card.innerHTML = `
        <div class="os-card-header">
          <span>⚙️ Running Windows Processes (Top by RAM)</span>
          <span class="os-badge-pill">${act.processes.length} Active</span>
        </div>
        <div class="os-card-body" style="padding: 0;">
          <table class="os-proc-table">
            <thead>
              <tr><th>Process</th><th>PID</th><th>Memory</th><th>Action</th></tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
      container.appendChild(card);
    }

    // 3. Terminal & Shell Output Widget
    else if ((actType === 'shell' || actType === 'command' || actType === 'powershell') && (act.stdout !== undefined || act.stderr !== undefined || act.command)) {
      const card = document.createElement('div');
      card.className = 'os-card';
      const isSuccess = act.exit_code === 0;
      const cmdText = act.command || 'PowerShell Command';
      const output = (act.stdout || '') + (act.stderr ? ('\n[STDERR]\n' + act.stderr) : '');
      card.innerHTML = `
        <div class="os-card-header">
          <span>⚡ PowerShell: <code>${escapeHtml(cmdText)}</code></span>
          <span class="os-badge-pill" style="color: ${isSuccess ? '#10a37f' : '#ef4444'};">Exit: ${act.exit_code !== undefined ? act.exit_code : 0}</span>
        </div>
        <div class="os-card-body" style="padding: 8px 12px;">
          <div class="os-terminal-box">${escapeHtml(output.trim() || '(No console output)')}</div>
        </div>
      `;
      container.appendChild(card);
    }

    // 4. File Inspector Widget
    else if ((actType === 'read_file' || act.file_data) && act.file_data && act.file_data.content !== undefined) {
      const fd = act.file_data;
      const card = document.createElement('div');
      card.className = 'os-card';
      card.innerHTML = `
        <div class="os-card-header">
          <span>📄 ${escapeHtml(fd.file_name)} (${escapeHtml(fd.size_display || '')})</span>
          <span class="os-badge-pill">${fd.total_lines} lines</span>
        </div>
        <div class="os-card-body" style="padding: 8px 12px;">
          <div class="os-terminal-box" style="color: #e2e8f0;">${escapeHtml(fd.content)}</div>
        </div>
      `;
      container.appendChild(card);
    }

    // 5. Default Compact OS Badge
    else {
      const badge = document.createElement('div');
      badge.className = 'action-badge';
      let label = `⚡ ${actType}`;
      if (actType === 'open_app') label = `🚀 Launched: ${act.app || act.target || ''}`;
      else if (actType === 'type') label = `⌨️ Typed text`;
      else if (actType === 'press') label = `⌨️ Key: ${act.key}`;
      else if (actType === 'hotkey') label = `⚡ Hotkey: ${(act.keys || []).join('+')}`;
      else if (actType === 'click') label = `🖱️ Clicked: (${act.x}, ${act.y})`;
      else if (actType === 'kill_process') label = `🛑 Kill Process: ${act.name || act.pid || ''}`;
      badge.textContent = label;
      container.appendChild(badge);
    }
  });
}


