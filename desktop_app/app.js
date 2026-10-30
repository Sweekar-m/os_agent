// Dynamic Backend API Base URL
const API_BASE = (window.location && window.location.protocol && window.location.protocol.startsWith('http'))
  ? window.location.origin
  : 'http://127.0.0.1:8000';

let currentModel = 'nvidia/nemotron-3.5-lightning-30b-a3b';
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

// Agent Status Pills
const agentPillBrowser = document.getElementById('agent-pill-browser');
const agentPillFile = document.getElementById('agent-pill-file');
const agentPillSystem = document.getElementById('agent-pill-system');
const agentPillVision = document.getElementById('agent-pill-vision');

// Emergency Stop
const btnEmergencyStop = document.getElementById('btn-emergency-stop');
const emergencyStopText = document.getElementById('emergency-stop-text');
let isEmergencyStopped = false;

// Speech: STT & TTS
const btnDesktopMic = document.getElementById('btn-desktop-mic');
const micIcon = document.getElementById('mic-icon');
const btnDesktopTts = document.getElementById('btn-desktop-tts');
const ttsIcon = document.getElementById('tts-icon');
let speechRecognition = null;
let isListening = false;
let ttsEnabled = localStorage.getItem('os_agent_tts') !== 'false';

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
const selectLlmProvider = document.getElementById('select-llm-provider');
const inputApiKey = document.getElementById('input-api-key');
const btnToggleKeyVis = document.getElementById('btn-toggle-key-vis');
const inputBaseUrl = document.getElementById('input-base-url');
const inputChatModel = document.getElementById('input-chat-model');
const inputVisionModel = document.getElementById('input-vision-model');
const settingsPairingPin = document.getElementById('settings-pairing-pin');
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

// Backend Status Banner
const bannerEl = document.getElementById('backend-status-banner');
const bannerIcon = document.getElementById('banner-icon');
const bannerText = document.getElementById('banner-text');
const bannerRetryBtn = document.getElementById('banner-retry-btn');
let backendState = 'startup';
let reconnectAttempts = 0;
let healthPollTimer = null;
let activeEventSources = {};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
  initEventListeners();
  updateBackendState('startup', 'Connecting to OS Agent backend...');
  startHealthMonitor();
  initAgentStatusPolling();
  initEmergencyStop();
  initSpeech();
  loadSettings();
  initRemotePolling();
  recoverActiveTask();
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

  if (bannerRetryBtn) {
    bannerRetryBtn.addEventListener('click', () => {
      updateBackendState('recovering', 'Checking backend connection...');
      checkBackendStatus();
    });
  }
}

// --- Status, Agents & Emergency Stop ---
function updateBackendState(state, message = '') {
  backendState = state;
  if (!bannerEl) return;

  if (state === 'ready') {
    reconnectAttempts = 0;
    bannerEl.className = 'backend-banner ready';
    if (bannerIcon) bannerIcon.textContent = '✅';
    if (bannerText) bannerText.textContent = 'Backend Online & Ready';
    if (bannerRetryBtn) bannerRetryBtn.style.display = 'none';
    if (sendBtn) sendBtn.disabled = !promptTextarea.value.trim() || isGenerating;
    setTimeout(() => {
      if (backendState === 'ready') {
        bannerEl.style.display = 'none';
      }
    }, 2000);
  } else if (state === 'startup') {
    bannerEl.style.display = 'flex';
    bannerEl.className = 'backend-banner';
    if (bannerIcon) bannerIcon.textContent = '🔄';
    if (bannerText) bannerText.textContent = message || 'Starting OS Agent backend services...';
    if (bannerRetryBtn) bannerRetryBtn.style.display = 'none';
  } else if (state === 'disconnected') {
    bannerEl.style.display = 'flex';
    bannerEl.className = 'backend-banner error';
    if (bannerIcon) bannerIcon.textContent = '⚠️';
    if (bannerText) bannerText.textContent = message || 'Backend disconnected. Reconnecting...';
    if (bannerRetryBtn) bannerRetryBtn.style.display = 'inline-flex';
  } else if (state === 'recovering') {
    bannerEl.style.display = 'flex';
    bannerEl.className = 'backend-banner';
    if (bannerIcon) bannerIcon.textContent = '🔄';
    if (bannerText) bannerText.textContent = message || 'Attempting automatic recovery...';
    if (bannerRetryBtn) bannerRetryBtn.style.display = 'none';
  }
}

async function checkBackendStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/health/ready`, { cache: 'no-store' });
    if (res.ok) {
      const readyData = await res.json();
      if (readyData.ready) {
        updateBackendState('ready');
      } else {
        updateBackendState('startup', 'Initializing database and agents...');
      }

      const statusRes = await fetch(`${API_BASE}/api/status`);
      if (statusRes.ok) {
        const data = await statusRes.json();
        if (data.emergency_stopped) {
          setEmergencyStopUI(true);
        }
        if (data.has_api_key) {
          footerApiStatus.textContent = `${(data.provider || 'LLM').toUpperCase()} Active`;
          footerApiStatus.style.color = 'var(--accent)';
        } else {
          footerApiStatus.textContent = 'API Key Required';
          footerApiStatus.style.color = 'var(--warning)';
        }
      }
      return true;
    } else {
      throw new Error(`HTTP ${res.status}`);
    }
  } catch (err) {
    reconnectAttempts++;
    const delaySec = Math.min(10, Math.pow(1.5, reconnectAttempts));
    updateBackendState('disconnected', `Network connection lost (${err.message}). Retrying in ${Math.round(delaySec)}s...`);
    footerApiStatus.textContent = 'Backend Offline';
    footerApiStatus.style.color = 'var(--danger)';
    return false;
  }
}

function startHealthMonitor() {
  const poll = async () => {
    const ok = await checkBackendStatus();
    const delay = ok ? 4000 : Math.min(10000, 1000 * Math.pow(1.5, reconnectAttempts));
    healthPollTimer = setTimeout(poll, delay);
  };
  poll();
}

function initAgentStatusPolling() {
  fetchAgentStatus();
  setInterval(fetchAgentStatus, 4000);
}

async function fetchAgentStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/agents/status`);
    if (res.ok) {
      const data = await res.json();
      if (agentPillBrowser && data.browser_agent) {
        const count = data.browser_agent.open_browsers_count || 0;
        agentPillBrowser.title = `Browser Agent: ${count} browser window(s) open (Existing Chrome/Edge)`;
      }
      if (agentPillFile && data.file_agent) {
        agentPillFile.title = `File Agent: Workspace ready at ${data.file_agent.workspace}`;
      }
      if (agentPillSystem && data.system_agent) {
        agentPillSystem.title = `System Agent: Windows OS with ${data.system_agent.cpu_count || 'multi'} cores`;
      }
      if (agentPillVision && data.vision_agent) {
        const win = data.vision_agent.active_window || 'Desktop';
        agentPillVision.title = `Vision Agent: Active [${win}] | Res ${data.vision_agent.screen_dimensions.join('x')}`;
      }
    }
  } catch (err) {
    // Backend offline or reconnecting
  }
}

function initEmergencyStop() {
  if (!btnEmergencyStop) return;

  btnEmergencyStop.addEventListener('click', async () => {
    if (isEmergencyStopped) {
      // Clear emergency stop
      try {
        const res = await fetch(`${API_BASE}/api/emergency-stop/clear`, { method: 'POST' });
        if (res.ok) {
          setEmergencyStopUI(false);
          appendMessage('assistant', '✅ Emergency Stop cleared. Agent operations resumed.');
        }
      } catch (err) {
        alert('Failed to clear emergency stop: ' + err.message);
      }
    } else {
      // Trigger emergency stop
      if (!confirm('🛑 Trigger Emergency Stop? This immediately terminates active agent operations and blocks further execution.')) {
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/api/emergency-stop`, { method: 'POST' });
        if (res.ok) {
          setEmergencyStopUI(true);
          appendMessage('assistant', '🛑 EMERGENCY STOP ACTIVATED: All agent actions halted. Approvals cancelled.');
        }
      } catch (err) {
        alert('Failed to activate emergency stop: ' + err.message);
      }
    }
  });
}

function setEmergencyStopUI(stopped) {
  isEmergencyStopped = stopped;
  if (!btnEmergencyStop) return;
  if (stopped) {
    btnEmergencyStop.classList.add('stopped');
    if (emergencyStopText) emergencyStopText.textContent = 'STOPPED (Resume)';
  } else {
    btnEmergencyStop.classList.remove('stopped');
    if (emergencyStopText) emergencyStopText.textContent = 'Emergency Stop';
  }
}

// --- Speech: STT & TTS ---
function initSpeech() {
  // TTS Button
  if (btnDesktopTts) {
    if (!ttsEnabled) btnDesktopTts.classList.add('muted');
    btnDesktopTts.addEventListener('click', () => {
      ttsEnabled = !ttsEnabled;
      localStorage.setItem('os_agent_tts', ttsEnabled ? 'true' : 'false');
      btnDesktopTts.classList.toggle('muted', !ttsEnabled);
      if (ttsIcon) ttsIcon.textContent = ttsEnabled ? '🔊' : '🔇';
      if (!ttsEnabled) {
        stopDesktopSpeech();
      }
    });
  }

  // STT Microphone Button
  if (btnDesktopMic) {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRec) {
      speechRecognition = new SpeechRec();
      speechRecognition.continuous = false;
      speechRecognition.interimResults = false;
      speechRecognition.lang = 'en-US';

      speechRecognition.onresult = (e) => {
        const transcript = e.results[0][0].transcript;
        promptTextarea.value = transcript;
        promptTextarea.dispatchEvent(new Event('input'));
        stopListening();
        // Focus prompt
        promptTextarea.focus();
      };

      speechRecognition.onerror = (e) => {
        console.warn('[STT Error]', e);
        stopListening();
      };

      speechRecognition.onend = () => {
        stopListening();
      };

      btnDesktopMic.addEventListener('click', () => {
        if (isListening) {
          speechRecognition.stop();
          stopListening();
        } else {
          try {
            speechRecognition.start();
            isListening = true;
            btnDesktopMic.classList.add('listening');
          } catch (err) {
            console.error('Speech recognition start failed', err);
            stopListening();
          }
        }
      });
    } else {
      btnDesktopMic.title = 'Speech-to-Text not supported in this browser';
      btnDesktopMic.style.opacity = '0.5';
    }
  }
}

function stopListening() {
  isListening = false;
  if (btnDesktopMic) btnDesktopMic.classList.remove('listening');
}

let currentDesktopAudio = null;
let lastDesktopSpokenText = '';
let lastDesktopSpokenTime = 0;

function stopDesktopSpeech() {
  if (currentDesktopAudio) {
    try {
      currentDesktopAudio.pause();
      currentDesktopAudio.currentTime = 0;
    } catch (e) {}
    currentDesktopAudio = null;
  }
  if (window.speechSynthesis) {
    try {
      window.speechSynthesis.cancel();
    } catch (e) {}
  }
}

function speakText(text, audioUrl = null) {
  if (!ttsEnabled || !text) return;

  const now = Date.now();
  const cleanNorm = text.trim().toLowerCase();
  // Deduplicate recent speech within 15 seconds
  if (cleanNorm === lastDesktopSpokenText && (now - lastDesktopSpokenTime) < 15000) {
    console.log('[Desktop TTS] Skipping duplicate spoken text');
    return;
  }
  lastDesktopSpokenText = cleanNorm;
  lastDesktopSpokenTime = now;

  // Interrupt previous speech
  stopDesktopSpeech();

  if (audioUrl) {
    try {
      const fullAudioUrl = audioUrl.startsWith('http') ? audioUrl : `${API_BASE}${audioUrl}`;
      currentDesktopAudio = new Audio(fullAudioUrl);
      currentDesktopAudio.play().catch((err) => {
        console.warn('[Desktop Neural Audio Error, falling back to Web Speech]', err);
        speakDesktopWebSpeech(text);
      });
      return;
    } catch (err) {
      console.warn('[Desktop Audio init error]', err);
    }
  }

  speakDesktopWebSpeech(text);
}

function speakDesktopWebSpeech(text) {
  if (!ttsEnabled || !window.speechSynthesis || !text) return;
  try {
    const clean = text.replace(/```[\s\S]*?```/g, '').replace(/[*_#`[\]()]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!clean) return;
    const utterance = new SpeechSynthesisUtterance(clean.substring(0, 300));
    utterance.rate = 1.05;
    utterance.pitch = 1.0;
    window.speechSynthesis.speak(utterance);
  } catch (err) {
    console.warn('[TTS Error]', err);
  }
}

async function loadSettings() {
  try {
    const res = await fetch(`${API_BASE}/api/settings`);
    if (res.ok) {
      const data = await res.json();
      if (selectLlmProvider && data.llm_provider) {
        selectLlmProvider.value = data.llm_provider;
      }
      if (inputApiKey && data.has_api_key) {
        inputApiKey.placeholder = `Configured (${data.masked_key})`;
      }
      if (inputBaseUrl && data.llm_base_url) {
        inputBaseUrl.value = data.llm_base_url;
      }
      if (inputChatModel && data.llm_model) {
        inputChatModel.value = data.llm_model;
      }
      if (inputVisionModel && data.llm_vision_model) {
        inputVisionModel.value = data.llm_vision_model;
      }
      if (settingsPairingPin && data.remote_pairing_pin) {
        settingsPairingPin.textContent = data.remote_pairing_pin;
      }
      if (rangeStreamFps && data.stream_fps) {
        rangeStreamFps.value = data.stream_fps;
        valStreamFps.textContent = data.stream_fps;
      }
      if (rangeStreamQuality && data.stream_quality) {
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
    stream_fps: parseInt(rangeStreamFps.value),
    stream_quality: parseInt(rangeStreamQuality.value)
  };

  if (selectLlmProvider) payload.llm_provider = selectLlmProvider.value;
  if (inputBaseUrl && inputBaseUrl.value.trim()) payload.llm_base_url = inputBaseUrl.value.trim();
  if (inputChatModel && inputChatModel.value.trim()) payload.llm_model = inputChatModel.value.trim();
  if (inputVisionModel && inputVisionModel.value.trim()) payload.llm_vision_model = inputVisionModel.value.trim();

  const keyVal = inputApiKey.value.trim();
  if (keyVal) {
    payload.llm_api_key = keyVal;
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

// --- Background Task Progress & Real-Time SSE (Redesigned) ---

function copyTextToClipboard(text, btnEl, successLabel = 'Copied!') {
  const showFeedback = () => {
    if (btnEl) {
      const origHtml = btnEl.innerHTML;
      btnEl.innerHTML = `<span>✓</span> ${escapeHtml(successLabel)}`;
      btnEl.style.borderColor = 'rgba(16, 185, 129, 0.4)';
      btnEl.style.color = '#34d399';
      setTimeout(() => {
        btnEl.innerHTML = origHtml;
        btnEl.style.borderColor = '';
        btnEl.style.color = '';
      }, 1800);
    }
  };

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(showFeedback).catch(() => {
      fallbackCopyText(text, showFeedback);
    });
  } else {
    fallbackCopyText(text, showFeedback);
  }
}

function fallbackCopyText(text, cb) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand('copy');
    if (cb) cb();
  } catch (e) {
    console.warn('Fallback copy failed:', e);
  }
  document.body.removeChild(ta);
}

function renderTaskCard(container, taskId, promptText, initialStatus = 'QUEUED', taskData = null) {
  container.innerHTML = '';
  const card = document.createElement('div');
  const normStatus = (initialStatus || 'QUEUED').toUpperCase();
  const isFinished = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(normStatus);
  card.className = `executing-card ${isFinished ? 'completed' : ''}`;
  card.id = `task-card-${taskId}`;

  let reportHtml = '';
  if (taskData && taskData.completion_report && taskData.completion_report.summary) {
    reportHtml = formatMarkdown(taskData.completion_report.summary);
  } else if (normStatus === 'FAILED' && taskData && taskData.error) {
    reportHtml = `<span style="color:#f87171;">${escapeHtml(taskData.error)}</span>`;
  }

  card.innerHTML = `
    <div class="executing-header" id="task-header-${taskId}" style="${isFinished ? 'display:none;' : ''}">
      <div class="executing-main-row">
        <div class="executing-spinner" id="task-spinner-${taskId}"></div>
        <span class="executing-status-text" id="task-status-text-${taskId}">Executing...</span>
      </div>
      <button class="btn-cancel-simple" id="btn-cancel-task-${taskId}">Cancel</button>
    </div>
    <div class="executing-step-text" id="task-step-text-${taskId}" style="${isFinished ? 'display:none;' : ''}">
      ${escapeHtml(promptText || '')}
    </div>
    <div id="approval-slot-${taskId}"></div>
    <div id="task-result-${taskId}" class="task-result-simple" style="${isFinished ? 'display:block;' : 'display:none;'}">
      <div id="outcome-content-${taskId}">${reportHtml}</div>
    </div>
  `;

  container.appendChild(card);

  // Cancel Button Event
  const cancelBtn = card.querySelector(`#btn-cancel-task-${taskId}`);
  if (cancelBtn) {
    cancelBtn.addEventListener('click', async () => {
      cancelBtn.disabled = true;
      cancelBtn.textContent = 'Cancelling...';
      try {
        await fetch(`${API_BASE}/api/tasks/${taskId}/cancel`, { method: 'POST' });
      } catch (e) {
        console.warn('Cancel request error:', e);
      }
    });
  }

  return card;
}

function subscribeToTaskEvents(taskId, container, promptText) {
  if (activeEventSources[taskId]) {
    activeEventSources[taskId].close();
  }

  localStorage.setItem('os_agent_active_task_id', taskId);

  const card = document.getElementById(`task-card-${taskId}`);
  const header = document.getElementById(`task-header-${taskId}`);
  const spinner = document.getElementById(`task-spinner-${taskId}`);
  const statusText = document.getElementById(`task-status-text-${taskId}`);
  const stepText = document.getElementById(`task-step-text-${taskId}`);
  const cancelBtn = document.getElementById(`btn-cancel-task-${taskId}`);
  const approvalSlot = document.getElementById(`approval-slot-${taskId}`);
  const resultDiv = document.getElementById(`task-result-${taskId}`);
  const outcomeContent = document.getElementById(`outcome-content-${taskId}`);

  const removeApprovalBanner = () => {
    if (approvalSlot) approvalSlot.innerHTML = '';
  };

  const renderApprovalBanner = (apprData) => {
    removeApprovalBanner();
    if (!approvalSlot) return;

    const box = document.createElement('div');
    box.className = 'approval-simple-box';
    box.id = `approval-box-${taskId}`;

    box.innerHTML = `
      <div class="approval-simple-title">⚠️ Action Requires Authorization</div>
      <div class="approval-simple-desc">
        <b>Action:</b> ${escapeHtml(apprData.step || apprData.action || 'OS Operation')}<br>
        <span style="opacity:0.85;">${escapeHtml(apprData.reason || 'This operation requires authorization under security policy.')}</span>
      </div>
      <div class="approval-simple-actions">
        <button class="btn-appr-allow" id="btn-appr-allow-${taskId}">Allow</button>
        <button class="btn-appr-deny" id="btn-appr-deny-${taskId}">Deny</button>
      </div>
    `;

    approvalSlot.appendChild(box);

    const sendDecision = async (decision) => {
      const apprId = apprData.approval_id || apprData.id;
      if (!apprId) return;
      try {
        const btns = box.querySelectorAll('button');
        btns.forEach(b => { b.disabled = true; b.style.opacity = '0.5'; });
        await fetch(`${API_BASE}/api/approvals/${apprId}/respond`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ decision })
        });
        removeApprovalBanner();
        if (statusText) statusText.textContent = 'Executing...';
      } catch (err) {
        console.warn('Failed to respond to approval:', err);
      }
    };

    box.querySelector(`#btn-appr-allow-${taskId}`)?.addEventListener('click', () => sendDecision('allow_once'));
    box.querySelector(`#btn-appr-deny-${taskId}`)?.addEventListener('click', () => sendDecision('deny'));
  };

  const evtSource = new EventSource(`${API_BASE}/api/tasks/${taskId}/events`);
  activeEventSources[taskId] = evtSource;

  evtSource.onmessage = (e) => {
    try {
      const payload = JSON.parse(e.data);
      const evtType = payload.event_type;
      const data = payload.data || payload.payload || {};

      if (evtType === 'PHASE_CHANGED') {
        const phase = (data.phase || 'EXECUTING').toUpperCase();
        if (statusText) {
          statusText.textContent = phase === 'PLANNING' ? 'Planning...' : 'Executing...';
        }
      } else if (evtType === 'APPROVAL_REQUIRED') {
        if (statusText) statusText.textContent = 'Action Requires Authorization';
        renderApprovalBanner(data);
      } else if (evtType === 'PLAN_GENERATED') {
        if (statusText) statusText.textContent = 'Executing...';
        const steps = data.steps || [];
        if (steps.length > 0 && stepText) {
          stepText.textContent = steps[0].title || steps[0].action || promptText;
        }
      } else if (evtType === 'ACTION_EXECUTING') {
        removeApprovalBanner();
        if (statusText) statusText.textContent = 'Executing...';
        if (stepText) {
          const act = data.action || data.step_title || (data.step_index !== undefined ? `Executing step ${data.step_index + 1}...` : 'Executing action...');
          stepText.textContent = act;
        }
      } else if (evtType === 'ACTION_COMPLETED') {
        if (stepText && (data.summary || data.error)) {
          stepText.textContent = data.summary || data.error;
        }
      } else if (evtType === 'TASK_COMPLETED') {
        removeApprovalBanner();
        if (header) header.style.display = 'none';
        if (stepText) stepText.style.display = 'none';
        if (card) card.classList.add('completed');
        if (resultDiv) {
          resultDiv.style.display = 'block';
          if (outcomeContent) {
            outcomeContent.innerHTML = formatMarkdown(data.summary || 'Task completed successfully.');
          }
        }
        localStorage.removeItem('os_agent_active_task_id');
        evtSource.close();
        delete activeEventSources[taskId];

        const sp = data.spoken_summary || data.summary;
        if (sp) speakText(sp);
      } else if (evtType === 'TASK_FAILED') {
        removeApprovalBanner();
        if (spinner) spinner.style.display = 'none';
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (statusText) statusText.textContent = '❌ Execution Failed';
        if (stepText) stepText.textContent = data.error || 'Execution encountered an error.';
        if (resultDiv) {
          resultDiv.style.display = 'block';
          if (outcomeContent) {
            outcomeContent.innerHTML = `<span style="color:#f87171;">${escapeHtml(data.error || 'Execution failed.')}</span>`;
          }
        }
        localStorage.removeItem('os_agent_active_task_id');
        evtSource.close();
        delete activeEventSources[taskId];
      } else if (evtType === 'TASK_CANCELLED') {
        removeApprovalBanner();
        if (spinner) spinner.style.display = 'none';
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (statusText) statusText.textContent = '🛑 Cancelled';
        if (stepText) stepText.textContent = data.reason || 'Task was cancelled.';
        localStorage.removeItem('os_agent_active_task_id');
        evtSource.close();
        delete activeEventSources[taskId];
      }
    } catch (err) {
      console.warn('Task SSE parsing note:', err);
    }
  };

  evtSource.onerror = async () => {
    evtSource.close();
    delete activeEventSources[taskId];
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}`);
      if (res.ok) {
        const taskInfo = await res.json();
        const st = taskInfo.status;
        if (st === 'COMPLETED' || st === 'FAILED' || st === 'CANCELLED') {
          localStorage.removeItem('os_agent_active_task_id');
          if (header) header.style.display = 'none';
          if (stepText) stepText.style.display = 'none';
          if (card) card.classList.add('completed');
          if (resultDiv) {
            resultDiv.style.display = 'block';
            if (outcomeContent) {
              outcomeContent.innerHTML = formatMarkdown((taskInfo.completion_report && taskInfo.completion_report.summary) || `Task status: ${st}`);
            }
          }
          return;
        }
      }
    } catch (e) {}

    setTimeout(() => {
      if (localStorage.getItem('os_agent_active_task_id') === taskId) {
        subscribeToTaskEvents(taskId, container, promptText);
      }
    }, 3000);
  };
}

async function recoverActiveTask() {
  const savedTaskId = localStorage.getItem('os_agent_active_task_id');
  if (!savedTaskId) return;

  try {
    const res = await fetch(`${API_BASE}/api/tasks/${savedTaskId}`);
    if (res.ok) {
      const task = await res.json();
      emptyState.style.display = 'none';
      const bubble = appendMessage('assistant', '');
      renderTaskCard(bubble, savedTaskId, task.user_prompt, task.status, task);

      if (['QUEUED', 'PLANNING', 'EXECUTING', 'AWAITING_APPROVAL'].includes(task.status)) {
        subscribeToTaskEvents(savedTaskId, bubble, task.user_prompt);

        // Check if there's an active pending approval for this task
        if (task.status === 'AWAITING_APPROVAL') {
          try {
            const apprRes = await fetch(`${API_BASE}/api/approvals/pending?task_id=${savedTaskId}`);
            if (apprRes.ok) {
              const apprs = await apprRes.json();
              if (Array.isArray(apprs) && apprs.length > 0) {
                const a = apprs[0];
                const bannerData = {
                  approval_id: a.id,
                  step: a.action_type,
                  agent: 'Specialized Agent',
                  action: a.command || a.action_type,
                  reason: a.reason,
                  risk_level: a.risk_level
                };
                setTimeout(() => {
                  const card = document.getElementById(`task-card-${savedTaskId}`);
                  if (card) {
                    const badge = document.getElementById(`task-badge-${savedTaskId}`);
                    if (badge) badge.className = 'task-badge awaiting_approval';
                  }
                }, 100);
              }
            }
          } catch (e) {}
        }
      } else {
        localStorage.removeItem('os_agent_active_task_id');
      }
    } else {
      localStorage.removeItem('os_agent_active_task_id');
    }
  } catch (err) {
    console.warn('Active task recovery note:', err);
  }
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

  stopDesktopSpeech();
  emptyState.style.display = 'none';

  appendMessage('user', text);
  chatHistory.push({ role: 'user', content: text });

  promptTextarea.value = '';
  promptTextarea.style.height = 'auto';
  sendBtn.disabled = true;
  isGenerating = true;

  const assistantBubble = appendMessage('assistant', '');
  assistantBubble.innerHTML = `
    <div class="simple-loading-row">
      <div class="simple-spinner"></div>
      <span class="simple-loading-label">Executing...</span>
    </div>
  `;

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

    // 1. If backend accepted this as a background task
    if (data.async_task && data.task_id) {
      renderTaskCard(assistantBubble, data.task_id, text, data.status || 'QUEUED');
      subscribeToTaskEvents(data.task_id, assistantBubble, text);
      chatHistory.push({ role: 'assistant', content: `[Task ${data.task_id} running in background]` });
      return;
    }

    const errDetail = data.error || data.detail || (!res.ok ? `HTTP ${res.status}: ${res.statusText || 'Server Error'}` : null);
    if (errDetail) {
      assistantBubble.textContent = `❌ Error: ${errDetail}`;
      chatHistory.push({ role: 'assistant', content: errDetail });
    } else {
      let content = data.content || '';
      if (!content && (!data.actions || data.actions.length === 0)) {
        content = "*(Completed with no text output)*";
      }
      assistantBubble.innerHTML = formatMarkdown(content);

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

      if (data.actions && data.actions.length > 0) {
        const actionContainer = document.createElement('div');
        actionContainer.style.marginTop = '8px';
        renderOsActions(data.actions, actionContainer);
        assistantBubble.appendChild(actionContainer);
      }

      chatHistory.push({ role: 'assistant', content: content });

      const textToSpeak = data.spoken_summary || content;
      if (textToSpeak) {
        speakText(textToSpeak, data.audio_url);
      }
    }
  } catch (err) {
    assistantBubble.classList.remove('thinking');
    assistantBubble.textContent = `❌ Network Error: Could not connect to OS Agent backend at ${API_BASE} (${err.message}). Is the FastAPI server running?`;
    updateBackendState('disconnected', `Network connection lost: ${err.message}`);
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


