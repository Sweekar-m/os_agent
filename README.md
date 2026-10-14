# ⚡ FCC-Codex Autonomous OS Agent

A desktop-first autonomous AI operating agent that completes development and productivity tasks independently while keeping the human in control through a robust permission and approval engine.

Integrates real **Codex / FCC-Codex** execution capabilities and the real **AGY (Antigravity)** agent for independent review and validation.

---

## 🌟 Architecture & Workflow

```
[ User Natural Language Prompt ]
               │
               ▼
   [ Intent & Planning AI ] ──(Inspects workspace context)
               │
               ▼
      [ Structured Task Plan ]
               │
               ▼
 ┌───[ Permission Engine ]───┐
 │   Checks Grants vs Policy │
 └───┬───────────────────┬───┘
     │ (Auto-Permitted)  │ (Sensitive / Destructive)
     │                   ▼
     │         [ Human Approval Modal ]
     │         1. Allow once
     │         2. Allow for this session
     │         3. Allow always for this project
     │         4. Allow full access
     │         5. Deny
     │         6. Cancel task
     │                   │ (Approved)
     ▼                   ▼
 [ Real Codex Execution Engine ] ──(Real tools, file edits & shell execution)
               │
               ▼
 [ Iterative Verifier (Tests/Builds) ]
               │
      (Pass) ┌─┴─┐ (Fail)
             │   │
             │   └──► [ Autonomous Failure Recovery Loop ] ──► (Codex fix & re-test)
             ▼
 [ AGY Independent Reviewer ] ──(Real agy.EXE evaluation & validation)
               │
               ▼
   [ Final Completion Report ] (Verified success evidence & modified file list)
```

---

## 🛡️ Permission & Approval Engine

The backend enforces a strict default-deny policy with canonical workspace isolation:

1. **Allow once**: Approves only the specific command or action; subsequent operations require fresh approval.
2. **Allow for this session**: Grants matching action category for the current task/session.
3. **Allow always for this project**: Creates a persistent, narrowly-scoped rule for the project path in SQLite (revocable anytime in Settings).
4. **Allow full access**: Requires explicit user confirmation; grants broad project permissions within the authorized workspace while keeping critical system operations protected.
5. **Deny**: Rejects the requested action; returns denial to the agent so it can pivot safely or explain the blocker.
6. **Cancel task**: Immediately stops the task and terminates active execution subprocesses.

---

## 📂 Project Structure

```
d:/project_a/
├── backend/
│   ├── main.py               # FastAPI 2.0 REST & SSE event stream endpoints
│   ├── orchestrator.py       # Central pipeline orchestrator & state machine
│   ├── permission_engine.py  # Backend permission, workspace isolation & grants
│   ├── planner.py            # Intent & Planning AI with workspace inspection
│   ├── codex_runtime.py      # Real Codex / FCC-Codex streaming execution
│   ├── agy_reviewer.py       # Real AGY CLI independent review integration
│   ├── verifier.py           # Automated test runner & autonomous failure recovery
│   ├── db.py                 # SQLite persistence (tasks, approvals, rules, audit logs)
│   ├── config.py             # Environment configuration
│   ├── input_controller.py   # Windows mouse & keyboard controller
│   ├── screen_capture.py     # Fast screen grabber & compression
│   ├── llm_client.py         # NVIDIA NIM LLM client
│   ├── ppt_agent.py          # PowerPoint agent tools
│   ├── doc_agent.py          # Universal document agent
│   └── venv/                 # Python virtual environment
├── desktop_app/
│   ├── index.html            # Desktop interface & permission modal
│   ├── style.css             # Modern dark-mode styling & design tokens
│   ├── app.js                # Frontend controller & SSE event stream handler
│   └── qrious.min.js         # Offline QR code generator
├── tests/
│   └── test_agent_system.py  # Automated test suite for Acceptance Tests A - N
├── start.py                  # One-command unified launcher
├── run.bat                   # Windows batch launcher
└── README.md
```

---

## 🚀 Quick Start

### 1. Launch the Application
Double-click:
```bat
run.bat
```
Or run from terminal:
```bash
python start.py
```

This starts the Python backend on `http://localhost:8000` and launches the desktop agent interface.

### 2. Run the Acceptance Test Suite
Execute all 14 mandatory acceptance test scenarios:
```powershell
backend\venv\Scripts\python.exe -m unittest tests/test_agent_system.py
```

---

## 🧪 Acceptance Test Coverage

All 14 mandatory acceptance scenarios are automated and verified:
- **Test A — Normal prompt**: Structured planning schema and instruction generation.
- **Test B — Real file operations**: Creating and modifying files within authorized boundary.
- **Test C — Permission interception**: Approval-required operation triggers waiting state.
- **Test D — Allow once**: Approves single action without granting future actions.
- **Test E — Session grant**: Subsequent matching actions proceed without prompting.
- **Test F — Persistent project grant**: Saves rule to database, restarts, and verifies revocation.
- **Test G — Denial**: Human denial is returned to execution runtime.
- **Test H — Full access**: Broad project grant with critical safeguard preservation.
- **Test I — Failure recovery**: Captures test failures and formulates targeted fixes.
- **Test J — Cancellation**: Stops running processes and updates task state.
- **Test K — Workspace isolation**: Blocks path traversal (`..` or outside access).
- **Test L — Concurrent tasks**: Isolates approvals, events, and states across tasks.
- **Test M — Real AGY review**: Evaluates task context using real `agy.EXE`.
- **Test N — Restart recovery**: Recovers stale in-flight tasks to BLOCKED on restart.
