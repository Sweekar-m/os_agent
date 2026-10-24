import asyncio
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Callable

from db import (
    create_task,
    get_task,
    list_tasks,
    update_task_status,
    save_task_plan,
    save_completion_report,
    add_task_event,
    log_audit,
    recover_stale_tasks
)
from permission_engine import permission_engine, PermissionDecision, ActionCategory, RiskLevel
from llm_gateway import llm_gateway
from agents.browser_agent import browser_agent
from agents.file_agent import file_agent
from agents.system_agent import system_agent
from agents.vision_agent import vision_agent
from speech_service import speech_service

logger = logging.getLogger("orchestrator")
logger.setLevel(logging.INFO)

class TaskState:
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class MultiAgentOrchestrator:
    """
    Central Coordinator for the Windows OS Assistant.
    Routes natural-language commands to Browser, File, System, and Vision Agents,
    enforcing human permissions and verifying execution outcomes.
    """
    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.event_subscribers: Dict[str, List[asyncio.Queue]] = {}
        self.default_workspace = str(Path(__file__).resolve().parent.parent)

    def startup(self):
        """Called on application startup to recover stale tasks from unexpected crashes."""
        logger.info("Initializing Multi-Agent OS Orchestrator...")
        recover_stale_tasks()

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        if task_id not in self.event_subscribers:
            self.event_subscribers[task_id] = []
        self.event_subscribers[task_id].append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue):
        if task_id in self.event_subscribers:
            if q in self.event_subscribers[task_id]:
                self.event_subscribers[task_id].remove(q)
            if not self.event_subscribers[task_id]:
                del self.event_subscribers[task_id]

    async def broadcast_event(self, task_id: str, event_type: str, data: Dict[str, Any]):
        """Dispatches event to DB and active SSE / WebSocket subscriber queues."""
        evt_payload = {
            "task_id": task_id,
            "event_type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        add_task_event(task_id, event_type, data)

        subscribers = self.event_subscribers.get(task_id, [])
        for q in list(subscribers):
            try:
                await q.put(evt_payload)
            except Exception:
                pass

    def emergency_stop(self) -> Dict[str, Any]:
        """Halts all active tasks immediately and blocks new actions."""
        res = permission_engine.trigger_emergency_stop()
        # Cancel all active asyncio tasks
        for tid, worker in list(self.active_tasks.items()):
            if not worker.done():
                worker.cancel()
            update_task_status(tid, TaskState.CANCELLED, error_message="Emergency Stop Triggered")
        self.active_tasks.clear()
        return res

    def clear_emergency_stop(self) -> Dict[str, Any]:
        return permission_engine.clear_emergency_stop()

    async def cancel_task(self, task_id: str) -> bool:
        """Cancels a specific running task."""
        update_task_status(task_id, TaskState.CANCELLED, error_message="Cancelled by user")
        permission_engine.reset_session(task_id)

        worker = self.active_tasks.get(task_id)
        if worker and not worker.done():
            worker.cancel()

        await self.broadcast_event(task_id, "TASK_CANCELLED", {"reason": "User cancellation"})
        self.active_tasks.pop(task_id, None)
        return True

    async def create_and_run_task(
        self,
        prompt: str,
        workspace: Optional[str] = None,
        permission_mode: str = "balanced",
        model: Optional[str] = None,
        chat_history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Public entrypoint: registers task, triggers planning, and spawns the multi-agent worker.
        """
        if permission_engine.is_emergency_stopped():
            raise RuntimeError("Emergency stop is currently active. Clear emergency stop in Settings to proceed.")

        ws = workspace or self.default_workspace
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        create_task(task_id, prompt, ws, permission_mode, model or llm_gateway.chat_model)

        await self.broadcast_event(task_id, "TASK_CREATED", {
            "prompt": prompt,
            "workspace": ws,
            "permission_mode": permission_mode,
            "model": model or llm_gateway.chat_model
        })

        if permission_mode == "full_access":
            permission_engine._full_access_grants[task_id] = True

        worker = asyncio.create_task(
            self._run_multi_agent_pipeline(task_id, prompt, ws, permission_mode, chat_history=chat_history)
        )
        self.active_tasks[task_id] = worker
        return task_id

    # ----------------- Pipeline State Machine -----------------

    async def _run_multi_agent_pipeline(
        self,
        task_id: str,
        prompt: str,
        workspace: str,
        permission_mode: str,
        chat_history: Optional[List[Dict[str, Any]]] = None
    ):
        try:
            # 1. INTENT & ROUTING PHASE
            update_task_status(task_id, TaskState.PLANNING)
            await self.broadcast_event(task_id, "PHASE_CHANGED", {"phase": TaskState.PLANNING})

            p_lower = prompt.lower().strip()

            # Conversational greetings check
            if p_lower in ["hi", "hello", "hey", "good morning", "yo", "sup", "how are you"]:
                resp_text = f"Hello! I am your Windows OS Assistant. I'm ready to control your browser, manage files, check system performance, or inspect what's on your screen. What would you like to do?"
                await self._complete_task_with_response(task_id, prompt, resp_text, agents_used=["Assistant"])
                return

            # Check if screen vision analysis is explicitly requested
            is_screen_query = any(k in p_lower for k in [
                "what's on my screen", "what am i seeing", "look at my screen", "explain this error",
                "what error", "describe screen", "screenshot", "what app is open"
            ])

            if is_screen_query:
                await self.broadcast_event(task_id, "AGENT_SELECTED", {
                    "agent": "Vision Agent",
                    "action": "Analyzing desktop screen and active window..."
                })
                vision_res = await vision_agent.analyze_screen(prompt=prompt)
                analysis_text = vision_res.get("analysis", "I analyzed your screen.")
                active_app = (vision_res.get("active_window") or {}).get("title", "Desktop")
                final_text = f"**Screen Analysis ({active_app})**:\n\n{analysis_text}"
                await self._complete_task_with_response(task_id, prompt, final_text, agents_used=["Vision Agent"])
                return

            # 2. MULTI-AGENT TASK PLANNING
            plan = await self._formulate_multi_agent_plan(task_id, prompt, workspace, chat_history=chat_history)
            save_task_plan(task_id, plan)
            await self.broadcast_event(task_id, "PLAN_GENERATED", plan)

            # 3. EXECUTION PHASE
            update_task_status(task_id, TaskState.EXECUTING)
            await self.broadcast_event(task_id, "PHASE_CHANGED", {"phase": TaskState.EXECUTING})

            executed_actions = []
            agents_engaged = set()

            for step_idx, step in enumerate(plan.get("steps", [])):
                if permission_engine.is_emergency_stopped():
                    raise asyncio.CancelledError("Emergency stop engaged during execution.")

                agent_name = step.get("agent", "System Agent")
                action_name = step.get("action", "")
                params = step.get("params", {})
                category = step.get("category", ActionCategory.SYSTEM_COMMAND)
                risk = step.get("risk", RiskLevel.LOW)
                agents_engaged.add(agent_name)

                # Permission Check
                is_permitted, grant_src = permission_engine.is_action_permitted(
                    task_id=task_id,
                    workspace_root=workspace,
                    category=category,
                    permission_mode=permission_mode
                )

                if not is_permitted:
                    update_task_status(task_id, TaskState.AWAITING_APPROVAL)
                    await self.broadcast_event(task_id, "APPROVAL_REQUIRED", {
                        "step": step.get("title"),
                        "agent": agent_name,
                        "action": action_name,
                        "reason": step.get("reason", f"{agent_name} needs to perform {action_name}"),
                        "risk_level": risk
                    })

                    decision, meta = await permission_engine.request_approval(
                        task_id=task_id,
                        workspace_root=workspace,
                        action_type=f"{agent_name}.{action_name}",
                        reason=step.get("reason"),
                        impact=step.get("impact"),
                        risk_level=risk
                    )

                    if decision in [PermissionDecision.DENY, PermissionDecision.CANCEL]:
                        update_task_status(task_id, TaskState.CANCELLED)
                        await self.broadcast_event(task_id, "TASK_CANCELLED", {
                            "reason": f"User denied or cancelled {agent_name} action"
                        })
                        return

                    update_task_status(task_id, TaskState.EXECUTING)

                # Dispatch to appropriate Specialized Agent
                await self.broadcast_event(task_id, "ACTION_EXECUTING", {
                    "step_index": step_idx,
                    "agent": agent_name,
                    "action": action_name,
                    "params": params
                })

                action_result = await self._dispatch_agent_action(agent_name, action_name, params)
                executed_actions.append({
                    "step": step.get("title"),
                    "agent": agent_name,
                    "action": action_name,
                    "result": action_result
                })

                await self.broadcast_event(task_id, "ACTION_COMPLETED", {
                    "step_index": step_idx,
                    "agent": agent_name,
                    "action": action_name,
                    "success": action_result.get("success", True),
                    "summary": action_result.get("message") or action_result.get("summary") or "Action executed"
                })

            # 4. OUTCOME SYNTHESIS & REPORTING
            final_report = self._synthesize_final_outcome(prompt, plan, executed_actions, list(agents_engaged))
            spoken_summary = speech_service.filter_text_for_speech(final_report["summary"])
            final_report["spoken_summary"] = spoken_summary

            save_completion_report(task_id, final_report)
            update_task_status(task_id, TaskState.COMPLETED)
            await self.broadcast_event(task_id, "TASK_COMPLETED", final_report)
            log_audit(task_id, "TASK_COMPLETED", {"agents": list(agents_engaged)})

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} cancelled.")
            update_task_status(task_id, TaskState.CANCELLED)
            await self.broadcast_event(task_id, "TASK_CANCELLED", {"reason": "Execution cancelled"})
        except Exception as e:
            logger.error(f"Error in multi-agent pipeline for {task_id}: {e}", exc_info=True)
            err_msg = str(e)
            update_task_status(task_id, TaskState.FAILED, error_message=err_msg)
            await self.broadcast_event(task_id, "TASK_FAILED", {"error": err_msg})
        finally:
            self.active_tasks.pop(task_id, None)

    async def _dispatch_agent_action(self, agent_name: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches action directly to the instantiated Specialized Agent."""
        try:
            # 1. BROWSER AGENT
            if agent_name == "Browser Agent":
                if action == "navigate":
                    return await browser_agent.navigate(params.get("url", ""))
                elif action == "search":
                    return await browser_agent.search(params.get("query", ""), engine=params.get("engine", "google"))
                elif action == "scroll":
                    return await browser_agent.scroll(params.get("direction", "down"), amount=float(params.get("amount", 3.0)))
                elif action == "new_tab":
                    return await browser_agent.new_tab(params.get("url"))
                elif action == "close_tab":
                    return await browser_agent.close_tab()
                elif action == "switch_tab":
                    return await browser_agent.switch_tab(params.get("direction", "next"))
                elif action == "click":
                    return await browser_agent.click_at(int(params.get("x", 0)), int(params.get("y", 0)))
                elif action == "type":
                    return await browser_agent.type_text(params.get("text", ""), press_enter=params.get("press_enter", False))
                elif action == "extract_text":
                    return await browser_agent.extract_page_text()
                elif action in ["list_tabs", "get_tabs", "open_tabs"]:
                    return browser_agent.get_open_tabs(params.get("hwnd"))
                elif action in ["summarize_tabs", "summarize_open_tabs", "tab_summary"]:
                    return await browser_agent.summarize_open_tabs(params.get("hwnd"), max_tabs=int(params.get("max_tabs", 15)))

            # 2. FILE AGENT
            elif agent_name == "File Agent":
                if action == "search":
                    return file_agent.search_files(
                        query=params.get("query", ""),
                        search_dir=params.get("directory"),
                        search_content=params.get("search_content", False)
                    )
                elif action == "recent":
                    return file_agent.find_recent_documents(
                        folders=params.get("folders"),
                        limit=int(params.get("limit", 10))
                    )
                elif action == "open":
                    return file_agent.open_file(params.get("target") or params.get("path"))
                elif action == "read":
                    return file_agent.read_file(params.get("path", ""), max_lines=int(params.get("max_lines", 300)))
                elif action == "write":
                    return file_agent.write_file(params.get("path", ""), params.get("content", ""), overwrite=params.get("overwrite", False))
                elif action == "edit":
                    return file_agent.edit_file(params.get("path", ""), params.get("find_text", ""), params.get("replace_text", ""))
                elif action == "delete":
                    return file_agent.delete_file(params.get("path", ""), confirm=params.get("confirm", True))
                elif action == "list_dir":
                    return file_agent.list_dir(params.get("directory"))

            # 3. SYSTEM AGENT
            elif agent_name == "System Agent":
                if action == "telemetry":
                    return system_agent.get_system_telemetry()
                elif action == "list_processes":
                    return system_agent.list_processes(limit=int(params.get("limit", 20)), filter_name=params.get("filter"))
                elif action == "kill_process":
                    return system_agent.terminate_process(pid=params.get("pid"), name=params.get("name"), confirm=True)
                elif action == "launch_app":
                    return system_agent.launch_app(params.get("app", ""))
                elif action == "wait_for_window":
                    return system_agent.wait_for_window(params.get("title", ""), timeout=float(params.get("timeout", 10.0)))
                elif action in ["type_into_window", "type_text", "enter_text"]:
                    return system_agent.type_into_window(params.get("text", ""), title_substr=params.get("title"))
                elif action in ["verify_window_text", "verify_text"]:
                    return system_agent.verify_window_text(params.get("title", ""), params.get("expected_snippet", ""), timeout=float(params.get("timeout", 5.0)))
                elif action == "open_path":
                    return system_agent.open_in_default_app(params.get("path", ""))
                elif action == "run_command":
                    return system_agent.run_terminal_command(params.get("command", ""), shell=params.get("shell", "powershell"))
                elif action == "diagnose":
                    return system_agent.diagnose_system()

            # 4. VISION AGENT
            elif agent_name == "Vision Agent":
                if action == "analyze":
                    return await vision_agent.analyze_screen(prompt=params.get("prompt"))
                elif action == "locate":
                    return await vision_agent.locate_target_coordinates(params.get("target", ""))
                elif action == "explain_error":
                    return await vision_agent.explain_error_on_screen()

            return {"success": False, "error": f"Unknown agent '{agent_name}' or action '{action}'"}
        except Exception as e:
            logger.error(f"Error in {agent_name}.{action}: {e}")
            return {"success": False, "error": str(e)}

    async def _formulate_multi_agent_plan(
        self,
        task_id: str,
        prompt: str,
        workspace: str,
        chat_history: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Uses LLM Gateway or intelligent heuristic parsing to break the user's objective
        into actionable steps mapped to Browser, File, System, or Vision Agents.
        """
        p_lower = prompt.lower()
        from intent_router import intent_router

        # Deterministic Fast Heuristics for common single-agent intents
        # 0. Live Browser Tab Enumeration & Summarization
        if intent_router.is_summarize_tabs_request(prompt):
            return {
                "objective": prompt,
                "strategy": "Summarize Open Browser Tabs",
                "steps": [
                    {
                        "title": "Enumerate and summarize open browser tabs",
                        "agent": "Browser Agent",
                        "action": "summarize_tabs",
                        "params": {"max_tabs": 15},
                        "category": ActionCategory.BROWSER_ACTION,
                        "risk": RiskLevel.LOW,
                        "reason": "Inspect live tabs and summarize readable content."
                    }
                ]
            }

        if intent_router.is_list_tabs_request(prompt):
            return {
                "objective": prompt,
                "strategy": "List Open Browser Tabs",
                "steps": [
                    {
                        "title": "List all currently open browser tabs",
                        "agent": "Browser Agent",
                        "action": "list_tabs",
                        "params": {},
                        "category": ActionCategory.BROWSER_ACTION,
                        "risk": RiskLevel.LOW,
                        "reason": "Enumerate titles and URLs of open browser tabs."
                    }
                ]
            }

        # Check if query is file-related
        is_file_search = any(w in p_lower for w in [
            "file", "files", "folder", "directory", ".docx", ".pdf", ".txt", ".csv", ".xlsx", ".py", ".zip", ".pptx",
            "docx", "desktop", "downloads", "documents", "document"
        ])

        # A. Browser Search & Navigation (Exclude file searches)
        is_browser_trigger = any(w in p_lower for w in ["google", "browse to", "open website", "open url", "youtube"]) or (
            ("search for" in p_lower or p_lower.startswith("search ")) and not is_file_search
        )
        if is_browser_trigger:
            target_query = prompt
            for prefix in ["search for", "google for", "browse to", "google", "search"]:
                if prefix in p_lower:
                    target_query = prompt[p_lower.find(prefix) + len(prefix):].strip()
                    break
            return {
                "objective": prompt,
                "strategy": "Existing Browser Search & Navigation",
                "steps": [
                    {
                        "title": f"Search existing browser for '{target_query}'",
                        "agent": "Browser Agent",
                        "action": "search",
                        "params": {"query": target_query},
                        "category": ActionCategory.BROWSER_ACTION,
                        "risk": RiskLevel.LOW,
                        "reason": "Search requested topic in existing browser window."
                    }
                ]
            }

        # B. Hardware Telemetry & Specs
        if any(w in p_lower for w in ["cpu", "ram", "memory", "specs", "disk space", "hardware", "telemetry", "system info"]):
            return {
                "objective": prompt,
                "strategy": "System Telemetry Inspection",
                "steps": [
                    {
                        "title": "Read Windows hardware telemetry and resources",
                        "agent": "System Agent",
                        "action": "telemetry",
                        "params": {},
                        "category": ActionCategory.SAFE_READ,
                        "risk": RiskLevel.LOW,
                        "reason": "Inspect CPU, RAM, and Disk utilization."
                    }
                ]
            }

        # C. Process Inspection & Diagnostics
        if any(w in p_lower for w in ["processes", "running apps", "task manager", "diagnose", "slow"]):
            return {
                "objective": prompt,
                "strategy": "Process Inspection & Diagnostics",
                "steps": [
                    {
                        "title": "Inspect running processes and diagnose system health",
                        "agent": "System Agent",
                        "action": "diagnose",
                        "params": {},
                        "category": ActionCategory.SAFE_READ,
                        "risk": RiskLevel.LOW,
                        "reason": "Identify heavy processes and system alerts."
                    }
                ]
            }

        # D1. Recent Documents Search
        if intent_router.is_recent_documents_request(prompt):
            return {
                "objective": prompt,
                "strategy": "Recent Documents Discovery",
                "steps": [
                    {
                        "title": "Search recent documents across Desktop, Documents, and Downloads",
                        "agent": "File Agent",
                        "action": "recent",
                        "params": {"limit": 10},
                        "category": ActionCategory.SAFE_READ,
                        "risk": RiskLevel.LOW,
                        "reason": "Find recently saved documents on Windows system."
                    }
                ]
            }

        # D2. Open Document / File
        if intent_router.is_open_file_request(prompt, chat_history):
            open_tgt = intent_router.extract_open_file_target(prompt, chat_history) or prompt
            return {
                "objective": prompt,
                "strategy": f"Open Document ({open_tgt or 'Recent Document'})",
                "steps": [
                    {
                        "title": f"Open '{open_tgt}' in desktop session",
                        "agent": "File Agent",
                        "action": "open",
                        "params": {"target": open_tgt},
                        "category": ActionCategory.SAFE_READ,
                        "risk": RiskLevel.LOW,
                        "reason": f"Open {open_tgt} visibly on Windows desktop."
                    }
                ]
            }

        # D3. Launch application
        if p_lower.startswith("open ") or p_lower.startswith("launch "):
            app_target = prompt.split(" ", 1)[1].strip()
            return {
                "objective": prompt,
                "strategy": f"Launch Application ({app_target})",
                "steps": [
                    {
                        "title": f"Launch '{app_target}' on desktop",
                        "agent": "System Agent",
                        "action": "launch_app",
                        "params": {"app": app_target},
                        "category": ActionCategory.SYSTEM_LAUNCH,
                        "risk": RiskLevel.LOW,
                        "reason": f"Launch {app_target} in Windows environment."
                    }
                ]
            }

        # E. File Searching
        file_search_triggers = ["find file", "search file", "look for file", "search files", "search for a docx", "search for docx", "find docx", "search desktop", "search downloads"]
        if any(w in p_lower for w in file_search_triggers) or (("search" in p_lower or "find" in p_lower) and is_file_search):
            # Extract search directory if specified
            target_folder = None
            for f_name in ["desktop", "documents", "downloads", "pictures", "videos", "music"]:
                if f_name in p_lower:
                    target_folder = f"{f_name.capitalize()} folder"
                    break

            # Extract search query
            q = prompt
            for prefix in ["search for a", "search for", "find all", "find a", "find", "search", "look for"]:
                if prefix in p_lower:
                    idx = p_lower.find(prefix) + len(prefix)
                    q = prompt[idx:].strip()
                    break

            # Clean folder reference from query if present
            if target_folder:
                for f_term in ["on desktop", "in desktop", "desktop folder", "desktop", "in documents", "documents folder", "documents", "in downloads", "downloads folder", "downloads"]:
                    if f_term in q.lower():
                        q = re.sub(rf"\b{re.escape(f_term)}\b", "", q, flags=re.IGNORECASE).strip()

            q_clean = re.sub(r"^(?:a\s+|an\s+|the\s+)", "", q, flags=re.IGNORECASE).strip()
            q_clean = q_clean.rstrip(".")

            return {
                "objective": prompt,
                "strategy": "File Agent Search",
                "steps": [
                    {
                        "title": f"Search files matching '{q_clean}' in {target_folder or 'workspace'}",
                        "agent": "File Agent",
                        "action": "search",
                        "params": {
                            "query": q_clean or "docx",
                            "directory": target_folder,
                            "search_content": False
                        },
                        "category": ActionCategory.FILE_READ,
                        "risk": RiskLevel.LOW,
                        "reason": f"Search files matching {q_clean} across local directory."
                    }
                ]
            }

        # General LLM Planning for multi-agent workflows
        planning_prompt = (
            f"You are the Planner for a Windows Multi-Agent OS Assistant.\n"
            f"User Objective: {prompt}\n"
            f"Available Specialized Agents:\n"
            f"- Browser Agent: navigate, search, scroll, new_tab, close_tab, click, type, extract_text\n"
            f"- File Agent: search, recent, open, read, write, edit, delete, list_dir\n"
            f"- System Agent: telemetry, list_processes, kill_process, launch_app, open_path, run_command, diagnose\n"
            f"- Vision Agent: analyze, locate, explain_error\n\n"
            f"Generate a JSON execution plan:\n"
            f'{{"objective": "{prompt}", "strategy": "<brief strategy>", "steps": ['
            f'{{"title": "<step title>", "agent": "<Browser Agent|File Agent|System Agent|Vision Agent>", '
            f'"action": "<action_name>", "params": {{}}, "category": "<ActionCategory>", "risk": "LOW|MEDIUM|HIGH"}}'
            f']}}'
        )

        llm_res = await llm_gateway.chat_complete(
            messages=[{"role": "user", "content": planning_prompt}],
            temperature=0.2
        )

        if llm_res.get("success"):
            import re
            m = re.search(r"\{.*\}", llm_res.get("content", ""), re.DOTALL)
            if m:
                try:
                    import json
                    return json.loads(m.group(0))
                except Exception:
                    pass

        # Robust Fallback Plan
        return {
            "objective": prompt,
            "strategy": "System Execution & Inspection",
            "steps": [
                {
                    "title": "Inspect system context and telemetry",
                    "agent": "System Agent",
                    "action": "telemetry",
                    "params": {},
                    "category": ActionCategory.SAFE_READ,
                    "risk": RiskLevel.LOW
                }
            ]
        }

    def _synthesize_final_outcome(
        self,
        prompt: str,
        plan: Dict[str, Any],
        executed_actions: List[Dict[str, Any]],
        agents_engaged: List[str]
    ) -> Dict[str, Any]:
        """Synthesizes human-friendly summary of all executed operations."""
        summary_lines = [f"### 🎯 Task Complete: {prompt}\n"]
        for act in executed_actions:
            agent = act["agent"]
            res = act["result"]
            if res.get("success", True):
                msg = res.get("message") or res.get("summary")
                if not msg and "cpu" in res:
                    msg = f"CPU: {res['cpu']['percent']}%, RAM: {res['memory']['percent']}% used ({res['memory']['used_gb']} GB)"
                elif not msg and "documents" in res:
                    msg = f"Found {len(res.get('documents', []))} recent document(s)"
                elif not msg and res.get("opened"):
                    msg = f"Opened '{res.get('filename')}' on desktop"
                elif not msg and "summaries" in res:
                    sum_lines = [f"Summarized {res.get('total_tabs', len(res['summaries']))} tabs in {res.get('browser', 'browser')}:\n"]
                    for s in res.get("summaries", []):
                        sum_lines.append(f"  • **{s.get('index')}. {s.get('title')}** ({s.get('url')}):\n    {s.get('summary')}")
                    msg = "\n".join(sum_lines)
                elif not msg and "tabs" in res:
                    tab_lines = [f"Found {res.get('total_tabs', len(res['tabs']))} open tabs in {res.get('browser', 'browser')}:"]
                    for t in res.get("tabs", []):
                        act_str = " (Active)" if t.get("is_active") else ""
                        tab_lines.append(f"  • {t.get('index')}. {t.get('title')}{act_str} — {t.get('url')}")
                    msg = "\n".join(tab_lines)
                summary_lines.append(f"• **{agent}**: {msg or 'Action succeeded.'}")
            else:
                summary_lines.append(f"• **{agent}**: ⚠️ {res.get('error', 'Encountered issue.')}")

        full_summary = "\n".join(summary_lines)
        return {
            "objective": prompt,
            "final_status": TaskState.COMPLETED,
            "agents_engaged": agents_engaged,
            "actions_executed_count": len(executed_actions),
            "summary": full_summary,
            "details": executed_actions
        }

    async def _complete_task_with_response(self, task_id: str, prompt: str, content: str, agents_used: List[str]):
        """Helper for instant conversation or screen responses."""
        report = {
            "objective": prompt,
            "final_status": TaskState.COMPLETED,
            "agents_engaged": agents_used,
            "actions_executed_count": 1,
            "summary": content,
            "spoken_summary": speech_service.filter_text_for_speech(content),
            "details": []
        }
        save_completion_report(task_id, report)
        update_task_status(task_id, TaskState.COMPLETED)
        await self.broadcast_event(task_id, "TASK_COMPLETED", report)

orchestrator = MultiAgentOrchestrator()
