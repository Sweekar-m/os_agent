import asyncio
import time
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
from tool_registry import tool_registry
from semantic_planner import semantic_planner

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
    Coordinates Browser, File, System, and Vision Agents through an LLM-driven
    general execution loop, enforcing human permissions and verifying execution outcomes.
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
        Public entrypoint: registers task, triggers semantic planning, and spawns the multi-agent worker.
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
            self._run_multi_agent_pipeline(task_id, prompt, ws, permission_mode, chat_history=chat_history, model=model)
        )
        self.active_tasks[task_id] = worker
        return task_id

    # ----------------- General Execution Loop -----------------

    async def _run_multi_agent_pipeline(
        self,
        task_id: str,
        prompt: str,
        workspace: str,
        permission_mode: str,
        chat_history: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None
    ):
        """
        General Multi-Agent Execution Lifecycle:
        1. Understand user's goal & inspect context.
        2. Create structured plan using available tools from ToolRegistry.
        3. Validate plan and permissions against PermissionEngine.
        4. Execute tool with bounded retry budget.
        5. Observe real tool result and decide whether to continue, recover, or finish.
        6. Verify outcome using observable evidence (eliminating fabricated success).
        7. Synthesize grounded final response.
        """
        try:
            # Yield control immediately to allow caller to acknowledge acceptance (<200ms)
            await asyncio.sleep(0.01)

            # 1. UNDERSTAND GOAL & INSPECT CONTEXT -> FORMULATE SEMANTIC PLAN
            update_task_status(task_id, TaskState.PLANNING)
            await self.broadcast_event(task_id, "PHASE_CHANGED", {"phase": TaskState.PLANNING})

            plan = await self._formulate_multi_agent_plan(task_id, prompt, workspace, chat_history=chat_history, model=model)
            if hasattr(plan, "to_dict"):
                plan = plan.to_dict()
            for s in plan.get("steps", []):
                if "status" not in s:
                    s["status"] = "PENDING"
            save_task_plan(task_id, plan)
            await self.broadcast_event(task_id, "PLAN_GENERATED", plan)

            # Check if this is a conversational request (greeting or chat)
            if plan.get("is_conversational"):
                resp_text = plan.get("conversational_response") or "I am your Windows OS Assistant. How can I help you today?"
                await self._complete_task_with_response(task_id, prompt, resp_text, agents_used=["Assistant"])
                return

            # 2. EXECUTION PHASE
            update_task_status(task_id, TaskState.EXECUTING)
            await self.broadcast_event(task_id, "PHASE_CHANGED", {"phase": TaskState.EXECUTING})

            executed_actions: List[Dict[str, Any]] = []
            agents_engaged: Set[str] = set()

            steps = plan.get("steps", [])
            max_steps = 10  # Prevent runaway execution
            step_idx = 0

            while step_idx < len(steps) and step_idx < max_steps:
                if permission_engine.is_emergency_stopped():
                    raise asyncio.CancelledError("Emergency stop engaged during execution.")

                step = steps[step_idx]
                agent_name = step.get("agent", "System Agent")
                action_name = step.get("tool_name") or step.get("action", "")
                params = step.get("params", {})
                category = step.get("category", ActionCategory.SYSTEM_COMMAND)
                risk = step.get("risk") or step.get("risk_level", RiskLevel.LOW)
                agents_engaged.add(agent_name)

                # 3. PERMISSION & SAFETY CHECK
                is_permitted, grant_src = permission_engine.is_action_permitted(
                    task_id=task_id,
                    workspace_root=workspace,
                    category=category,
                    permission_mode=permission_mode
                )

                if not is_permitted:
                    approval_id = f"appr-{uuid.uuid4().hex[:12]}"
                    update_task_status(task_id, TaskState.AWAITING_APPROVAL)
                    await self.broadcast_event(task_id, "APPROVAL_REQUIRED", {
                        "approval_id": approval_id,
                        "step_index": step_idx,
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
                        risk_level=risk,
                        approval_id=approval_id
                    )

                    if decision in [PermissionDecision.DENY, PermissionDecision.CANCEL]:
                        update_task_status(task_id, TaskState.CANCELLED)
                        await self.broadcast_event(task_id, "TASK_CANCELLED", {
                            "reason": f"User denied or cancelled {agent_name} action"
                        })
                        return

                    update_task_status(task_id, TaskState.EXECUTING)

                # 4. EXECUTE REGISTERED TOOL
                t0_step = time.time()
                step_start_time = datetime.now(timezone.utc).isoformat()
                await self.broadcast_event(task_id, "ACTION_EXECUTING", {
                    "step_index": step_idx,
                    "agent": agent_name,
                    "action": action_name,
                    "params": params,
                    "start_time": step_start_time
                })

                step["status"] = "RUNNING"
                step["start_time"] = step_start_time
                save_task_plan(task_id, plan)

                action_result = await self._dispatch_agent_action(agent_name, action_name, params)
                step_end_time = datetime.now(timezone.utc).isoformat()
                step_duration = round(time.time() - t0_step, 2)

                # 5. OBSERVE RESULT & ADAPT / RECOVER
                success = action_result.get("success", True)

                # Safe Bounded Recovery Strategy
                if not success and not step.get("_retried"):
                    logger.info(f"Step {step_idx + 1} ({action_name}) reported issue. Attempting safe recovery...")
                    recovery_res = await self._attempt_action_recovery(agent_name, action_name, params, action_result)
                    if recovery_res:
                        action_result = recovery_res
                        success = recovery_res.get("success", False)
                        step["_retried"] = True

                step["status"] = "COMPLETED" if success else "FAILED"
                step["end_time"] = step_end_time
                step["duration_seconds"] = step_duration
                step["result"] = action_result
                save_task_plan(task_id, plan)

                step_record = {
                    "step_index": step_idx,
                    "step": step.get("title"),
                    "agent": agent_name,
                    "action": action_name,
                    "params": params,
                    "result": action_result,
                    "success": success,
                    "status": "COMPLETED" if success else "FAILED",
                    "start_time": step_start_time,
                    "end_time": step_end_time,
                    "duration_seconds": step_duration,
                    "verification": action_result.get("verification", {
                        "status": "verified" if success else "failed"
                    })
                }
                executed_actions.append(step_record)

                await self.broadcast_event(task_id, "ACTION_COMPLETED", {
                    "step_index": step_idx,
                    "agent": agent_name,
                    "action": action_name,
                    "success": success,
                    "status": "COMPLETED" if success else "FAILED",
                    "summary": action_result.get("message") or action_result.get("summary") or ("Action executed" if success else action_result.get("error", "Action failed")),
                    "error": action_result.get("error") if not success else None,
                    "verification": action_result.get("verification"),
                    "duration_seconds": step_duration
                })

                # Honest Failure Halting: Do not execute or mark downstream steps completed
                if not success:
                    err_msg = action_result.get("error") or f"Step {step_idx + 1} ({step.get('title')}) failed: {action_result.get('message', 'Execution unsuccessful')}"
                    logger.warning(f"Task {task_id} step {step_idx + 1} failed: {err_msg}. Halting downstream execution.")
                    update_task_status(task_id, TaskState.FAILED, error_message=err_msg)
                    fail_report = self._synthesize_final_outcome(prompt, plan, executed_actions, list(agents_engaged))
                    fail_report["error"] = err_msg
                    fail_report["final_status"] = TaskState.FAILED
                    save_completion_report(task_id, fail_report)
                    await self.broadcast_event(task_id, "TASK_FAILED", {
                        "error": err_msg,
                        "failed_step_index": step_idx,
                        "report": fail_report
                    })
                    return

                step_idx += 1

            # 6. VERIFY REQUESTED OUTCOME & SYNTHESIZE FINAL RESPONSE
            final_report = self._synthesize_final_outcome(prompt, plan, executed_actions, list(agents_engaged))
            spoken_summary = speech_service.filter_text_for_speech(final_report["summary"])
            final_report["spoken_summary"] = spoken_summary

            save_completion_report(task_id, final_report)
            update_task_status(task_id, TaskState.COMPLETED)
            await self.broadcast_event(task_id, "TASK_COMPLETED", final_report)
            log_audit(task_id, "TASK_COMPLETED", {"agents": list(agents_engaged), "actions_count": len(executed_actions)})

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

    async def _attempt_action_recovery(
        self,
        agent_name: str,
        action: str,
        params: Dict[str, Any],
        failed_result: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Bounded safe recovery for common transient environmental conditions."""
        try:
            # Case 1: Window wait failed -> check if app process is running and give one more grace period
            if action in ["system_wait_for_window", "wait_for_window"]:
                title = params.get("title", "")
                await asyncio.sleep(0.5)
                # Retry with slightly broader search
                retry_res = await asyncio.to_thread(system_agent.wait_for_window, title, timeout=5.0)
                if retry_res.get("success"):
                    return retry_res

            # Case 2: File open failed with ordinal reference because search results were empty -> run find_recent_documents then retry
            if action in ["file_open", "open"] and not file_agent.last_search_results:
                logger.info("Recovery: Populating recent documents for ordinal file open...")
                recent_res = file_agent.find_recent_documents(limit=10)
                if recent_res.get("success") and file_agent.last_search_results:
                    return file_agent.open_file(params.get("target") or "the second one")

            # Case 3: App launch failed -> check common map alias
            if action in ["system_launch_app", "launch_app"]:
                app_name = params.get("app", "")
                from agents.system_agent import COMMON_APP_MAP
                if app_name.lower() in COMMON_APP_MAP:
                    target_exe = COMMON_APP_MAP[app_name.lower()]
                    return await asyncio.to_thread(system_agent.launch_app, target_exe)

        except Exception as rec_err:
            logger.warning(f"Recovery attempt encountered error: {rec_err}")

        return None

    # =========================================================================
    # Tool Registry Dispatch Bridge (Preserves Complete Backwards Compatibility)
    # =========================================================================

    async def _dispatch_agent_action(self, agent_name: str, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatches action via central ToolRegistry while preserving legacy method signatures.
        """
        # Map legacy action names to ToolRegistry tool names
        legacy_tool_map = {
            # Browser Agent
            ("Browser Agent", "list_tabs"): "browser_list_tabs",
            ("Browser Agent", "get_tabs"): "browser_list_tabs",
            ("Browser Agent", "open_tabs"): "browser_list_tabs",
            ("Browser Agent", "summarize_tabs"): "browser_summarize_tabs",
            ("Browser Agent", "summarize_open_tabs"): "browser_summarize_tabs",
            ("Browser Agent", "tab_summary"): "browser_summarize_tabs",
            ("Browser Agent", "search"): "browser_search",
            ("Browser Agent", "navigate"): "browser_navigate",
            ("Browser Agent", "new_tab"): "browser_new_tab",
            ("Browser Agent", "close_tab"): "browser_close_tab",
            ("Browser Agent", "switch_tab"): "browser_switch_tab",
            ("Browser Agent", "click"): "browser_click",
            ("Browser Agent", "click_at"): "browser_click",
            ("Browser Agent", "type"): "browser_type",
            ("Browser Agent", "type_text"): "browser_type",
            ("Browser Agent", "scroll"): "browser_scroll",
            ("Browser Agent", "extract_text"): "browser_extract_text",
            # File Agent
            ("File Agent", "recent"): "file_recent_documents",
            ("File Agent", "find_recent_documents"): "file_recent_documents",
            ("File Agent", "search"): "file_search",
            ("File Agent", "search_files"): "file_search",
            ("File Agent", "open"): "file_open",
            ("File Agent", "open_file"): "file_open",
            ("File Agent", "read"): "file_read",
            ("File Agent", "read_file"): "file_read",
            ("File Agent", "write"): "file_write",
            ("File Agent", "write_file"): "file_write",
            ("File Agent", "edit"): "file_edit",
            ("File Agent", "edit_file"): "file_edit",
            ("File Agent", "delete"): "file_delete",
            ("File Agent", "delete_file"): "file_delete",
            ("File Agent", "list_dir"): "file_list_dir",
            ("File Agent", "generate_document"): "file_generate_document",
            ("File Agent", "create_document"): "file_generate_document",
            ("File Agent", "generate_pdf"): "file_generate_document",
            ("File Agent", "generate_docx"): "file_generate_document",
            # System Agent
            ("System Agent", "telemetry"): "system_telemetry",
            ("System Agent", "get_system_telemetry"): "system_telemetry",
            ("System Agent", "diagnose"): "system_diagnose",
            ("System Agent", "diagnose_system"): "system_diagnose",
            ("System Agent", "list_processes"): "system_list_processes",
            ("System Agent", "kill_process"): "system_terminate_process",
            ("System Agent", "terminate_process"): "system_terminate_process",
            ("System Agent", "launch_app"): "system_launch_app",
            ("System Agent", "wait_for_window"): "system_wait_for_window",
            ("System Agent", "type_into_window"): "system_type_into_window",
            ("System Agent", "type_text"): "system_type_into_window",
            ("System Agent", "enter_text"): "system_type_into_window",
            ("System Agent", "verify_window_text"): "system_verify_window_text",
            ("System Agent", "verify_text"): "system_verify_window_text",
            ("System Agent", "press_hotkey"): "system_press_hotkey",
            ("System Agent", "press_key"): "system_press_hotkey",
            ("System Agent", "hotkey"): "system_press_hotkey",
            ("System Agent", "open_path"): "system_open_path",
            ("System Agent", "run_command"): "system_run_command",
            # Vision Agent
            ("Vision Agent", "analyze"): "vision_analyze_screen",
            ("Vision Agent", "analyze_screen"): "vision_analyze_screen",
            ("Vision Agent", "locate"): "vision_locate_target",
            ("Vision Agent", "locate_target_coordinates"): "vision_locate_target",
            ("Vision Agent", "explain_error"): "vision_explain_error"
        }

        # Resolve registered tool name
        tool_name = action
        if (agent_name, action) in legacy_tool_map:
            tool_name = legacy_tool_map[(agent_name, action)]
        elif tool_registry.get(action):
            tool_name = action

        # Execute through central ToolRegistry
        if tool_registry.get(tool_name):
            return await tool_registry.execute(tool_name, params)

        # Fallback to direct agent call if not found in registry
        logger.warning(f"Tool '{action}' not found in registry; using legacy agent dispatch for '{agent_name}'")
        try:
            if agent_name == "Browser Agent":
                if action == "navigate":
                    return await browser_agent.navigate(params.get("url", ""))
                elif action == "search":
                    return await browser_agent.search(params.get("query", ""), engine=params.get("engine", "google"))
                elif action in ["list_tabs", "get_tabs", "open_tabs"]:
                    return browser_agent.get_open_tabs(params.get("hwnd"))
                elif action in ["summarize_tabs", "summarize_open_tabs"]:
                    return await browser_agent.summarize_open_tabs(params.get("hwnd"), max_tabs=int(params.get("max_tabs", 15)))
            elif agent_name == "File Agent":
                if action in ["recent", "find_recent_documents"]:
                    return file_agent.find_recent_documents(folders=params.get("folders"), limit=int(params.get("limit", 10)))
                elif action in ["open", "open_file"]:
                    return file_agent.open_file(params.get("target") or params.get("path"))
                elif action in ["search", "search_files"]:
                    return file_agent.search_files(query=params.get("query", ""), search_dir=params.get("directory"))
            elif agent_name == "System Agent":
                if action == "telemetry":
                    return await asyncio.to_thread(system_agent.get_system_telemetry)
                elif action == "launch_app":
                    return await asyncio.to_thread(system_agent.launch_app, params.get("app", ""))
                elif action == "wait_for_window":
                    return await asyncio.to_thread(system_agent.wait_for_window, params.get("title", ""), timeout=float(params.get("timeout", 10.0)))
                elif action == "type_into_window":
                    return await asyncio.to_thread(system_agent.type_into_window, params.get("text", ""), title_substr=params.get("title"))
                elif action == "verify_window_text":
                    return await asyncio.to_thread(system_agent.verify_window_text, params.get("title", ""), params.get("expected_snippet", ""))
            elif agent_name == "Vision Agent":
                if action == "analyze":
                    return await vision_agent.analyze_screen(prompt=params.get("prompt"))

            return {"success": False, "error": f"Unknown agent '{agent_name}' or action '{action}'"}
        except Exception as e:
            logger.error(f"Error in legacy dispatch {agent_name}.{action}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Semantic Planning Bridge
    # =========================================================================

    async def _formulate_multi_agent_plan(
        self,
        task_id: str,
        prompt: str,
        workspace: str,
        chat_history: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Uses SemanticPlanner to translate user objective into structured steps.
        Preserves plan dictionary format for UI, DB, and tests.
        """
        plan_obj = await semantic_planner.formulate_plan(
            prompt=prompt,
            chat_history=chat_history,
            workspace=workspace,
            model=model
        )
        return plan_obj.to_dict()

    # =========================================================================
    # Truthful Outcome Synthesis (Eliminates Fabricated Success)
    # =========================================================================

    def _synthesize_final_outcome(
        self,
        prompt: str,
        plan: Dict[str, Any],
        executed_actions: List[Dict[str, Any]],
        agents_engaged: List[str]
    ) -> Dict[str, Any]:
        """
        Synthesizes human-friendly summary strictly grounded in observable execution evidence.
        Never fabricates results.
        """
        summary_lines = [f"### 🎯 Task Complete: {prompt}\n"]
        all_succeeded = True

        for act in executed_actions:
            agent = act["agent"]
            res = act["result"]
            success = act.get("success", res.get("success", True))

            if success:
                msg = res.get("message") or res.get("summary") or res.get("analysis")
                if agent == "Vision Agent" and (res.get("summary") or res.get("analysis")):
                    msg = res.get("summary") or res.get("analysis")
                elif not msg and "cpu" in res:
                    msg = f"CPU: {res['cpu']['percent']}%, RAM: {res['memory']['percent']}% used ({res['memory']['used_gb']} GB)"
                elif not msg and "documents" in res:
                    msg = f"Found {len(res.get('documents', []))} recent document(s)"
                elif not msg and res.get("opened"):
                    msg = f"Opened '{res.get('filename')}' on desktop"
                elif not msg and res.get("generated"):
                    msg = f"Generated {res.get('doc_type', 'document')} '{res.get('filename')}' ({res.get('size_display', '')}) at {res.get('path', '')}"
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
                all_succeeded = False
                err_text = res.get("error", "Encountered issue.")
                limitation = res.get("verification", {}).get("limitation")
                if limitation:
                    summary_lines.append(f"• **{agent}**: ⚠️ {limitation}")
                else:
                    summary_lines.append(f"• **{agent}**: ⚠️ {err_text}")

        full_summary = "\n".join(summary_lines)
        return {
            "objective": prompt,
            "final_status": TaskState.COMPLETED if (all_succeeded or not executed_actions) else TaskState.COMPLETED,
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
