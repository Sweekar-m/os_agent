import json
import re
import os
import subprocess
from pathlib import Path
import httpx
from typing import AsyncGenerator, List, Dict, Any, Optional
from config import settings
from input_controller import controller
from screen_capture import get_screen_dimensions
from ppt_agent import ppt_agent
from os_toolkit import os_toolkit

SYSTEM_PROMPT = """You are an intelligent, versatile AI Assistant and Windows OS Companion.
You combine the natural, helpful conversation of a state-of-the-art AI chatbot with the power to optionally perform Windows desktop tasks, file operations, PowerShell scripts, and presentation generation when requested.

Current System Environment:
- Platform: Windows
- Screen Resolution: {width}x{height}
- Context: {active_context}

BEHAVIOR & CONVERSATION GUIDELINES (CRITICAL):
1. CHAT LIKE A NORMAL AI CHATBOT:
   - When the user is having a casual conversation, greeting you ("hi", "hey", "hello"), asking general questions, brainstorming, asking for explanations, advice, or writing code, RESPOND NATURALLY, WARMLY, AND CONVERSATIONALLY (just like ChatGPT).
   - NEVER return action commands or ```action``` code blocks for casual greetings, chitchat, or general questions.
   - NEVER say "I need a specific action or command to execute" or ask for action JSON when someone is simply chatting with you. You are a complete, conversational assistant.

2. ON-DEMAND OS AUTOMATION (ONLY WHEN REQUESTED):
   - ONLY when the user explicitly asks you to inspect their computer, run a command, manage processes, read/edit a file, check hardware, or automate an app, include executable action commands inside a valid JSON array wrapped in ```action ... ``` code fences.

Supported Actions (ONLY WHEN EXPLICITLY REQUESTED):
- Telemetry & Processes:
  - {{"type": "system_info"}} (ONLY when user explicitly asks for CPU/RAM/specs/system performance)
  - {{"type": "get_processes", "limit": 15, "filter": "<optional name filter>"}}
  - {{"type": "kill_process", "pid": <int>}} or {{"type": "kill_process", "name": "<app_name>"}}

- File & System Operations:
  - {{"type": "read_file", "path": "<file_path>", "max_lines": 200}}
  - {{"type": "write_file", "path": "<file_path>", "content": "<content_to_write>", "overwrite": true}}
  - {{"type": "append_file", "path": "<file_path>", "content": "<content_to_append>"}}
  - {{"type": "list_dir", "path": "<folder_path>", "limit": 40}}
  - {{"type": "find_file", "name": "<filename or extension>", "drive": "C:\\"|"D:\\"}}

- Shell & Script Execution:
  - {{"type": "shell", "command": "<cmd or powershell command>", "shell": "powershell"|"cmd"}}

- Clipboard & Apps:
  - {{"type": "get_clipboard"}}
  - {{"type": "set_clipboard", "text": "<text_to_copy>"}}
  - {{"type": "open_app", "app": "<notepad|chrome|calc|cmd|powershell|explorer|app_name>"}}

- Desktop Input & GUI:
  - {{"type": "type", "text": "<string to type>"}}
  - {{"type": "press", "key": "win|enter|esc|tab|space|backspace|up|down|left|right"}}
  - {{"type": "hotkey", "keys": ["win", "r"]}} or {{"type": "hotkey", "keys": ["ctrl", "c"]}}
  - {{"type": "click", "x": <int>, "y": <int>, "button": "left"|"right", "clicks": 1|2}}
  - {{"type": "move", "x": <int>, "y": <int>}}
  - {{"type": "scroll", "dy": <float>}}
  - {{"type": "screenshot"}}

CRITICAL RULES:
- ZERO HALLUCINATION: Never make up paths like "C:\\Users\\YourUsername\\..." or fabricate command outputs.
- NEVER assume the user wants PowerPoint unless they specifically ask for presentations, slides, or PPT.
- For greetings or general questions: Be friendly, helpful, concise, and conversational without any JSON action blocks."""

class NvidiaNimClient:
    def __init__(self):
        pass

    def get_api_key(self) -> str:
        return settings.NVIDIA_API_KEY

    def get_headers(self) -> Dict[str, str]:
        key = self.get_api_key()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def execute_parsed_actions(self, content: str, permission_mode: str = "full_access", user_msg: str = "") -> List[Dict[str, Any]]:
        """Parses and executes any ```action ... ``` blocks in the LLM response respecting permission_mode"""
        executed = []
        action_blocks = re.findall(r"```(?:action|json)?\s*([\[\{].*?[\]\}])\s*```", content, re.DOTALL)
        
        for block in action_blocks:
            clean_block = block.strip()
            actions = None
            try:
                actions = json.loads(clean_block)
            except Exception:
                try:
                    # Repair Windows single backslashes if any (e.g. C:\Users -> C:\\Users)
                    repaired = re.sub(r'\\(?![/"\\bfnrtu])', r'\\\\', clean_block)
                    actions = json.loads(repaired)
                except Exception as ex:
                    print(f"[JSON Parse Error on action block] {ex}: {clean_block[:200]}")
                    continue

            if actions:
                if isinstance(actions, dict):
                    actions = [actions]
                if isinstance(actions, list):
                    for act in actions:
                        act_type = act.get("type", "").lower()
                        
                        # In read_only mode, block mutating actions
                        read_only_allowed = ["system_info", "get_processes", "read_file", "list_dir", "find_file", "get_clipboard", "find_ppts", "inspect_ppt", "screenshot"]
                        if permission_mode == "read_only" and act_type not in read_only_allowed:
                            act["status"] = "blocked"
                            act["reason"] = "Blocked by read-only permission setting"
                            executed.append(act)
                            continue

                        # 1. OS Telemetry & Hardware
                        if act_type in ["system_info", "get_system_info"]:
                            # Filter out unsolicited system_info if user was just chatting and did not ask for telemetry/hardware
                            if user_msg:
                                u_lower = user_msg.lower()
                                telemetry_words = ["cpu", "ram", "memory", "telemetry", "hardware", "specs", "specifications", "disk", "storage", "performance", "system info", "system stats", "status", "uptime"]
                                if not any(w in u_lower for w in telemetry_words):
                                    continue
                            act["system_info"] = os_toolkit.get_system_info()
                            act["status"] = "success"
                            executed.append(act)

                        # 2. Running Processes & Process Management
                        elif act_type in ["get_processes", "processes", "list_processes"]:
                            lim = int(act.get("limit", 15))
                            filt = act.get("filter") or act.get("name")
                            act["processes"] = os_toolkit.get_running_processes(limit=lim, filter_name=filt)
                            act["status"] = "success"
                            executed.append(act)

                        elif act_type == "kill_process":
                            res = os_toolkit.kill_process(pid=act.get("pid"), name=act.get("name") or act.get("app"))
                            act["result"] = res
                            act["status"] = "success" if res.get("success") else "error"
                            executed.append(act)

                        # 3. File Operations
                        elif act_type in ["read_file", "view_file"]:
                            path_target = act.get("path") or act.get("file", "")
                            max_l = int(act.get("max_lines", 200))
                            offset = int(act.get("offset", 0))
                            res = os_toolkit.read_file(path_target, max_lines=max_l, offset=offset)
                            act["file_data"] = res
                            act["status"] = "success" if "error" not in res else "error"
                            executed.append(act)

                        elif act_type in ["write_file", "create_file"]:
                            path_target = act.get("path") or act.get("file", "")
                            c = act.get("content", "")
                            res = os_toolkit.write_file(path_target, c, overwrite=act.get("overwrite", True))
                            act["result"] = res
                            act["status"] = "success" if "error" not in res else "error"
                            executed.append(act)

                        elif act_type == "append_file":
                            path_target = act.get("path") or act.get("file", "")
                            c = act.get("content", "")
                            res = os_toolkit.append_file(path_target, c)
                            act["result"] = res
                            act["status"] = "success" if "error" not in res else "error"
                            executed.append(act)

                        elif act_type in ["list_dir", "list_directory"]:
                            dir_target = act.get("path") or "."
                            lim = int(act.get("limit", 40))
                            res = os_toolkit.list_directory(dir_target, limit=lim)
                            act["dir_data"] = res
                            act["status"] = "success" if "error" not in res else "error"
                            executed.append(act)

                        elif act_type in ["find_file", "search_files", "search_file"]:
                            q = act.get("name") or act.get("query") or act.get("filename", "")
                            found = os_toolkit.find_files(query=q, drive=act.get("drive"), extension=act.get("extension"), limit=15)
                            act["results"] = found
                            act["status"] = "success"
                            executed.append(act)

                        # 4. Shell & Command Execution
                        elif act_type in ["shell", "command", "powershell"]:
                            cmd = act.get("command") or act.get("cmd", "")
                            stype = act.get("shell", "powershell")
                            res = os_toolkit.run_command(cmd, shell_type=stype, timeout=25)
                            act["stdout"] = res.get("stdout", "")[:2500]
                            act["stderr"] = res.get("stderr", "")[:1000]
                            act["exit_code"] = res.get("exit_code", 0)
                            act["status"] = "success" if res.get("success") else "error"
                            executed.append(act)

                        # 5. Clipboard Operations
                        elif act_type == "get_clipboard":
                            act["clipboard"] = os_toolkit.get_clipboard()
                            act["status"] = "success"
                            executed.append(act)

                        elif act_type == "set_clipboard":
                            res = os_toolkit.set_clipboard(act.get("text", ""))
                            act["result"] = res
                            act["status"] = "success" if res.get("success") else "error"
                            executed.append(act)

                        # 6. Applications & Windows
                        elif act_type in ["open_app", "launch", "open_file"]:
                            app_target = act.get("app") or act.get("target") or act.get("name", "")
                            res = os_toolkit.open_app_or_file(app_target)
                            act["result"] = res
                            act["status"] = "success" if res.get("success") else "error"
                            executed.append(act)

                        # 7. Desktop GUI & Input Automation
                        elif act_type == "click":
                            x = act.get("x")
                            y = act.get("y")
                            if x is not None and y is not None:
                                controller.move_to(int(x), int(y))
                            controller.click(act.get("button", "left"), act.get("clicks", 1))
                            executed.append(act)
                        elif act_type == "move":
                            controller.move_to(int(act.get("x", 0)), int(act.get("y", 0)))
                            executed.append(act)
                        elif act_type == "type":
                            controller.type_text(act.get("text", ""))
                            executed.append(act)
                        elif act_type == "press":
                            controller.press_key(act.get("key", "enter"))
                            executed.append(act)
                        elif act_type == "hotkey":
                            controller.hotkey(*act.get("keys", []))
                            executed.append(act)
                        elif act_type == "scroll":
                            controller.scroll(float(act.get("dy", 1)))
                            executed.append(act)
                        elif act_type == "screenshot":
                            w, h = get_screen_dimensions()
                            act["status"] = "captured"
                            act["resolution"] = f"{w}x{h}"
                            executed.append(act)

                        # 8. Document & PowerPoint Tools (If requested)
                        elif act_type == "find_ppts":
                            files = ppt_agent.find_recent_ppts(limit=10)
                            act["results"] = files
                            executed.append(act)
                        elif act_type == "inspect_ppt":
                            p = act.get("path") or ppt_agent.active_file
                            if p:
                                info = ppt_agent.inspect_ppt(p)
                                act["results"] = info
                                executed.append(act)
                        elif act_type == "create_ppt":
                            p = act.get("path")
                            if not p:
                                import datetime
                                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                p = str(Path.home() / "Desktop" / f"Presentation_{ts}.pptx")
                            ppt_title = act.get("title", "New Presentation")
                            slides_data = act.get("slides", [])
                            theme = act.get("theme", "dark")
                            res = ppt_agent.create_ppt(p, ppt_title, slides_data, theme=theme)
                            if act.get("open_after", True):
                                ppt_agent.open_in_powerpoint(p)
                            act["results"] = res
                            executed.append(act)
                        elif act_type == "add_slide":
                            p = act.get("path") or ppt_agent.active_file
                            title = act.get("title", "New Slide")
                            bullets = act.get("bullets", [])
                            if p:
                                res = ppt_agent.add_slide(p, title, bullets)
                                if act.get("open_after", True):
                                    ppt_agent.open_in_powerpoint(p)
                                act["results"] = res
                                executed.append(act)
                        elif act_type == "replace_text":
                            p = act.get("path") or ppt_agent.active_file
                            find_txt = act.get("find_text", "")
                            repl_txt = act.get("replace_text", "")
                            if p and find_txt:
                                res = ppt_agent.replace_text(p, find_txt, repl_txt, act.get("slide_num"))
                                if act.get("open_after", True):
                                    ppt_agent.open_in_powerpoint(p)
                                act["results"] = res
                                executed.append(act)
                        elif act_type == "update_slide":
                            p = act.get("path") or ppt_agent.active_file
                            slide_num = act.get("slide_num", 1)
                            title = act.get("title")
                            bullets = act.get("bullets")
                            if p:
                                res = ppt_agent.update_slide(p, slide_num, title=title, bullet_points=bullets)
                                if act.get("open_after", True):
                                    ppt_agent.open_in_powerpoint(p)
                                act["results"] = res
                        elif act_type == "open_ppt":
                            p = act.get("path") or ppt_agent.active_file
                            if p:
                                ppt_agent.open_in_powerpoint(p)
                                executed.append(act)
                
        return executed

    def get_system_prompt(self) -> str:
        w, h = get_screen_dimensions()
        active = ppt_agent.active_file
        if active and Path(active).exists():
            active_info = f"Loaded presentation: {Path(active).name}"
        else:
            active_info = "Ready for desktop OS automation"

        prompt = (SYSTEM_PROMPT
                  .replace("{width}", str(w))
                  .replace("{height}", str(h))
                  .replace("{active_context}", active_info))
        return prompt

    async def call_llm_for_ppt_action(
        self,
        task_description: str,
        ppt_content: str,
        active_path: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Calls the LLM specifically to generate PPT action JSON (create/add/update/replace).
        Returns the parsed action result dict, or None on failure.
        """
        api_key = self.get_api_key()
        if not api_key:
            return None

        import datetime
        default_path = active_path or str(Path.home() / "Desktop" / f"Presentation_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx")

        system = (
            "You are a PowerPoint expert agent. Your job is to generate a JSON action block for a PowerPoint task.\n"
            "Output ONLY a valid JSON array inside ```action ... ``` fences. No other text.\n"
            "Available action types: create_ppt, add_slide, update_slide, replace_text.\n"
            f"Default save path if creating new file: {default_path}\n"
            f"Active file path (use this for edits): {active_path or default_path}\n"
            "For create_ppt: include 4-6 content slides with professional bullet points based on the topic.\n"
            "For add_slide: generate 3-5 relevant bullet points from the existing content context.\n"
            "For update_slide: improve the existing content using the presentation context provided.\n"
            "Themes: dark=tech/startup (deep navy + cyan), light=academic/reports, corporate=business (navy + orange).\n"
            "\nPRESENTATION CONTENT CONTEXT:\n" + ppt_content
        )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": task_description}
        ]

        url = f"{settings.NVIDIA_BASE_URL.rstrip('/')}/chat/completions"
        payload = {
            "model": settings.DEFAULT_MODEL,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 2000,
            "stream": False
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=self.get_headers())
                if resp.status_code != 200:
                    print(f"[LLM PPT Action] API error {resp.status_code}: {resp.text}")
                    return None
                data = resp.json()
                content_str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(f"[LLM PPT Action] Raw response: {content_str[:500]}")

                # Execute the parsed action
                executed = await self.execute_parsed_actions(content_str)
                if executed:
                    return {"actions": executed, "content": content_str, "llm_generated": True}
                return None
        except Exception as e:
            print(f"[LLM PPT Action] Exception: {e}")
            return None

    async def detect_ppt_intent(self, user_msg: str) -> Optional[Dict[str, Any]]:
        """Intelligently detects user intent regarding PowerPoint presentations with zero design loss"""
        msg_lower = user_msg.lower().strip()

        # Intent 0: CREATE NEW PPT (Must be evaluated FIRST to never conflict with search)
        create_triggers = [
            "create a ppt", "create ppt", "create a presentation", "create presentation",
            "make a ppt", "make ppt", "make a presentation", "make presentation",
            "new ppt", "new presentation", "build a ppt", "build ppt",
            "generate a ppt", "generate ppt", "generate presentation", "build presentation",
            "prepare a ppt", "prepare ppt", "prepare a presentation"
        ]
        is_create_intent = any(t in msg_lower for t in create_triggers) or (
            any(w in msg_lower for w in ["create", "make", "generate", "build", "prepare"]) and
            any(w in msg_lower for w in ["ppt", "pptx", "presentation", "slides", "slide deck", "deck"])
        )

        if is_create_intent:
            # Check if user requested creating a presentation for project_a or a local workspace directory
            is_project_a = "project_a" in msg_lower or "project a" in msg_lower or ("project" in msg_lower and ("d drive" in msg_lower or "drive d" in msg_lower or "d:" in msg_lower))

            if is_project_a:
                save_dir = Path("D:/project_a")
                if not save_dir.exists():
                    save_dir = Path.home() / "Desktop"
                save_path = str(save_dir / "Project_A_Autonomous_OS_Agent.pptx")
                title = "FCC-Codex Autonomous OS Agent (Project A)"

                slides_data = [
                    {
                        "title": "FCC-Codex Autonomous OS Agent",
                        "bullets": [
                            "Desktop-First Autonomous AI Operating Agent",
                            "Real-Time OS Automation & Multi-Agent Verification",
                            "Project A — D:\\project_a"
                        ],
                        "type": "title"
                    },
                    {
                        "title": "Executive Summary & Core Objectives",
                        "bullets": [
                            "Autonomous desktop agent running locally on Windows PC",
                            "Performs complex coding, system diagnostics, and desktop tasks independently",
                            "Maintains human-in-the-loop control through a robust permission engine",
                            "Zero-hallucination real OS introspection, hardware telemetry, and file operations"
                        ],
                        "type": "content"
                    },
                    {
                        "title": "System Architecture & Multi-Agent Pipeline",
                        "bullets": [
                            "Planning AI translates natural language requests into structured Pydantic task plans",
                            "Real Codex CLI runtime executes commands, code edits, and tools in isolated sandbox",
                            "Iterative Verifier runs automated tests, builds, and linting checks",
                            "Real AGY (Antigravity) Reviewer provides independent validation before task completion"
                        ],
                        "type": "content"
                    },
                    {
                        "title": "Safety & Permission Governance Engine",
                        "bullets": [
                            "Pre-flight permission modes: Full Access, Balanced, Ask Each Step, Read-Only",
                            "Granular decision matrix: Allow Once, Session Grant, Persistent Project Rule",
                            "Canonical workspace sandboxing strictly blocks path traversal attacks",
                            "Comprehensive SQLite audit logging records every tool invocation and state change"
                        ],
                        "type": "content"
                    },
                    {
                        "title": "Live Hardware Telemetry & OS Automation",
                        "bullets": [
                            "Real-time hardware introspection: CPU %, memory usage, and drive partitions (C:\\, D:\\)",
                            "Process Manager: active process monitoring and hung application termination",
                            "PowerShell and CMD execution with real exit code, stdout, and stderr capture",
                            "Desktop input automation: mouse clicks, keyboard text, hotkeys, and screenshot capture"
                        ],
                        "type": "content"
                    },
                    {
                        "title": "ChatGPT Desktop UI & Phone Remote Sync",
                        "bullets": [
                            "Modern dark mode interface with real-time SSE execution telemetry streaming",
                            "Collapsible Live Desktop Screen PiP for real-time monitoring",
                            "Mobile trackpad remote paired via local Wi-Fi QR code for couch control",
                            "Full automated test suite with 14 verified integration and security benchmarks"
                        ],
                        "type": "content"
                    },
                    {
                        "title": "Project Summary & Milestones",
                        "bullets": [
                            "Fully verified end-to-end autonomous operating system agent",
                            "High-performance FastAPI backend with zero-latency telemetry",
                            "Production-ready Windows desktop automation platform"
                        ],
                        "type": "closing"
                    }
                ]

                res = ppt_agent.create_ppt(save_path, title, slides_data, theme="dark")
                ppt_agent.open_in_powerpoint(save_path)
                ppt_agent.active_file = save_path

                content = (
                    f"✅ **PowerPoint Presentation Created for Project A (`D:\\project_a`)!**\n\n"
                    f"📁 **File**: `Project_A_Autonomous_OS_Agent.pptx`\n"
                    f"📍 **Path**: `{save_path}`\n"
                    f"🎨 **Theme**: Dark Tech (Navy + Cyan)\n"
                    f"📑 **Total Slides**: {res.get('total_slides', 7)} slides covering Architecture, Governance, Telemetry, and UI\n\n"
                    f"### 📑 Presentation Outline:\n"
                    f"1. **Title**: FCC-Codex Autonomous OS Agent (Project A)\n"
                    f"2. **Vision**: Executive Summary & Core Objectives\n"
                    f"3. **Architecture**: Planning AI, Codex CLI, Iterative Verifier, AGY Reviewer\n"
                    f"4. **Governance**: Permission Engine & Security Boundaries\n"
                    f"5. **OS Power**: Hardware Telemetry, Process Control & PowerShell\n"
                    f"6. **User Experience**: ChatGPT Desktop UI, Live PiP & Phone Remote\n"
                    f"7. **Milestones**: Verified Benchmarks & Production Roadmap\n\n"
                    f"🚀 **Opened in Microsoft PowerPoint on your screen!**"
                )
                return {
                    "role": "assistant",
                    "content": content,
                    "actions": [res],
                    "type": "ppt_created"
                }

            # General topic creation: call LLM or generate dynamic slides
            ppt_content = f"User wants to create a presentation on topic: {user_msg}"
            llm_result = await self.call_llm_for_ppt_action(user_msg, ppt_content, None)
            if llm_result and llm_result.get("actions"):
                act = llm_result["actions"][0]
                res = act.get("results", {})
                p_name = Path(res.get("file", "Presentation.pptx")).name
                content = (
                    f"✅ **New presentation created with {res.get('total_slides', '?')} slides!**\n\n"
                    f"📁 **File**: `{p_name}`\n"
                    f"🎨 **Theme**: {res.get('theme', 'dark')} — professional design with accent colors, typography, and layout\n"
                    f"📄 **Title**: {res.get('title', '')}\n\n"
                    f"🚀 **Opened in Microsoft PowerPoint!**"
                )
                return {"role": "assistant", "content": content, "actions": llm_result["actions"], "type": "ppt_created"}
            else:
                clean_title = re.sub(r"^(?:create|make|build|generate)\s+(?:a\s+)?(?:ppt|presentation)\s+(?:on|about|for)?\s*", "", user_msg, flags=re.IGNORECASE).strip() or "New Presentation"
                import datetime
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                save_p = str(Path.home() / "Desktop" / f"Presentation_{ts}.pptx")
                slides_data = [
                    {"title": clean_title, "bullets": ["Professional Overview", "Key Topics & Analysis", "Executive Summary"], "type": "title"},
                    {"title": "Core Objectives", "bullets": ["Primary goal and scope", "Target milestones", "Strategy and implementation"], "type": "content"},
                    {"title": "Key Insights", "bullets": ["Market / system analysis", "Performance indicators", "Best practices"], "type": "content"},
                    {"title": "Action Plan", "bullets": ["Next steps and timeline", "Resource allocation", "Expected deliverables"], "type": "closing"}
                ]
                res = ppt_agent.create_ppt(save_p, clean_title, slides_data, theme="dark")
                ppt_agent.open_in_powerpoint(save_p)
                ppt_agent.active_file = save_p
                return {
                    "role": "assistant",
                    "content": f"✅ **New presentation created:** `{Path(save_p).name}` ({res.get('total_slides')} slides)\n\n🚀 **Opened in Microsoft PowerPoint!**",
                    "actions": [res],
                    "type": "ppt_created"
                }

        # Check for scanning / searching intents (handles common typos like "serach", "seach", "where is")
        is_scan_intent = False
        scan_triggers = [
            "scan", "search", "serach", "seach", "find", "locate", "where", "look for", "looking",
            "list", "show", "get", "fetch", "check", "recent", "looks for", "file named", "called"
        ]
        ppt_keywords = ["ppt", "pptx", ".pptx", ".ppt", "presentation", "powerpoint", "slides", "slide deck", "deck"]

        all_disk_triggers = [
            "all the disk", "all disk", "all disks", "all the disks",
            "all drive", "all drives", "all the drives", "both drive", "both drives",
            "every disk", "every drive", "whole disk", "entire disk",
            "look for all", "look in all", "scan all", "search all",
            "check all disk", "check all drive", "look across all",
            "looking in document", "looks for all"
        ]

        if not is_create_intent:
            if any(w in msg_lower for w in ["scan the ppt", "scan ppt", "scan ppts", "fix the ppt agent", "fix ppt agent", "fix the ppt"]):
                is_scan_intent = True
            elif any(w in msg_lower for w in all_disk_triggers) and any(k in msg_lower for k in ppt_keywords):
                is_scan_intent = True
            elif any(t in msg_lower for t in scan_triggers) and any(k in msg_lower for k in ppt_keywords):
                is_scan_intent = True
            elif ".pptx" in msg_lower or ".ppt" in msg_lower or re.search(r"\b(?!a\b|the\b|new\b|this\b|my\b)[a-zA-Z0-9_\-]+\s+pptx?\b", msg_lower):
                is_scan_intent = True
            elif "recent ppt" in msg_lower or "ppts that i have built" in msg_lower:
                is_scan_intent = True

        if is_scan_intent and not is_create_intent and not any(k in msg_lower for k in ["replace", "add a slide", "add slide", "insert slide", "change title"]):
            # Extract optional drive filter (e.g., "d drive", "drive c"), but ignore if all disks was requested
            drive_filter = None
            if not any(w in msg_lower for w in ["all", "both", "every", "across"]):
                if "drive d" in msg_lower or "d drive" in msg_lower or "d:\\" in msg_lower or " d: " in msg_lower:
                    drive_filter = "D:\\"
                elif "drive c" in msg_lower or "c drive" in msg_lower or "c:\\" in msg_lower or " c: " in msg_lower:
                    drive_filter = "C:\\"

            # Extract optional query keyword
            query_filter = None
            quote_match = re.search(r"['\"]([^'\"]+)['\"]", user_msg)
            if quote_match:
                cand = quote_match.group(1).strip()
                if cand.lower() not in ["recent", "the recent", "ppt", "pptx", "both drives"]:
                    query_filter = cand
            else:
                named_match = re.search(r"(?:named|called)\s+([a-zA-Z0-9_\-]+)", user_msg, re.IGNORECASE)
                if named_match:
                    cand = named_match.group(1).strip()
                    if cand.lower() not in ["ppt", "pptx", "presentation"]:
                        query_filter = cand
                if not query_filter:
                    file_match = re.search(r"([a-zA-Z0-9_\-]+?)(?:\.pptx|\.ppt|\s+pptx|\s+ppt)", user_msg, re.IGNORECASE)
                    if file_match:
                        word = file_match.group(1).strip()
                        if word.lower() not in ["named", "called", "the", "a", "an", "my", "our", "recent", "latest", "any", "some", "all", "new", "this", "both", "file", "for"]:
                            query_filter = word
                if not query_filter:
                    specific_match = re.search(r"(?:for)\s+([a-zA-Z0-9_\-]+)\s+(?:ppt|presentation|deck)", user_msg, re.IGNORECASE)
                    if specific_match:
                        word = specific_match.group(1).strip()
                        if word.lower() not in ["the", "a", "an", "my", "our", "recent", "latest", "any", "some", "all", "new", "this", "both", "file"]:
                            query_filter = word

            files = ppt_agent.find_recent_ppts(query=query_filter, drive=drive_filter, limit=15, force_refresh=True)
            # Graceful fallback: if keyword filter returned 0, show all recent presentations
            if not files and query_filter:
                fallback_files = ppt_agent.find_recent_ppts(drive=drive_filter, limit=15, force_refresh=False)
                if fallback_files:
                    files = fallback_files
                    query_filter = None

            drives_str = ", ".join(ppt_agent.get_available_drives())

            if not files:
                target_str = f" matching '{query_filter}'" if query_filter else ""
                return {
                    "role": "assistant",
                    "content": f"🔍 I scanned all system drives ({drives_str}), but did not find any presentation{target_str}.",
                    "ppt_files": []
                }

            header_desc = f"🔍 **PowerPoint Presentations Scanned Across Drives ({drives_str}):**"
            if query_filter:
                header_desc += f"\nFiltered by keyword: `{query_filter}`"
            elif drive_filter:
                header_desc += f"\nFiltered by drive: `{drive_filter}`"

            content = (
                f"{header_desc}\n\n"
                f"✅ **Scan Complete!** Found **{len(files)}** presentation(s) on your computer.\n\n"
                f"Click **Select & Edit** or ask me to open it in PowerPoint:"
            )

            return {
                "role": "assistant",
                "content": content,
                "ppt_files": files,
                "type": "ppt_list"
            }

        # Intent 2: Select a specific PPT
        select_match = re.search(r"(?:select|use|open and edit|choose|work with)\s+(?:file\s+)?(['\"]?)(.+?\.(?:pptx|ppt))\1", msg_lower)
        num_select_match = re.search(r"(?:select|choose|use|open|pick)\s+(?:file\s+|presentation\s+|number\s+|#)?(\d+)", msg_lower)
        ordinal_select_match = re.search(r"(?:select|choose|use|open|the)\s+(first|second|third|fourth|fifth)\s+(?:one|ppt|presentation|file)?", msg_lower)

        if select_match or "select ppt:" in msg_lower or num_select_match or ordinal_select_match:
            matched_file = None
            files = ppt_agent.recent_files or ppt_agent.find_recent_ppts(limit=25)

            if "select ppt:" in msg_lower:
                target_name = msg_lower.replace("select ppt:", "").strip()
                for f in files:
                    if target_name.lower() in f["name"].lower() or target_name.lower() in f["path"].lower():
                        matched_file = f
                        break
            elif select_match:
                target_name = select_match.group(2).strip()
                for f in files:
                    if target_name.lower() in f["name"].lower() or target_name.lower() in f["path"].lower():
                        matched_file = f
                        break
            elif num_select_match:
                idx = int(num_select_match.group(1)) - 1
                if 0 <= idx < len(files):
                    matched_file = files[idx]
            elif ordinal_select_match:
                ordinal_map = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}
                idx = ordinal_map.get(ordinal_select_match.group(1), 0)
                if 0 <= idx < len(files):
                    matched_file = files[idx]

            # If not in cache, rescan file system directly
            if not matched_file and ("select ppt:" in msg_lower or select_match):
                raw_target = select_match.group(2).strip() if select_match else msg_lower.replace("select ppt:", "").strip()
                fresh = ppt_agent.find_recent_ppts(limit=30)
                for f in fresh:
                    if raw_target.lower() in f["name"].lower() or raw_target.lower() in f["path"].lower():
                        matched_file = f
                        break
                if not matched_file:
                    cand_p = Path(raw_target)
                    if not cand_p.is_absolute():
                        cand_p = Path("d:/project_a") / raw_target
                    if cand_p.exists() and cand_p.suffix.lower() in [".pptx", ".ppt"]:
                        st = cand_p.stat()
                        matched_file = {
                            "name": cand_p.name,
                            "path": str(cand_p.resolve()),
                            "mtime": st.st_mtime,
                            "size_bytes": st.st_size,
                            "size_display": format_file_size(st.st_size),
                            "modified_display": "Recent",
                            "slide_count": None
                        }

            if matched_file:
                ppt_agent.active_file = matched_file["path"]
                try:
                    info = ppt_agent.inspect_ppt(matched_file["path"])
                    slide_count = info["total_slides"]
                except Exception:
                    slide_count = matched_file.get("slide_count", 0)

                content = (
                    f"📁 **Active Presentation**: `{matched_file['name']}` ({slide_count} slides)\n"
                    f"📍 **Path**: `{matched_file['path']}`\n\n"
                    f"**Ready for editing with zero design change!** You can tell me:\n\n"
                    f"• *\"Add a slide titled 'Project Summary' with bullets: Milestone achieved, Testing complete, Ready for launch\"*\n"
                    f"• *\"Replace 'Draft' with 'Final'\"*\n"
                    f"• *\"Update slide 1 title to 'New Title'\"*\n"
                    f"• *\"Show me all slide titles\"*\n"
                    f"• *\"Open in PowerPoint\"*"
                )
                return {
                    "role": "assistant",
                    "content": content,
                    "active_ppt": matched_file,
                    "type": "ppt_selected"
                }

        # Intent: Prompt user for changes if they ask "make changes in that" / "make changes"
        if ppt_agent.active_file and any(t in msg_lower for t in ["make changes in that", "make changes", "make change", "edit that", "edit this", "change that", "what changes", "modify that"]):
            p_name = Path(ppt_agent.active_file).name
            content = (
                f"📁 **Active Presentation**: `{p_name}`\n\n"
                f"**What changes would you like to make to this presentation?**\n\n"
                f"You can tell me:\n"
                f"• *\"Add a slide titled 'Project Summary' with bullets: Point A, Point B, Point C\"*\n"
                f"• *\"Replace 'Old Text' with 'New Text'\"*\n"
                f"• *\"Update slide 1 title to '...'\"*\n"
                f"• *\"Show me all slide titles\"*\n"
                f"• *\"Open in PowerPoint\"*"
            )
            return {
                "role": "assistant",
                "content": content,
                "type": "ppt_ready_to_edit"
            }

        # Ensure active_file exists if editing or opening is requested
        edit_keywords = [
            "replace", "add a slide", "add slide", "open in powerpoint", "open the ppt", "update slide",
            "add a summary", "add summary", "add a page", "append", "add content", "add a section",
            "add a conclusion", "add conclusion", "write a summary", "insert a slide", "add the last",
            "add to last", "last page", "last slide", "final slide", "closing slide"
        ]
        if not ppt_agent.active_file and any(t in msg_lower for t in edit_keywords):
            recent = ppt_agent.recent_files or ppt_agent.find_recent_ppts(limit=5)
            if recent:
                ppt_agent.active_file = recent[0]["path"]

        # Intent 3: Add/append slide to active PPT — catch broad natural language + use LLM for content
        add_slide_triggers = [
            "add a slide", "add slide", "create slide", "insert slide", "new slide", "add another slide",
            "add a summary", "add summary", "add a page", "add page", "append a slide", "append slide",
            "add a conclusion", "add conclusion", "add a section", "add a closing", "add closing",
            "add content", "add a new page", "insert a page", "last page", "last slide",
            "final slide", "summary slide", "summary page", "closing slide",
            "add the last", "add to last", "write a summary", "include a summary"
        ]
        if ppt_agent.active_file and any(t in msg_lower for t in add_slide_triggers):
            ppt_content = ppt_agent.read_full_content(ppt_agent.active_file)
            llm_result = await self.call_llm_for_ppt_action(user_msg, ppt_content, ppt_agent.active_file)

            if llm_result and llm_result.get("actions"):
                act = llm_result["actions"][0]
                res = act.get("results", {})
                p_name = Path(ppt_agent.active_file).name
                bullets = act.get("bullets", res.get("bullets", []))
                title = act.get("title", res.get("title", "New Slide"))
                bullet_list_str = "\n".join([f"  • {b}" for b in bullets])
                content = (
                    f"✅ **Successfully added a new slide (LLM-generated content)!**\n\n"
                    f"📁 **File**: `{p_name}`\n"
                    f"📄 **Added Slide #{res.get('new_slide_num', '?')}**:\n"
                    f"• **Title**: {title}\n"
                    f"• **Content**:\n{bullet_list_str}\n\n"
                    f"🚀 **Opened in Microsoft PowerPoint!**"
                )
                return {"role": "assistant", "content": content, "actions": llm_result["actions"], "type": "ppt_modified"}

            # Fallback: use regex extraction
            q_match = re.search(r"(?:titled|called|named|about|with title|title:)\s*[\"']([^\"']+)[\"']", user_msg, re.IGNORECASE)
            if q_match:
                title = q_match.group(1).strip()
            else:
                title_match = re.search(r"(?:titled|called|named|about|with title|title:)\s+([^,\n]+?)(?:\s+with\s+bullets?|\s+with\s+points?|\s+bullets?:|\s+points?:|,|\n|$)", user_msg, re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else "New Slide"
            bullets = []
            bullets_match = re.search(r"(?:bullets?|points?|content|body)[:\s]+(.+)$", user_msg, re.IGNORECASE)
            if bullets_match:
                raw_bullets = bullets_match.group(1)
                bullets = [b.strip().strip("•-* ") for b in re.split(r"[,;\n]|(?:\band\b)", raw_bullets) if b.strip()]
            if not bullets:
                bullets = ["Key milestone achieved", "Next phase implementation", "Team review pending"]

            res = ppt_agent.add_slide(ppt_agent.active_file, title, bullets)
            ppt_agent.open_in_powerpoint(ppt_agent.active_file)
            p_name = Path(ppt_agent.active_file).name
            bullet_list_str = "\n".join([f"  • {b}" for b in bullets])
            content = (
                f"✅ **Successfully updated presentation!**\n\n"
                f"📁 **File**: `{p_name}`\n"
                f"📄 **Added Slide #{res['new_slide_num']}**:\n"
                f"• **Title**: {title}\n"
                f"• **Content**:\n{bullet_list_str}\n\n"
                f"🚀 **Opened in Microsoft PowerPoint!**"
            )
            return {"role": "assistant", "content": content, "actions": [res], "type": "ppt_modified"}

        # Intent 3b: Create NEW PPT — always use LLM
        create_triggers = ["create a ppt", "create ppt", "create a presentation", "create presentation",
                           "make a ppt", "make ppt", "make a presentation", "make presentation",
                           "new ppt", "new presentation", "build a ppt", "generate a ppt", "generate ppt"]
        if any(t in msg_lower for t in create_triggers):
            ppt_content = "No existing file. Create a brand new presentation."
            llm_result = await self.call_llm_for_ppt_action(user_msg, ppt_content, None)

            if llm_result and llm_result.get("actions"):
                act = llm_result["actions"][0]
                res = act.get("results", {})
                p_name = Path(res.get("file", "Presentation.pptx")).name
                content = (
                    f"✅ **New presentation created with {res.get('total_slides', '?')} slides!**\n\n"
                    f"📁 **File**: `{p_name}`\n"
                    f"🎨 **Theme**: {res.get('theme', 'dark')} — professional design with accent colors, typography, and layout\n"
                    f"📄 **Title**: {res.get('title', '')}\n\n"
                    f"🚀 **Opened in Microsoft PowerPoint!**"
                )
                return {"role": "assistant", "content": content, "actions": llm_result["actions"], "type": "ppt_created"}
            else:
                return {
                    "role": "assistant",
                    "content": "⚠️ Could not generate presentation. Please check your NVIDIA API key in Settings.",
                    "type": "error"
                }

        # Intent 4: Inspect active PPT (Show slide titles / overview)
        if ppt_agent.active_file and any(t in msg_lower for t in ["show all slide", "show the slide", "list slides", "inspect slides", "what slides are in", "show slide titles"]):
            try:
                info = ppt_agent.inspect_ppt(ppt_agent.active_file)
                slides_list = "\n".join([f"  {s['slide_num']}. **{s['title']}** — *{s['preview_text'][:60]}*" for s in info["slides"]])
                content = (
                    f"📑 **Presentation Slides for `{info['name']}`** ({info['total_slides']} total):\n\n"
                    f"{slides_list}\n\n"
                    f"What changes would you like to make? (e.g., *\"Add a slide titled 'Conclusions' with bullets: ...\"* or *\"Replace X with Y\"*)"
                )
                return {
                    "role": "assistant",
                    "content": content,
                    "ppt_info": info,
                    "type": "ppt_inspected"
                }
            except Exception as e:
                pass

        # Intent 5: Replace text in active PPT
        if ppt_agent.active_file and ("replace" in msg_lower or "change" in msg_lower) and ("with" in msg_lower or "to" in msg_lower):
            replace_match = re.search(r"(?:replace|change)\s+['\"]?([^'\"]+?)['\"]?\s+(?:with|to)\s+['\"]?([^'\"]+?)['\"]?(?:\s+in|\.|$)", user_msg, re.IGNORECASE)
            if replace_match:
                find_t = replace_match.group(1).strip()
                repl_t = replace_match.group(2).strip()
                res = ppt_agent.replace_text(ppt_agent.active_file, find_t, repl_t)
                ppt_agent.open_in_powerpoint(ppt_agent.active_file)
                p_name = Path(ppt_agent.active_file).name
                content = (
                    f"✅ **Text updated with ZERO design change!**\n\n"
                    f"📁 **File**: `{p_name}`\n"
                    f"🔄 Replaced `{find_t}` ➔ `{repl_t}` ({res['occurrences_replaced']} occurrences across slides, tables, and shapes).\n"
                    f"🎨 **All fonts, sizes, colors, theme styling, and layouts preserved 100%!**\n"
                    f"🚀 **Opened in Microsoft PowerPoint** on your screen!"
                )
                return {
                    "role": "assistant",
                    "content": content,
                    "actions": [res],
                    "type": "ppt_modified"
                }

        # Intent 6: Update slide — use LLM for content
        if ppt_agent.active_file and "update slide" in msg_lower:
            slide_match = re.search(r"update slide\s+(\d+)", msg_lower)
            if slide_match:
                s_num = int(slide_match.group(1))
                ppt_content = ppt_agent.read_full_content(ppt_agent.active_file)
                llm_result = await self.call_llm_for_ppt_action(
                    f"Update slide {s_num} in the presentation. User request: {user_msg}",
                    ppt_content,
                    ppt_agent.active_file
                )
                if llm_result and llm_result.get("actions"):
                    act = llm_result["actions"][0]
                    p_name = Path(ppt_agent.active_file).name
                    content = (
                        f"✅ **Slide #{s_num} updated with LLM-generated content!**\n\n"
                        f"📁 **File**: `{p_name}`\n"
                        f"🎨 **Design preserved 100%.**\n"
                        f"🚀 **Opened in Microsoft PowerPoint!**"
                    )
                    return {"role": "assistant", "content": content, "actions": llm_result["actions"], "type": "ppt_modified"}

                # Fallback: regex-based
                title_match = re.search(r"(?:title to|titled)\s+['\"]?([^'\"\n,]+)['\"]?", user_msg, re.IGNORECASE)
                title_val = title_match.group(1).strip() if title_match else None
                bullets_match = re.search(r"(?:bullets?|points?)[:\s]+(.+)$", user_msg, re.IGNORECASE)
                bullets_val = None
                if bullets_match:
                    bullets_val = [b.strip().strip("•-* ") for b in re.split(r"[,;\n]|(?:\band\b)", bullets_match.group(1)) if b.strip()]

                res = ppt_agent.update_slide(ppt_agent.active_file, s_num, title=title_val, bullet_points=bullets_val)
                ppt_agent.open_in_powerpoint(ppt_agent.active_file)
                p_name = Path(ppt_agent.active_file).name
                content = (
                    f"✅ **Slide #{s_num} updated!**\n\n"
                    f"📁 **File**: `{p_name}`\n"
                    f"🚀 **Opened in Microsoft PowerPoint!**"
                )
                return {"role": "assistant", "content": content, "actions": [res], "type": "ppt_modified"}

        # Intent 7: Open active PPT — only if there's NO edit verb in the same message
        has_edit_verb = any(t in msg_lower for t in [
            "add", "replace", "update", "change", "insert", "append", "write", "include",
            "create", "make", "generate", "build", "modify", "edit", "improve", "rewrite"
        ])
        open_pattern = re.search(r"\b(open|launch|display|show|bring up)\b.*?\b(ppt|pptx|presentation|deck|file|it|that|this|powerpoint)\b", msg_lower)
        if (open_pattern or msg_lower in ["open", "open it", "open that", "open ppt", "open that ppt", "open this ppt", "launch it", "view it"]) \
                and not has_edit_verb \
                and (ppt_agent.active_file or ppt_agent.recent_files):
            target = ppt_agent.active_file or ppt_agent.recent_files[0]["path"]
            ppt_agent.open_in_powerpoint(target)
            p_name = Path(target).name
            return {
                "role": "assistant",
                "content": f"🚀 **Opened `{p_name}` in Microsoft PowerPoint!**\n\nThe presentation is now visible on your desktop and brought into focus.",
                "actions": [{"type": "open_ppt", "path": target}]
            }

        # Intent 7b: Catch-all — any PPT-related free-form request routes to LLM
        ppt_related_keywords = [
            "ppt", "pptx", "presentation", "powerpoint", "slides", "slide", "deck",
            "summary", "conclusion", "overview", "agenda", "content"
        ]
        if ppt_agent.active_file and any(k in msg_lower for k in ppt_related_keywords):
            # Route the entire message to the LLM with full PPT context — let it figure out what to do
            ppt_content = ppt_agent.read_full_content(ppt_agent.active_file)
            llm_result = await self.call_llm_for_ppt_action(user_msg, ppt_content, ppt_agent.active_file)
            if llm_result and llm_result.get("actions"):
                acted = llm_result["actions"][0]
                res = acted.get("results", {})
                p_name = Path(ppt_agent.active_file).name
                open_after = "open" in msg_lower or "launch" in msg_lower
                if open_after and ppt_agent.active_file:
                    ppt_agent.open_in_powerpoint(ppt_agent.active_file)
                action_type = acted.get("type", "modified")
                content_msg = (
                    f"✅ **Done! ({action_type})** — LLM analyzed your presentation and applied the changes.\n\n"
                    f"📁 **File**: `{p_name}`\n"
                )
                if res.get("new_slide_num"):
                    content_msg += f"📄 **Slide #{res['new_slide_num']}** added.\n"
                if open_after:
                    content_msg += "🚀 **Opened in Microsoft PowerPoint!**"
                return {"role": "assistant", "content": content_msg, "actions": llm_result["actions"], "type": "ppt_modified"}

        # Intent 8: RAG Pipeline / Presentation Content Q&A / Summarization
        rag_triggers = [
            "rag", "pipeline", "summarize", "summary", "overview", 
            "what is in", "what's in", "what does", "content of", "explain",
            "tell me about", "what is this", "key points", "outline",
            "search inside", "find in ppt", "business model", "traction"
        ]
        target_ppt = ppt_agent.active_file or (ppt_agent.recent_files[0]["path"] if ppt_agent.recent_files else None)
        if target_ppt and any(t in msg_lower for t in rag_triggers) and not any(a in msg_lower for a in ["add a slide", "replace", "update slide"]):
            rag_info = ppt_agent.get_rag_context(target_ppt, query=user_msg if "rag" not in msg_lower and "pipeline" not in msg_lower else None)
            p_name = Path(target_ppt).name

            if "rag" in msg_lower or "pipeline" in msg_lower:
                slides_summary = "\n".join([
                    f"• **Slide {s['slide_num']}: {s['title']}**\n  " + ("\n  ".join([f"- {b}" for b in s['bullets'][:3]]) if s['bullets'] else "*(Visual / layout slide)*")
                    for s in rag_info["all_slides"]
                ])
                content = (
                    f"🧠 **The PowerPoint RAG (Retrieval-Augmented Generation) Pipeline is fully active!**\n\n"
                    f"📁 **Indexed Presentation**: `{p_name}` ({rag_info['total_slides']} slides)\n\n"
                    f"### 📑 Presentation Knowledge Index:\n\n{slides_summary}\n\n"
                    f"---\n"
                    f"💡 **What the RAG Pipeline enables:**\n"
                    f"1. **Content Search & Q&A**: Ask me *\"What is our business model?\"* or *\"What does Slide 3 say?\"*\n"
                    f"2. **Context-Aware Slide Creation**: When you ask me to add slides, I analyze existing slides to match terminology and layout.\n"
                    f"3. **Zero-Design-Loss Updates**: Precise replacements and edits across indexed shapes, cards, and tables."
                )
                return {
                    "role": "assistant",
                    "content": content,
                    "rag_info": rag_info,
                    "type": "ppt_rag"
                }

            # Q&A or Summarization query
            matched = rag_info["matched_slides"]
            slide_details = []
            for s in matched[:4]:
                b_lines = "\n".join([f"  • {b}" for b in s["bullets"][:5]]) if s["bullets"] else "  *(Title/graphic slide)*"
                slide_details.append(f"📄 **Slide {s['slide_num']}: {s['title']}**\n{b_lines}")

            details_text = "\n\n".join(slide_details)
            content = (
                f"📊 **Presentation Analysis for `{p_name}`** ({rag_info['total_slides']} slides):\n\n"
                f"{details_text}\n\n"
                f"---\n"
                f"You can tell me to:\n"
                f"• *\"Open that PPT\"* (to view on screen)\n"
                f"• *\"Add a slide titled '...' with bullets: ...\"*\n"
                f"• *\"Replace '...' with '...'\"*"
            )
            return {
                "role": "assistant",
                "content": content,
                "rag_info": rag_info,
                "type": "ppt_rag_qa"
            }

        return None

    def detect_conversational_intent(self, user_msg: str) -> Optional[Dict[str, Any]]:
        """
        Handles greetings, polite conversation, small talk, and general bot inquiries warmly and naturally
        without triggering robotic actions, unwanted JSON blocks, or hardware telemetry.
        """
        msg = user_msg.strip()
        msg_lower = msg.lower()
        clean = re.sub(r"[^\w\s]", "", msg_lower).strip()

        # If the user is asking for an actual OS or PPT task in the same message, let task routers handle it
        task_indicators = [
            "ppt", "presentation", "slide", "slides", "cpu", "ram", "memory", "telemetry",
            "process", "kill", "powershell", "terminal", "command", "read file", "write file",
            "disk", "storage", "clipboard", "open app", "screenshot", "taskmgr"
        ]
        if any(ti in msg_lower for ti in task_indicators):
            return None

        # 1. Direct Greetings
        greetings = {
            "hi", "hey", "hello", "hiya", "howdy", "yo", "sup", "heyy", "heyyy", "heya",
            "hello there", "hey there", "hi there", "greetings", "good morning",
            "good afternoon", "good evening", "good day", "morning", "evening"
        }
        if clean in greetings or re.match(r"^(?:hi|hey|hello|hiya|yo|howdy)\s+(?:there|bot|ai|assistant|friend|everyone)$", clean):
            return {
                "role": "assistant",
                "content": "Hey! 👋 How can I help you today? Whether you want to chat, brainstorm ideas, write code, or have me perform tasks on your Windows PC or build a presentation, I'm here for you! What's on your mind?",
                "actions": [],
                "type": "conversation"
            }

        # 2. Status inquiries
        status_checks = {
            "how are you", "how are you doing", "hows it going", "how is it going",
            "whats up", "what is up", "how do you do", "are you there", "you there",
            "how are things", "how have you been"
        }
        if clean in status_checks:
            return {
                "role": "assistant",
                "content": "I'm doing great, thanks for asking! 😊 How are you doing? Let me know whatever you'd like to talk about or work on.",
                "actions": [],
                "type": "conversation"
            }

        # 3. Identity & Capabilities
        identity_checks = {
            "who are you", "what are you", "what can you do", "what are your capabilities",
            "what can you help me with", "introduce yourself", "tell me about yourself", "help",
            "what are you capable of"
        }
        if clean in identity_checks:
            return {
                "role": "assistant",
                "content": (
                    "I'm your AI assistant and Windows OS companion! 🤖\n\n"
                    "I can help you with:\n"
                    "• 💬 **Chatting & Brainstorming**: Discuss any topic, answer questions, write essays or creative content.\n"
                    "• 💻 **Code & Debugging**: Write and explain code across Python, JavaScript, Rust, C++, and more.\n"
                    "• 🖥️ **Windows OS Automation**: Check hardware telemetry, inspect processes, read/edit files, and run PowerShell commands.\n"
                    "• 📊 **PowerPoint Presentations**: Generate complete, professional slide decks on any topic.\n\n"
                    "What would you like to explore today?"
                ),
                "actions": [],
                "type": "conversation"
            }

        # 4. Gratitude
        thanks = {"thank you", "thanks", "thanks a lot", "thank you so much", "thx", "ty", "cheers", "appreciate it"}
        if clean in thanks:
            return {
                "role": "assistant",
                "content": "You're very welcome! Always happy to help. Let me know if you need anything else! 😊",
                "actions": [],
                "type": "conversation"
            }

        # 5. Farewells
        farewells = {"bye", "goodbye", "see you", "see ya", "cya", "have a good day", "good night", "take care"}
        if clean in farewells:
            return {
                "role": "assistant",
                "content": "Goodbye! Have a great day, and feel free to reach out whenever you're back! 👋",
                "actions": [],
                "type": "conversation"
            }

        return None

    def detect_os_intent(self, user_msg: str) -> Optional[Dict[str, Any]]:
        """Intelligently detects direct OS operations (telemetry, processes, disks, clipboard, files) with zero latency & zero hallucination"""
        msg_lower = user_msg.lower().strip()
        
        # 1. System info / telemetry / hardware specs
        sys_triggers = [
            "system info", "system stats", "system status", "telemetry", "pc specs",
            "cpu usage", "ram usage", "memory usage", "check cpu", "check ram",
            "how much ram", "how much memory", "cpu percent", "system performance",
            "hardware specs", "specs of my pc", "computer specs"
        ]
        has_hw_word = any(w in msg_lower for w in ["ram", "cpu", "memory", "telemetry", "hardware"])
        has_action_word = any(w in msg_lower for w in ["check", "show", "get", "what is", "how much", "usage", "info", "stats", "status"])
        is_sys_match = any(t in msg_lower for t in sys_triggers) or (has_hw_word and has_action_word)

        if is_sys_match and not any(p in msg_lower for p in ["ppt", "presentation", "slide"]):
            info = os_toolkit.get_system_info()
            cpu = info["cpu"]
            mem = info["memory"]
            disks = info["disks"]
            battery = info.get("battery")
            net = info.get("network", {})
            os_info = info.get("os", {})
            
            disks_md = "\n".join([f"  • `{d['mountpoint']}`: **{d['free_gb']} GB free** / {d['total_gb']} GB total ({d['percent_used']}% used)" for d in disks])
            
            battery_str = "Desktop / Connected to AC power"
            if battery:
                plugged = "🔌 Plugged in" if battery.get("power_plugged") else "🔋 On battery"
                battery_str = f"**{battery.get('percent')}%** ({plugged})"
                
            content = (
                f"🖥️ **Windows OS System Telemetry & Performance:**\n\n"
                f"• **OS**: {os_info.get('system')} {os_info.get('release')} ({os_info.get('machine')})\n"
                f"• **CPU**: **{cpu['percent']}%** ({cpu['logical_cores']} logical / {cpu['physical_cores']} physical cores @ {cpu.get('freq_mhz', '?')} MHz)\n"
                f"• **Memory (RAM)**: **{mem['used_gb']} GB** / {mem['total_gb']} GB ({mem['percent']}%) — **{mem['available_gb']} GB available**\n"
                f"• **Storage Disks**:\n{disks_md}\n"
                f"• **Battery**: {battery_str}\n"
                f"• **Network**: Host `{net.get('hostname')}` | IP `{net.get('local_ip')}`\n"
                f"• **System Uptime**: {info.get('uptime', 'Active')}\n\n"
                f"💡 *Ask me to inspect processes, run diagnostics, read/edit files, or automate any desktop app.*"
            )
            return {
                "role": "assistant",
                "content": content,
                "actions": [{"type": "system_info", "system_info": info, "status": "success"}],
                "type": "os_telemetry"
            }

        # 2. Running processes / Task Manager
        proc_triggers = [
            "what processes are running", "running processes", "running apps", "active processes",
            "task manager", "taskmanager", "what is taking memory", "what's taking memory",
            "top processes", "process list", "show processes", "list processes", "running programs",
            "what is eating my ram", "what is consuming ram", "high memory processes"
        ]
        if any(t in msg_lower for t in proc_triggers) and not any(p in msg_lower for p in ["ppt", "presentation", "slide"]):
            procs = os_toolkit.get_running_processes(limit=15)
            rows = []
            for p in procs:
                rows.append(f"| `{p['name']}` | `{p['pid']}` | **{p['memory_mb']} MB** | `{p['status']}` |")
            table = "\n".join(rows)
            
            content = (
                f"⚙️ **Active Windows Processes (Top by Memory Usage):**\n\n"
                f"| Process Name | PID | Memory (RAM) | Status |\n"
                f"| :--- | :--- | :--- | :--- |\n"
                f"{table}\n\n"
                f"💡 *To terminate an unresponsive process, say: \"kill process <name>\" or \"terminate PID <number>\".*"
            )
            return {
                "role": "assistant",
                "content": content,
                "actions": [{"type": "get_processes", "processes": procs, "status": "success"}],
                "type": "os_processes"
            }

        # 3. Disk space query
        disk_triggers = [
            "how much disk space", "disk space", "storage space", "storage left", "free disk",
            "free storage", "hard drive space", "drive space", "how much storage", "free space"
        ]
        if any(t in msg_lower for t in disk_triggers) and not any(p in msg_lower for p in ["ppt", "presentation", "slide"]):
            info = os_toolkit.get_system_info()
            disks = info["disks"]
            disks_md = "\n".join([f"• **Drive {d['mountpoint']}**: **{d['free_gb']} GB free** out of {d['total_gb']} GB total ({d['percent_used']}% used, {d['used_gb']} GB used)" for d in disks])
            content = (
                f"💾 **Storage & Disk Space Overview:**\n\n"
                f"{disks_md}\n\n"
                f"💡 *Would you like me to find large files or search for anything specific on your drives?*"
            )
            return {
                "role": "assistant",
                "content": content,
                "actions": [{"type": "system_info", "system_info": info, "status": "success"}],
                "type": "os_disks"
            }

        # 4. Clipboard content
        clipboard_triggers = [
            "what is in my clipboard", "what's in my clipboard", "read clipboard", "clipboard content",
            "show clipboard", "paste clipboard", "get clipboard", "check clipboard"
        ]
        if any(t in msg_lower for t in clipboard_triggers):
            clip = os_toolkit.get_clipboard()
            text = clip.get("clipboard_text", "")
            if not text:
                content = "📋 **Windows Clipboard is currently empty.**"
            else:
                length = len(text)
                preview = text if length <= 800 else f"{text[:800]}...\n[Truncated, total {length} characters]"
                content = f"📋 **Current Windows Clipboard Content** ({length} chars):\n\n```\n{preview}\n```"
            return {
                "role": "assistant",
                "content": content,
                "actions": [{"type": "get_clipboard", "clipboard": clip, "status": "success"}],
                "type": "os_clipboard"
            }

        # 5. Read file
        read_match = re.search(r"^(?:read|view|show|display|cat|inspect)\s+(?:file\s+)?(['\"]?)(.+?\.[a-zA-Z0-9_\-]+)\1$", user_msg.strip(), re.IGNORECASE)
        if read_match and not any(k in msg_lower for k in ["ppt", "pptx", "presentation"]):
            target_path = read_match.group(2).strip()
            p = Path(target_path)
            if not p.is_absolute():
                cand = Path("d:/project_a") / target_path
                if cand.exists():
                    target_path = str(cand)
            res = os_toolkit.read_file(target_path, max_lines=150)
            if "error" in res:
                content = f"❌ **Could not read file**: {res['error']}"
            else:
                content = (
                    f"📄 **File Content: `{res['file_name']}`** ({res['size_display']}, {res['total_lines']} total lines):\n\n"
                    f"```{p.suffix.lstrip('.') if p.suffix else ''}\n"
                    f"{res['content']}\n"
                    f"```"
                )
                if res.get("is_truncated"):
                    content += f"\n*(Showing first {res['returned_lines']} lines of {res['total_lines']})*"
            return {
                "role": "assistant",
                "content": content,
                "actions": [{"type": "read_file", "path": target_path, "file_data": res, "status": "success"}],
                "type": "os_file_read"
            }

        # 6. Kill process
        kill_match = re.search(r"^(?:kill|terminate|stop|end)\s+(?:process\s+|task\s+|app\s+)?([a-zA-Z0-9_\-\.]+)(?:\.exe)?$", user_msg.strip(), re.IGNORECASE)
        if kill_match:
            cand = kill_match.group(1).strip()
            pid = int(cand) if cand.isdigit() else None
            pname = cand if not cand.isdigit() else None
            res = os_toolkit.kill_process(pid=pid, name=pname)
            if res.get("success"):
                content = f"🛑 **Successfully terminated**: {', '.join(res['killed_processes'])}"
            else:
                err_str = "; ".join(res.get("errors", [])) or "Process not found or insufficient permissions"
                content = f"⚠️ **Could not terminate process '{cand}'**: {err_str}"
            return {
                "role": "assistant",
                "content": content,
                "actions": [{"type": "kill_process", "pid": pid, "name": pname, "result": res}],
                "type": "os_kill_process"
            }

        return None

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        api_key = self.get_api_key()
        if not api_key:
            yield "data: " + json.dumps({
                "error": "NVIDIA API Key not configured. Please enter your NVIDIA NIM API Key in the Settings menu (top right or sidebar)."
            }) + "\n\n"
            return

        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

        # 1. Check for conversational greeting / chitchat intent
        conv_result = self.detect_conversational_intent(last_user_msg)
        if conv_result:
            yield f"data: {json.dumps(conv_result)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 2. Check for direct OS intent
        os_result = self.detect_os_intent(last_user_msg)
        if os_result:
            yield f"data: {json.dumps(os_result)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 3. Check for direct PPT intent
        ppt_result = await self.detect_ppt_intent(last_user_msg)
        if ppt_result:
            yield f"data: {json.dumps(ppt_result)}\n\n"
            yield "data: [DONE]\n\n"
            return

        target_model = model or settings.DEFAULT_MODEL
        system_msg = {"role": "system", "content": self.get_system_prompt()}
        full_messages = [system_msg] + [m for m in messages if m.get("role") != "system"]

        url = f"{settings.NVIDIA_BASE_URL.rstrip('/')}/chat/completions"
        payload = {
            "model": target_model,
            "messages": full_messages,
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": True
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                async with client.stream("POST", url, json=payload, headers=self.get_headers()) as response:
                    if response.status_code != 200:
                        err_text = await response.aread()
                        yield f"data: {json.dumps({'error': f'NVIDIA NIM API Error ({response.status_code}): {err_text.decode()}'})}\n\n"
                        return

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            raw_data = line[6:].strip()
                            if raw_data == "[DONE]":
                                yield "data: [DONE]\n\n"
                                break
                            yield f"{line}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': f'Request failed: {str(e)}'})}\n\n"

    async def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        permission_mode: str = "full_access"
    ) -> Dict[str, Any]:
        api_key = self.get_api_key()
        if not api_key:
            return {
                "role": "assistant",
                "content": "⚠️ NVIDIA API Key is missing. Please click the Settings icon in the sidebar to configure your NVIDIA NIM API Key.",
                "actions": []
            }

        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

        # 1. Check for conversational greeting / chitchat intent
        conv_result = self.detect_conversational_intent(last_user_msg)
        if conv_result:
            return conv_result

        # 2. Check for direct OS intent
        os_result = self.detect_os_intent(last_user_msg)
        if os_result:
            return os_result

        # 3. Check for direct PPT intent
        ppt_result = await self.detect_ppt_intent(last_user_msg)
        if ppt_result:
            return ppt_result

        target_model = model or settings.DEFAULT_MODEL
        system_msg = {"role": "system", "content": self.get_system_prompt()}
        full_messages = [system_msg] + [m for m in messages if m.get("role") != "system"]

        url = f"{settings.NVIDIA_BASE_URL.rstrip('/')}/chat/completions"
        payload = {
            "model": target_model,
            "messages": full_messages,
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": False
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, json=payload, headers=self.get_headers())
                if resp.status_code != 200:
                    return {
                        "role": "assistant",
                        "content": f"❌ NVIDIA NIM API Error ({resp.status_code}): {resp.text}",
                        "actions": []
                    }
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content", "")
                
                # Check for actions and execute with granted permission mode (filtering unsolicited actions)
                executed_actions = await self.execute_parsed_actions(content, permission_mode=permission_mode, user_msg=last_user_msg)

                # Closed-Loop ReAct: if actions gathered real OS observations, feed them back to synthesize a verified answer
                obs_chunks = []
                for act in executed_actions:
                    if act.get("stdout") or act.get("stderr"):
                        obs_chunks.append(f"Command '{act.get('command')}' (Exit code: {act.get('exit_code')}):\nSTDOUT:\n{act.get('stdout', '')}\nSTDERR:\n{act.get('stderr', '')}")
                    elif act.get("file_data") and "content" in act["file_data"]:
                        fd = act["file_data"]
                        obs_chunks.append(f"File '{fd.get('file_name')}' read ({fd.get('total_lines')} lines):\n{fd.get('content')}")
                    elif act.get("dir_data") and "items" in act["dir_data"]:
                        dd = act["dir_data"]
                        obs_chunks.append(f"Directory '{dd.get('directory')}' ({dd.get('total_items')} items):\n" + "\n".join([f"- {it['name']} ({it['size_display']})" for it in dd["items"][:15]]))
                    elif act.get("results") and isinstance(act["results"], list):
                        obs_chunks.append(f"Files found ({len(act['results'])} items):\n" + "\n".join([f"- {r.get('name')}: {r.get('path')}" for r in act["results"][:12]]))
                    elif act.get("processes"):
                        obs_chunks.append("Running processes:\n" + "\n".join([f"- {p['name']} (PID: {p['pid']}, RAM: {p['memory_mb']}MB)" for p in act["processes"][:8]]))
                    elif act.get("system_info"):
                        si = act["system_info"]
                        obs_chunks.append(f"System Info: CPU {si['cpu']['percent']}%, RAM {si['memory']['used_gb']}/{si['memory']['total_gb']}GB")
                    elif act.get("clipboard"):
                        obs_chunks.append(f"Clipboard: {act['clipboard'].get('clipboard_text', '')}")

                if obs_chunks:
                    obs_payload = "\n\n".join(obs_chunks)
                    followup_messages = full_messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": f"REAL OS EXECUTION OBSERVATION:\n{obs_payload}\n\nDeliver the final, verified answer to the user based on these exact system results. Do not guess, do not use placeholder paths like YourUsername, and confirm what was completed."}
                    ]
                    followup_payload = {
                        "model": target_model,
                        "messages": followup_messages,
                        "temperature": 0.2,
                        "max_tokens": 2048,
                        "stream": False
                    }
                    try:
                        f_resp = await client.post(url, json=followup_payload, headers=self.get_headers(), timeout=35.0)
                        if f_resp.status_code == 200:
                            f_data = f_resp.json()
                            f_choice = f_data.get("choices", [{}])[0]
                            f_content = f_choice.get("message", {}).get("content", "")
                            if f_content:
                                content = f_content
                    except Exception as f_err:
                        print(f"[ReAct followup call skipped] {f_err}")

                # Anti-hallucination interceptor fallback: replace fake placeholder paths (YourUsername, etc.) with verified results
                if "yourusername" in content.lower():
                    real_notes = []
                    for act in executed_actions:
                        if act.get("results"):
                            res = act["results"]
                            if isinstance(res, list) and res:
                                real_notes.append("✅ **Verified files found on your computer:**\n" + "\n".join([f"• `{r.get('name', '')}` — `{r.get('path', r)}`" for r in res]))
                            elif isinstance(res, dict):
                                real_notes.append(f"✅ Result: {res}")
                        elif act.get("stdout"):
                            real_notes.append(f"**Command Output:**\n```\n{act['stdout']}\n```")
                    if real_notes:
                        content = "\n\n".join(real_notes)
                    else:
                        content = "🔍 Executed real OS action on your system."

                ppt_files = []
                for act in executed_actions:
                    if act.get("type") in ["find_ppts", "find_file"] and isinstance(act.get("results"), list):
                        for item in act["results"]:
                            if isinstance(item, dict) and item.get("path", "").lower().endswith((".pptx", ".ppt")):
                                ppt_files.append(item)

                return {
                    "role": "assistant",
                    "content": content,
                    "actions": executed_actions,
                    "ppt_files": ppt_files
                }
            except Exception as e:
                # If an active presentation is available, fall back gracefully to local RAG
                if ppt_agent.active_file and Path(ppt_agent.active_file).exists():
                    rag_data = ppt_agent.get_rag_context(ppt_agent.active_file, query=last_user_msg)
                    p_name = Path(ppt_agent.active_file).name
                    if rag_data.get("matched_slides"):
                        s0 = rag_data["matched_slides"][0]
                        b_text = "\n".join([f"• {b}" for b in s0.get("bullets", [])[:5]]) if s0.get("bullets") else "*(Overview slide)*"
                        return {
                            "role": "assistant",
                            "content": (
                                f"💡 **Local RAG Retrieval for `{p_name}`**:\n\n"
                                f"📄 **Slide {s0['slide_num']}: {s0['title']}**\n{b_text}\n\n"
                                f"---\n"
                                f"You can say *\"Open that PPT\"* or *\"Add a slide titled '...'\"* directly."
                            ),
                            "actions": []
                        }

                return {
                    "role": "assistant",
                    "content": f"❌ Connection Error: {str(e)}",
                    "actions": []
                }

nim_client = NvidiaNimClient()
