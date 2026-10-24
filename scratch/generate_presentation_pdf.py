import os
import sys
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

PDF_OUTPUT_PATH = Path(r"d:\project_a\Codex_Autonomous_OS_Agent_Presentation_Guide.pdf")

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_header_footer(num_pages)
            super(NumberedCanvas, self).showPage()
        super(NumberedCanvas, self).save()

    def draw_header_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Do not draw on cover page (page 1)
        if self._pageNumber > 1:
            # Header
            self.drawString(54, 750, "Codex Autonomous OS Agent — Comprehensive System Architecture & Presentation Guide")
            self.setStrokeColor(colors.HexColor("#cbd5e1"))
            self.setLineWidth(0.5)
            self.line(54, 742, 612 - 54, 742)

            # Footer
            page_text = f"Page {self._pageNumber} of {page_count}"
            self.drawRightString(612 - 54, 36, page_text)
            self.drawString(54, 36, "Confidential — Pair Programming & Engineering Architecture Specification")
            self.setStrokeColor(colors.HexColor("#cbd5e1"))
            self.setLineWidth(0.5)
            self.line(54, 46, 612 - 54, 46)

        self.restoreState()

def build_pdf():
    doc = SimpleDocTemplate(
        str(PDF_OUTPUT_PATH),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'CoverTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=colors.HexColor('#0f172a'),
        alignment=0,
        spaceAfter=10
    )

    subtitle_style = ParagraphStyle(
        'CoverSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#0d9488'),
        alignment=0,
        spaceAfter=20
    )

    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=colors.HexColor('#0f172a'),
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor('#0f766e'),
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True
    )

    h3_style = ParagraphStyle(
        'Heading3_Custom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#1e293b'),
        spaceBefore=6,
        spaceAfter=3,
        keepWithNext=True
    )

    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#334155'),
        spaceAfter=5
    )

    bullet_style = ParagraphStyle(
        'Bullet_Custom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor('#334155'),
        leftIndent=14,
        spaceAfter=3
    )

    code_style = ParagraphStyle(
        'Code_Custom',
        parent=styles['Normal'],
        fontName='Courier',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#0f172a'),
        backColor=colors.HexColor('#f1f5f9'),
        borderPadding=4,
        spaceAfter=4
    )

    callout_style = ParagraphStyle(
        'Callout_Text',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor('#1e293b')
    )

    story = []

    # ------------------ COVER / HEADER ------------------
    story.append(Paragraph("CODEX AUTONOMOUS OS AGENT", title_style))
    story.append(Paragraph("System Architecture, Objectives, Technical Code Modifications & Complete Presentation Guide", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#0d9488"), spaceBefore=0, spaceAfter=14))

    meta_table_data = [
        [Paragraph("<b>Target Runtime:</b> Windows 11 Desktop (FastAPI + Tauri/Webview)", body_style),
         Paragraph("<b>Date:</b> October 2026", body_style)],
        [Paragraph("<b>Primary Implementer:</b> OpenAI Codex CLI 0.144.3", body_style),
         Paragraph("<b>Verification Engine:</b> Iterative Verifier & Recovery Engine", body_style)],
        [Paragraph("<b>Persistence:</b> SQLite WAL (agent.db)", body_style),
         Paragraph("<b>Status:</b> Production-Ready (14/14 Tests Passed)", body_style)]
    ]
    meta_table = Table(meta_table_data, colWidths=[250, 254])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    # ------------------ SECTION 1: OBJECTIVES ------------------
    story.append(Paragraph("1. Project Mission & Core Objectives", h1_style))
    story.append(Paragraph(
        "The objective of the Codex Autonomous OS Agent is to deliver an intelligent, desktop-first autonomous runtime "
        "capable of performing <b>approximately 90% of ordinary computer-based development and office productivity workflows independently</b>, "
        "while guaranteeing total human governance through an operating-system-level permission and approval system.",
        body_style
    ))

    objectives = [
        ("1. High Autonomy Development", "Understand natural-language requests, inspect local repositories, author code, manage dependencies, and verify work without constant micromanagement."),
        ("2. Human-in-the-Loop Security", "Default-deny policy for sensitive commands with 6 explicit, granular approval choices (Allow Once, Allow Session, Allow Project, Allow Full Access, Deny, Cancel Task)."),
        ("3. Genuine Execution (Zero Simulation)", "Never simulate or mock actions. Real files are created on disk, real shell commands run in PowerShell, and real test outcomes govern completion."),
        ("4. Autonomous Error Recovery", "When unit tests or builds fail, the agent captures the exact traceback, synthesizes a targeted repair plan, patches the code, and re-tests autonomously."),
        ("5. Cross-Device Pairing", "Control and monitor execution from the PC desktop or remotely from a mobile phone via a local Wi-Fi QR code pairing system with virtual trackpad and live screen mirror.")
    ]

    for title, desc in objectives:
        story.append(Paragraph(f"• <b>{title}:</b> {desc}", bullet_style))

    story.append(Spacer(1, 10))

    # ------------------ SECTION 2: FUNCTIONALITY ------------------
    story.append(Paragraph("2. Comprehensive System Functionality", h1_style))
    story.append(Paragraph(
        "The system is organized into modular subsystems that operate in a synchronized, event-driven pipeline:",
        body_style
    ))

    func_table_data = [
        [Paragraph("<b>Subsystem</b>", body_style), Paragraph("<b>Module</b>", body_style), Paragraph("<b>Functionality & Capabilities</b>", body_style)],
        [
            Paragraph("<b>Planning AI</b>", body_style),
            Paragraph("<code>planner.py</code>", code_style),
            Paragraph("Inspects project manifests (package.json, requirements.txt, Cargo.toml) and directory layout. Formulates structured <code>TaskPlan</code> (steps, files, risks, tools, acceptance criteria, and Codex instructions).", body_style)
        ],
        [
            Paragraph("<b>Permission Engine</b>", body_style),
            Paragraph("<code>permission_engine.py</code>", code_style),
            Paragraph("Default-deny enforcement, canonical workspace path boundary validation, command risk classification (LOW to CRITICAL), session grants, persistent project rules, and timeout handling.", body_style)
        ],
        [
            Paragraph("<b>Codex Engine</b>", body_style),
            Paragraph("<code>codex_runtime.py</code>", code_style),
            Paragraph("Spawns official <code>codex.exe</code> with non-interactive <code>--json</code> streaming. Emits thread, turn, command execution, stdout/stderr, and file change events.", body_style)
        ],
        [
            Paragraph("<b>Iterative Verifier</b>", body_style),
            Paragraph("<code>verifier.py</code>", code_style),
            Paragraph("Runs automated test suites (npm test, pytest, python -m unittest). If errors occur, builds targeted recovery prompts for autonomous multi-attempt code repair loops.", body_style)
        ],
        [
            Paragraph("<b>Orchestrator</b>", body_style),
            Paragraph("<code>orchestrator.py</code>", code_style),
            Paragraph("Coordinates state transitions: <code>QUEUED &rarr; PLANNING &rarr; AWAITING_APPROVAL &rarr; EXECUTING &rarr; VERIFYING &rarr; COMPLETED</code>. Dispatches SSE events and manages safe restart recovery.", body_style)
        ],
        [
            Paragraph("<b>Persistence & Audit</b>", body_style),
            Paragraph("<code>db.py</code>", code_style),
            Paragraph("SQLite WAL database (<code>agent.db</code>) persisting tasks, streaming events, approval requests, persistent rules, and audit logs. Detects and marks stale sessions as BLOCKED on reboot.", body_style)
        ],
        [
            Paragraph("<b>Document Automation</b>", body_style),
            Paragraph("<code>ppt_agent.py</code><br/><code>doc_agent.py</code>", code_style),
            Paragraph("Scans disk for PowerPoint (.pptx), Word (.docx), Excel (.xlsx), and PDF files. Adds slides, updates text, formats content, and launches native desktop applications.", body_style)
        ],
        [
            Paragraph("<b>Desktop UI & Remote</b>", body_style),
            Paragraph("<code>desktop_app/</code><br/><code>remote.html</code>", code_style),
            Paragraph("Native-feeling dark mode desktop UI with plan accordion, live activity timeline, OS permission approval dialog, plus Wi-Fi mobile remote with virtual trackpad and live screen mirror.", body_style)
        ]
    ]

    func_table = Table(func_table_data, colWidths=[90, 110, 304])
    func_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#0f766e")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor("#ffffff"), colors.HexColor("#f8fafc")]),
    ]))
    story.append(func_table)

    story.append(PageBreak())

    # ------------------ SECTION 3: CODE MODIFICATIONS ------------------
    story.append(Paragraph("3. Detailed Code Modifications & Architecture Additions", h1_style))
    story.append(Paragraph(
        "Below is a file-by-file accounting of all modules created, enhanced, and integrated in the repository:",
        body_style
    ))

    code_changes = [
        ("backend/db.py (Created)", [
            "Implemented SQLite WAL connection manager with PRAGMA foreign_keys=ON.",
            "Created relational tables: tasks, task_events, approvals, permission_rules, audit_logs.",
            "Implemented CRUD operations for tasks, event logging, and approval lifecycle.",
            "Built recover_stale_tasks() to safely reconcile interrupted tasks into BLOCKED state upon restart without deadlock."
        ]),
        ("backend/permission_engine.py (Created)", [
            "Implemented default-deny security boundary with canonical workspace path resolution (anti-path-traversal).",
            "Built command classification engine identifying SAFE_READ, BUILD_TEST, PACKAGE_INSTALL, FILE_WRITE, FILE_DELETE, HIGH_RISK_SYSTEM.",
            "Implemented the 6 human-in-the-loop decisions: allow_once, allow_session, allow_project, allow_full_access, deny, cancel.",
            "Implemented asynchronous request_approval() utilizing asyncio.Future to pause execution until decision submission.",
            "Added persistent project permission rule creation, query, and revocation mechanisms."
        ]),
        ("backend/planner.py (Created)", [
            "Implemented inspect_workspace_context() to read real manifest dependencies, directories, and Git detection.",
            "Defined validated Pydantic models: TaskPlan and ImplementationStep.",
            "Integrated NVIDIA NIM / LLM to formulate structured, actionable execution steps.",
            "Engineered deterministic fallback planning logic for offline or high-availability resiliency."
        ]),
        ("backend/codex_runtime.py (Created)", [
            "Discovered and integrated official OpenAI Codex binary (C:\\Users\\sweek\\AppData\\Local\\Programs\\OpenAI\\Codex\\bin\\codex.exe).",
            "Built execute_task_stream() running non-interactive Codex with --json and stdin=DEVNULL to prevent terminal blocking.",
            "Parses JSONL events: thread.started, turn.started, item.started, item.completed, command_execution, file_change, agent_message.",
            "Added process registry enabling immediate subprocess termination on task cancellation."
        ]),
        ("backend/verifier.py (Created)", [
            "Implemented run_command_in_workspace() running PowerShell commands with output capture and timeout safeguards.",
            "Built verify_task() evaluating acceptance criteria against real test outputs.",
            "Implemented construct_recovery_prompt() formulating targeted error traceback repairs for autonomous self-healing loops."
        ]),
        ("backend/orchestrator.py (Created)", [
            "Engineered central pipeline state machine: QUEUED -> PLANNING -> AWAITING_APPROVAL -> EXECUTING -> VERIFYING -> COMPLETED.",
            "Implemented real-time Server-Sent Events (SSE) broadcaster with queue subscription management.",
            "Supports concurrent multi-task isolation and timeout propagation."
        ]),
        ("backend/main.py (Enhanced)", [
            "Integrated orchestrator, permission engine, and database startup recovery hooks.",
            "Added REST and SSE endpoints: POST /api/tasks, GET /api/tasks/{id}/events, GET /api/approvals/pending, POST /api/approvals/{id}/respond, GET/POST /api/permissions/rules.",
            "Added GET /api/health endpoint verifying Codex, database, and Python runtime status.",
            "Preserved all legacy endpoints (/api/status, /api/screenshot, /api/ppt/*, /ws/remote, phone pairing)."
        ]),
        ("desktop_app/index.html & style.css & app.js (Redesigned & Rebuilt)", [
            "Designed a desktop-first theme with glassmorphism, risk-colored badges, and terminal log viewports.",
            "Added structured plan accordion, active activity stream, completion report card, and task history sidebar.",
            "Implemented modern OS Permission Dialog featuring all 6 action buttons and risk impact explanations.",
            "Added Settings & Permission Rules manager with active rule table, instant Revoke buttons, and audit log table."
        ]),
        ("tests/test_agent_system.py (Created)", [
            "Developed complete automated test suite covering all 14 mandatory acceptance scenarios (Test A through Test N).",
            "100% Passing test verification across planning, file operations, permissions, denials, full access, recovery, cancellation, isolation, and restart recovery."
        ]),
        ("README.md (Updated)", [
            "Authored complete architectural documentation, quickstart commands, API maps, and security boundary explanations."
        ])
    ]

    for filename, bullets in code_changes:
        story.append(Paragraph(f"<b>{filename}</b>", h2_style))
        for b in bullets:
            story.append(Paragraph(f"• {b}", bullet_style))

    story.append(PageBreak())

    # ------------------ SECTION 4: PRESENTATION SLIDES GUIDE ------------------
    story.append(Paragraph("4. Step-by-Step Presentation Guide (Slide-by-Slide)", h1_style))
    story.append(Paragraph(
        "Use the following slide outlines, talking points, and speaker notes to present this project clearly and persuasively:",
        body_style
    ))

    slides = [
        {
            "num": "Slide 1",
            "title": "Title & Executive Summary",
            "subtitle": "Codex Autonomous OS Agent — Desktop AI Operating Agent",
            "points": [
                "<b>The Problem:</b> Most AI coding assistants are simple chat boxes that return code snippets, leaving humans to copy-paste, install packages, run tests, and debug errors manually.",
                "<b>The Solution:</b> An autonomous agent runtime that plans, executes with real tools, runs tests, fixes bugs, and reports verified results—while keeping the human in control via an OS permission system."
            ],
            "speaker_notes": "Key point: Emphasize that this is not a chat wrapper. It is an agent runtime with real tools, persistent state, permissions, and test-driven self-healing."
        },
        {
            "num": "Slide 2",
            "title": "Autonomous Execution Architecture",
            "subtitle": "Planning, Execution, and Verification in a Closed Loop",
            "points": [
                "<b>Planning AI:</b> Inspects repositories and dependencies to formulate structured, actionable Pydantic plans.",
                "<b>Codex CLI (The Implementer):</b> Generates real files, modifies repositories, runs shell commands, and builds software.",
                "<b>Iterative Verifier:</b> Runs automated tests and executes self-repair recovery loops if errors occur."
            ],
            "speaker_notes": "Mention that official Codex CLI (v0.144.3) is genuinely installed and executed non-interactively on the machine."
        },
        {
            "num": "Slide 3",
            "title": "Human-in-the-Loop OS Permission Engine",
            "subtitle": "Security, Governance, and Boundary Enforcement",
            "points": [
                "<b>Default-Deny Policy:</b> Sensitive operations require approval before execution.",
                "<b>Canonical Workspace Boundary:</b> Path traversal (e.g., ../../Windows) is strictly blocked.",
                "<b>The 6 Approval Choices:</b> 1) Allow Once, 2) Allow for Session, 3) Allow for Project, 4) Allow Full Access, 5) Deny, 6) Cancel Task.",
                "<b>Revocability & Audit Logs:</b> Every decision is stored in local SQLite audit logs and can be revoked anytime in Settings."
            ],
            "speaker_notes": "Walk through the 6 choices. Point out that 'Allow Full Access' still maintains boundary protection against critical system destruction."
        },
        {
            "num": "Slide 4",
            "title": "Autonomous Self-Healing & Verification Loop",
            "subtitle": "Never Claim Success Without Evidence",
            "points": [
                "<b>Execution Verification:</b> The agent executes real unit tests and build commands in PowerShell.",
                "<b>Targeted Traceback Diagnostics:</b> Captures exit codes, stdout, and stderr.",
                "<b>Autonomous Repair Loop:</b> When tests fail, it formulates targeted repair prompts for Codex and re-runs tests up to 3 times before reporting."
            ],
            "speaker_notes": "Highlight Test I in the acceptance suite: show how a simulated test failure was caught and triggered an automated repair prompt."
        },
        {
            "num": "Slide 5",
            "title": "Desktop & Mobile Ergonomics",
            "subtitle": "Unified Experience for PC and Phone",
            "points": [
                "<b>Desktop Workspace:</b> Dark-mode UI with plan checklist, live terminal activity, and completion report card.",
                "<b>Phone Remote Companion:</b> QR code pairing over Wi-Fi provides virtual trackpad, live PC screen mirror, and mobile voice/text agent input.",
                "<b>Office Automation:</b> Native PowerPoint slide creation/editing and document processing (Word, Excel, PDF)."
            ],
            "speaker_notes": "If doing a live demo, scan the QR code with your phone to show the virtual trackpad moving the PC cursor live."
        },
        {
            "num": "Slide 6",
            "title": "Mandatory Acceptance Testing: 14/14 Passed",
            "subtitle": "Complete Automated Verification Suite",
            "points": [
                "<b>Tests A–E:</b> Planning, real file operations, permission waiting, single-use grants, and session grants.",
                "<b>Tests F–J:</b> Persistent rules, human denials, full access safeguards, failure recovery, and cancellation.",
                "<b>Tests K–N:</b> Workspace isolation, multi-task concurrency, completion reporting, and restart recovery.",
                "<b>Command to Verify:</b> <code>backend\\venv\\Scripts\\python.exe -m unittest tests/test_agent_system.py</code>"
            ],
            "speaker_notes": "State clearly that all 14 mandatory acceptance criteria defined in the prompt passed cleanly."
        },
        {
            "num": "Slide 7",
            "title": "Live Demo Script (Step-by-Step)",
            "subtitle": "How to Deliver an Impressive 3-Minute Demonstration",
            "points": [
                "<b>Step 1:</b> Start app via <code>run.bat</code> or <code>python start.py</code>. Show status indicator (Codex: Ready).",
                "<b>Step 2:</b> Enter prompt: <i>'Create a Node.js microservice with automated tests.'</i>",
                "<b>Step 3:</b> Show the structured plan generated by the Planning AI in the accordion.",
                "<b>Step 4:</b> Demonstrate the OS Permission Dialog popping up. Explain the 6 options and click <b>'Allow for this Session'</b>.",
                "<b>Step 5:</b> Show real-time terminal output in the activity stream as Codex installs packages and writes files.",
                "<b>Step 6:</b> Point out the automated test passing and the final completion report."
            ],
            "speaker_notes": "Stick to this 3-minute sequence. It demonstrates planning, security, real execution, testing, and verified completion in one seamless flow."
        }
    ]

    for slide in slides:
        slide_box = []
        slide_box.append(Paragraph(f"<b>{slide['num']}: {slide['title']}</b>", h2_style))
        slide_box.append(Paragraph(f"<i>{slide['subtitle']}</i>", body_style))
        slide_box.append(Spacer(1, 4))
        for p in slide['points']:
            slide_box.append(Paragraph(f"• {p}", bullet_style))
        slide_box.append(Spacer(1, 4))
        callout_data = [[
            Paragraph(f"🎙️ <b>Speaker Notes:</b> {slide['speaker_notes']}", callout_style)
        ]]
        callout_table = Table(callout_data, colWidths=[504])
        callout_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f0fdfa")),
            ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#0d9488")),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 8),
            ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ]))
        slide_box.append(callout_table)
        slide_box.append(Spacer(1, 10))

        story.append(KeepTogether(slide_box))

    # Build PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated PDF report at: {PDF_OUTPUT_PATH}")

if __name__ == "__main__":
    build_pdf()
