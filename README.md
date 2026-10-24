# 🖥️ Multi-Agent Operating System Assistant for Windows

A general-purpose, multimodal, multi-agent operating system assistant for Windows. It operates your computer through natural-language text and voice commands, understands the current screen, interacts with existing applications, manages real files, and performs validated system operations with human permission governance.

Powered by a **provider-independent LLM gateway** (NVIDIA NIM, OpenAI, Ollama, or custom OpenAI-compatible endpoints) and four specialized agents.

---

## 🌟 The 4 Specialized Agents

```
                        ┌────────────────────────────────────────┐
                        │   Multi-Agent Central Orchestrator     │
                        │    (LLM Gateway + Planning + Safety)   │
                        └──────────────────┬─────────────────────┘
                                           │
         ┌──────────────────┬──────────────┴─────┬──────────────────┐
         │                  │                    │                  │
         ▼                  ▼                    ▼                  ▼
┌──────────────────┐┌──────────────────┐┌──────────────────┐┌──────────────────┐
│  Browser Agent   ││    File Agent    ││   System Agent   ││   Vision Agent   │
│ Controls EXISTING││ Real files, safe ││ Windows apps,    ││ Multimodal screen│
│ Chrome / Edge    ││ paths, diffs &   ││ process manager, ││ understanding &  │
│ (No Playwright!) ││ automatic backups││ telemetry & shell││ UI element locator│
└──────────────────┘└──────────────────┘└──────────────────┘└──────────────────┘
```

### 1. 🌐 Browser Agent
Controls your **existing** Chrome, Edge, or Brave browser windows and tabs:
- **Strictly NO Playwright** and never launches an isolated headless browser.
- Discovers existing desktop browser windows via Windows user32 APIs.
- Address bar omnibox navigation via `Ctrl+L` / `Alt+D`.
- Tab management: `Ctrl+T` (new tab), `Ctrl+W` (close tab), `Ctrl+Tab` (switch tabs).
- Page scrolling, clicking, and keyboard typing with visual verification.

### 2. 📁 File Agent
Manages real files on your Windows machine with security guards:
- Fast filename and content grep search across authorized workspaces.
- Automatic timestamped backups in `scratch/.file_backups/` before modifying or overwriting files.
- Generates unified diffs for modifications.
- **Delete confirmation guard**: blocks silent deletions (`confirm=True` required).
- Canonical path resolution preventing path traversal and symlink escapes.

### 3. ⚙️ System Agent
Interacts with the real Windows operating system:
- Real-time hardware telemetry: CPU %, RAM used/total, per-drive disk space, and battery status via `psutil`.
- Process inspection and memory management (lists top processes by RAM, terminates hung tasks).
- Launches desktop applications (`notepad`, `calc`, `code`, etc.) and opens paths in default Windows apps.
- Validated PowerShell command execution with strict permission classification.
- System diagnostics and health recommendations.

### 4. 👁️ Vision Agent
Genuine screen understanding, not just screenshot display:
- Captures active window or full desktop with display scaling and DPI awareness.
- Multimodal visual reasoning with vision-capable models (e.g. `meta/llama-3.2-11b-vision-instruct`, `gpt-4o`).
- UI element coordinate localization using Windows UIAutomation (`UIAutomationCore.dll`) with visual coordinate fallback.
- Explains on-screen error dialogs and recommends agent remediations.
- Stale target handling: gracefully degrades if elements move or disappear.

---

## 🛑 Safety, Permissions & Emergency Stop

All operations are governed by a trusted backend security boundary:
- **Default-Deny Policy**: All sensitive operations (file deletion, process termination, arbitrary shell commands, browser control) require explicit user consent.
- **6-Choice Approval Matrix**:
  1. `Allow once`: Authorizes only the single pending action.
  2. `Allow for this session`: Grants category approval for the active task.
  3. `Allow always for this project`: Stores a persistent scoped rule in SQLite.
  4. `Allow full access`: Autonomous mode with high-risk system protections.
  5. `Deny`: Safely rejects the action and allows agent to pivot.
  6. `Cancel`: Halts the task and releases locks.
- **Emergency Stop Button**: Instant system-wide halt (`POST /api/emergency-stop`) that terminates in-flight operations, rejects pending approvals, and blocks future agent actions until explicitly resumed.
- **Secret Masking**: API keys are masked (`nvapi-1...ef12`) and never written to logs.

---

## 📱 Phone Remote Over LAN

Control your laptop from your phone without installing native apps:
- Open `http://<LAN-IP>:8000/remote` in any mobile browser on the same Wi-Fi.
- **Secure 4-Digit PIN Pairing**: Prompts for pairing PIN (`4826`, viewable in Desktop Settings) and returns an authentication token.
- **Low-Latency Live Screen Mirror**: WebSocket stream + instant snapshot sync.
- **Tactile Mini Trackpad**: Drag finger to move laptop cursor, 1-finger tap for left click, 2-finger tap for right click, 2-finger drag to scroll, hold-to-drag button.
- **Voice & Text Commands**: Send prompts directly to the orchestrator via Web Speech API.
- **Spoken Assistant Responses**: Reads task completion summaries aloud using mobile TTS.
- **Emergency Stop**: Dedicated mobile `🛑 STOP` button and warning banner.

---

## 🎙️ Speech Services (STT & TTS)

- **Speech-to-Text (STT)**: Microphone button on desktop and phone using native `webkitSpeechRecognition` / `SpeechRecognition` with server Whisper fallback.
- **Text-to-Speech (TTS)**: Assistant speaks concise, cleaned task summaries using `speechSynthesis`. Code blocks, URLs, and markdown tokens are cleanly filtered for natural human speech.

---

## 📂 Repository Structure

```
d:/project_a/
├── backend/
│   ├── main.py               # FastAPI server, REST API, WebSocket streaming & pairing
│   ├── orchestrator.py       # MultiAgentOrchestrator coordinating the 4 agents
│   ├── config.py             # Settings, multi-provider LLM config & pairing keys
│   ├── llm_gateway.py        # Provider-independent LLM gateway (NVIDIA/OpenAI/Ollama)
│   ├── permission_engine.py  # Default-deny permission engine & emergency stop
│   ├── speech_service.py     # STT/TTS coordination and speech text cleaner
│   ├── input_controller.py   # Windows mouse, keyboard & DPI coordinate controller
│   ├── screen_capture.py     # Fast desktop screen capture via MSS
│   ├── db.py                 # SQLite database for tasks, audit logs, and rules
│   ├── agents/
│   │   ├── browser_agent.py  # Agent 1: Existing Chrome/Edge/Brave window controller
│   │   ├── file_agent.py     # Agent 2: Real Windows file manager with diffs & backups
│   │   ├── system_agent.py   # Agent 3: Windows system, process, telemetry & shell agent
│   │   └── vision_agent.py   # Agent 4: Multimodal vision & UI element locator
│   └── static/
│       └── remote.html       # Mobile phone remote interface with PIN pairing & trackpad
├── desktop_app/
│   ├── index.html            # Desktop interface with Agent status pills & Emergency Stop
│   ├── style.css             # Dark theme design system & widgets
│   ├── app.js                # Frontend controller, STT/TTS, and settings modal
│   └── qrious.min.js         # Offline QR code generator for phone pairing
├── tests/
│   ├── test_multi_agent_system.py # 18 automated tests covering all 4 agents & orchestrator
│   └── test_os_agent.py      # Telemetry, process, and intent detection tests
├── run.bat                   # One-click Windows launcher
└── README.md
```

---

## 🚀 Getting Started

### 1. Launch the System
Double-click `run.bat` or run:
```bat
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
Then open `desktop_app/index.html` in Chrome/Edge, or visit `http://localhost:8000/remote` on your phone.

### 2. Configure LLM Provider
Click **Settings** (⚙️) on the desktop app to select:
- **Provider**: NVIDIA NIM, OpenAI, Ollama, or Custom.
- **API Key**: Enter your provider key (saved locally in `backend/.env`).
- **Models**: Configure Chat model and Vision model.
- **Voice Provider**: Configurable Neural TTS (`edge_tts`, `openai`, `web_speech`).
- **Default Voice**: `en-US-JennyNeural` (Natural US Female) or `en-US-GuyNeural` (Natural US Male).
- **Pairing PIN**: Default `4826`.

### 3. Voice & Conversation Capabilities
- **Lightweight Intent Router**: Distinguishes casual talk, assistant identity, screen questions, browser, file, system operations, and voice commands. Casual small talk goes straight to conversational LLM without spawning 4-agent plans or action blocks.
- **Emoji-Aware Normalization**: Converts emojis contextually (`Task completed ✅` -> `"Task completed successfully."`, `I got a warning ⚠️` -> `"I got a warning."`, omits decorative emojis `👋`/`😂` without reading unicode names, explains emojis if explicitly asked).
- **Natural Neural TTS**: Microsoft Edge-TTS streaming MP3 audio endpoint (`/api/speech/tts`) with volume, speed, pitch control, deduplication cache (30s TTL), and seamless Web Speech fallback.
- **Interruption & Sync**: Both desktop and mobile remote interrupt previous playback upon new user input or emergency stop.

### 4. Run the Automated Test Suite
```bat
backend\venv\Scripts\python.exe -m unittest tests/test_conversation_and_voice.py
```
```bat
backend\venv\Scripts\python.exe -m unittest tests/test_multi_agent_system.py
```
```bat
backend\venv\Scripts\python.exe -m unittest tests/test_os_agent.py
```
