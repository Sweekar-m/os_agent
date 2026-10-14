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
    save_task_verification,
    save_task_review,
    save_completion_report,
    add_task_event,
    log_audit,
    recover_stale_tasks
)
from permission_engine import permission_engine, PermissionDecision, ActionCategory, RiskLevel
from planner import planner, TaskPlan
from codex_runtime import codex_runtime
from agy_reviewer import agy_reviewer
from verifier import verifier

logger = logging.getLogger("orchestrator")
logger.setLevel(logging.INFO)

class TaskState:
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    NEEDS_INPUT = "NEEDS_INPUT"

class Orchestrator:
    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        # Listeners for streaming SSE events: task_id -> List[asyncio.Queue]
        self.event_subscribers: Dict[str, List[asyncio.Queue]] = {}

    def startup(self):
        """Runs recovery on startup to handle any tasks interrupted by previous server termination."""
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
        """Dispatches event to DB and all active SSE subscriber queues for this task."""
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

    async def create_and_run_task(
        self,
        prompt: str,
        workspace: str,
        permission_mode: str = "balanced",
        model: str = "gpt-5.6-terra"
    ) -> str:
        """Entrypoint: creates a task and spawns the background execution state machine."""
        task_id = f"task-{uuid.uuid4().hex[:10]}"
        create_task(task_id, prompt, workspace, permission_mode, model)
        await self.broadcast_event(task_id, "TASK_CREATED", {
            "prompt": prompt,
            "workspace": workspace,
            "permission_mode": permission_mode,
            "model": model
        })

        if permission_mode == "full_access":
            permission_engine._full_access_grants[task_id] = True

        # Spawn asynchronous worker task
        worker = asyncio.create_task(
            self._run_task_pipeline(task_id, prompt, workspace, permission_mode, model)
        )
        self.active_tasks[task_id] = worker
        return task_id

    async def cancel_task(self, task_id: str) -> bool:
        """Cancels a running task, terminating its subprocess and cleaning up state."""
        update_task_status(task_id, TaskState.CANCELLED, error_message="Cancelled by user")
        codex_runtime.cancel_task(task_id)
        permission_engine.reset_session(task_id)

        worker = self.active_tasks.get(task_id)
        if worker and not worker.done():
            worker.cancel()

        await self.broadcast_event(task_id, "TASK_CANCELLED", {"reason": "User cancellation"})
        self.active_tasks.pop(task_id, None)
        return True

    # ----------------- Pipeline State Machine -----------------

    async def _run_task_pipeline(
        self,
        task_id: str,
        prompt: str,
        workspace: str,
        permission_mode: str,
        model: str
    ):
        try:
            # 1. PLANNING PHASE
            update_task_status(task_id, TaskState.PLANNING)
            await self.broadcast_event(task_id, "PHASE_CHANGED", {"phase": TaskState.PLANNING})

            task_plan = await planner.create_plan(
                task_id=task_id,
                user_prompt=prompt,
                workspace=workspace,
                permission_mode=permission_mode
            )
            save_task_plan(task_id, task_plan.dict())
            await self.broadcast_event(task_id, "PLAN_GENERATED", task_plan.dict())

            # 2. PERMISSION INTERCEPTION CHECK
            # If user already granted permission once via the dropdown (full_access), do NOT interrupt!
            needs_upfront_approval = False
            upfront_reason = ""
            upfront_impact = ""
            upfront_risk = RiskLevel.MEDIUM

            if permission_mode == "full_access":
                # User gave permission once upfront via the dropdown below the textbox
                needs_upfront_approval = False
            elif permission_mode == "ask_permission":
                needs_upfront_approval = True
                upfront_reason = "Executing plan in strict approval mode."
                upfront_impact = f"Allows Codex to execute {len(task_plan.implementation_steps)} planned step(s) in {Path(workspace).name}."
            else:
                for step in task_plan.implementation_steps:
                    if step.permission_required or step.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                        needs_upfront_approval = True
                        upfront_reason = f"Plan contains high-risk step: {step.title}"
                        upfront_impact = step.description
                        upfront_risk = step.risk_level
                        break

            # If permitted already by persistent project rules or full access, check that
            if needs_upfront_approval:
                is_permitted, grant_src = permission_engine.is_action_permitted(
                    task_id=task_id,
                    workspace_root=workspace,
                    category=ActionCategory.FILE_WRITE,
                    command=None,
                    permission_mode=permission_mode
                )
                if not is_permitted:
                    update_task_status(task_id, TaskState.AWAITING_APPROVAL)
                    await self.broadcast_event(task_id, "PHASE_CHANGED", {
                        "phase": TaskState.AWAITING_APPROVAL,
                        "reason": upfront_reason,
                        "impact": upfront_impact,
                        "risk_level": upfront_risk
                    })

                    decision, meta = await permission_engine.request_approval(
                        task_id=task_id,
                        workspace_root=workspace,
                        action_type="EXECUTE_TASK_PLAN",
                        command=None,
                        target_path=workspace,
                        reason=upfront_reason,
                        impact=upfront_impact,
                        risk_level=upfront_risk
                    )

                    await self.broadcast_event(task_id, "APPROVAL_RESOLVED", {
                        "decision": decision,
                        "meta": meta
                    })

                    if decision == PermissionDecision.DENY:
                        update_task_status(task_id, TaskState.BLOCKED, error_message="User denied permission to execute plan")
                        await self.broadcast_event(task_id, "TASK_BLOCKED", {"reason": "Denied by user"})
                        return
                    elif decision == PermissionDecision.CANCEL:
                        await self.cancel_task(task_id)
                        return

            # 3. REAL CODEX EXECUTION PHASE
            update_task_status(task_id, TaskState.EXECUTING)
            await self.broadcast_event(task_id, "PHASE_CHANGED", {"phase": TaskState.EXECUTING})

            modified_files: List[str] = []
            recovery_attempts = 0
            current_prompt = task_plan.codex_instructions

            while True:
                # Execute stream via Codex runtime
                async for event in codex_runtime.execute_task_stream(
                    task_id=task_id,
                    prompt=current_prompt,
                    workspace=workspace,
                    permission_mode=permission_mode,
                    model=model
                ):
                    await self.broadcast_event(task_id, f"CODEX_{event.get('type', 'EVENT').upper()}", event)

                    if event.get("type") == "file_change":
                        for ch in event.get("changes", []):
                            p = ch.get("path")
                            if p and p not in modified_files:
                                modified_files.append(p)

                    elif event.get("type") == "execution_finished":
                        summary = event.get("summary", {})
                        for f in summary.get("modified_files", []):
                            if f not in modified_files:
                                modified_files.append(f)

                # Check if task was cancelled during execution
                curr_task = get_task(task_id)
                if curr_task and curr_task["status"] == TaskState.CANCELLED:
                    return

                # 4. VERIFICATION PHASE
                update_task_status(task_id, TaskState.VERIFYING)
                await self.broadcast_event(task_id, "PHASE_CHANGED", {"phase": TaskState.VERIFYING})

                verif_res = await verifier.verify_task(
                    task_id=task_id,
                    workspace_root=workspace,
                    verification_commands=task_plan.verification_commands,
                    acceptance_criteria=task_plan.acceptance_criteria
                )
                save_task_verification(task_id, verif_res.to_dict())
                await self.broadcast_event(task_id, "VERIFICATION_RESULT", verif_res.to_dict())

                # If verification passed cleanly, break out of recovery loop
                if verif_res.passed:
                    break

                # 5. AUTONOMOUS RECOVERY LOOP
                recovery_attempts += 1
                if recovery_attempts > verifier.max_recovery_retries:
                    logger.warning(f"Task {task_id} exceeded max recovery retries ({verifier.max_recovery_retries})")
                    break

                update_task_status(task_id, TaskState.RECOVERING)
                await self.broadcast_event(task_id, "PHASE_CHANGED", {
                    "phase": TaskState.RECOVERING,
                    "attempt": recovery_attempts,
                    "max_attempts": verifier.max_recovery_retries
                })

                current_prompt = verifier.construct_recovery_prompt(
                    original_objective=prompt,
                    verification_errors=verif_res.errors,
                    attempt_number=recovery_attempts
                )
                await self.broadcast_event(task_id, "RECOVERY_TRIGGERED", {
                    "attempt": recovery_attempts,
                    "prompt": current_prompt
                })

            # 6. AGY INDEPENDENT REVIEW PHASE
            review_res = await agy_reviewer.review_task(
                task_id=task_id,
                user_objective=prompt,
                workspace=workspace,
                acceptance_criteria=task_plan.acceptance_criteria,
                modified_files=modified_files,
                verification_summary=verif_res.to_dict()
            )
            save_task_review(task_id, review_res)
            await self.broadcast_event(task_id, "AGY_REVIEW_RESULT", review_res)

            # 7. FINAL COMPLETION REPORT
            final_status = TaskState.COMPLETED if verif_res.passed else TaskState.FAILED
            completion_report = {
                "task_id": task_id,
                "objective": prompt,
                "workspace": workspace,
                "final_status": final_status,
                "verified_success": verif_res.passed,
                "modified_files": modified_files,
                "verification_checks_passed": verif_res.passed,
                "verification_summary": verif_res.summary,
                "verification_errors": verif_res.errors,
                "agy_review": review_res,
                "recovery_attempts": recovery_attempts,
                "completed_at": datetime.utcnow().isoformat()
            }
            save_completion_report(task_id, completion_report)
            update_task_status(task_id, final_status)
            await self.broadcast_event(task_id, "TASK_COMPLETED", completion_report)
            log_audit(task_id, "PIPELINE_COMPLETED", {"status": final_status, "verified": verif_res.passed})

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} pipeline cancelled")
            update_task_status(task_id, TaskState.CANCELLED)
        except Exception as e:
            logger.error(f"Task {task_id} pipeline error: {e}", exc_info=True)
            update_task_status(task_id, TaskState.FAILED, error_message=str(e))
            await self.broadcast_event(task_id, "TASK_ERROR", {"error": str(e)})
        finally:
            self.active_tasks.pop(task_id, None)

orchestrator = Orchestrator()
