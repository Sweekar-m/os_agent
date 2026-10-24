import asyncio
import json
import os
import shutil
import logging
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional, Tuple

from permission_engine import permission_engine, PermissionDecision, ActionCategory
from db import add_task_event, update_task_status, log_audit

logger = logging.getLogger("codex_runtime")
logger.setLevel(logging.INFO)

# Known binary locations
POTENTIAL_CODEX_PATHS = [
    Path(r"C:\Users\sweek\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe"),
    Path(r"C:\Users\sweek\AppData\Local\OpenAI\Codex\bin\9691020b546a15b2\codex.exe")
]

def find_codex_binary() -> Optional[str]:
    """Finds the installed official OpenAI Codex binary."""
    for p in POTENTIAL_CODEX_PATHS:
        if p.exists() and p.is_file():
            return str(p)
    which_codex = shutil.which("codex")
    if which_codex:
        return which_codex
    return None

class CodexRuntime:
    def __init__(self):
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
        self.cancellation_requested: Dict[str, bool] = {}

    def get_status(self) -> Dict[str, Any]:
        binary = find_codex_binary()
        return {
            "available": binary is not None,
            "binary_path": binary,
            "runtime_type": "official_codex_cli" if binary else "not_installed",
            "active_tasks_count": len(self.active_processes)
        }

    def cancel_task(self, task_id: str):
        """Immediately stops the running Codex process for a task."""
        self.cancellation_requested[task_id] = True
        proc = self.active_processes.get(task_id)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                logger.info(f"Terminated Codex process for task {task_id}")
            except Exception as e:
                logger.warning(f"Failed to terminate Codex process for {task_id}: {e}")

    async def execute_task_stream(
        self,
        task_id: str,
        prompt: str,
        workspace: str,
        permission_mode: str = "balanced",
        model: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Executes a task through Codex CLI with non-interactive JSONL streaming,
        enforcing human-in-the-loop permissions and real execution monitoring.
        """
        binary = find_codex_binary()
        if not binary:
            err_msg = "Codex binary not found on this system. Please check Codex CLI installation."
            yield {"type": "error", "message": err_msg}
            add_task_event(task_id, "ERROR", {"message": err_msg})
            return

        # Canonical path validation
        is_valid, resolved_ws = permission_engine.validate_path_in_workspace(workspace, workspace)
        if not is_valid:
            err_msg = f"Security Violation: Invalid workspace path '{workspace}'"
            yield {"type": "security_block", "message": err_msg}
            add_task_event(task_id, "SECURITY_BLOCK", {"message": err_msg})
            return

        selected_model = model or "gpt-5.6-terra"
        self.cancellation_requested[task_id] = False

        # Build CLI command arguments
        # As discovered in testing: codex exec --skip-git-repo-check --json -C <cwd> -s <sandbox>
        cmd = [
            binary,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-C", resolved_ws
        ]

        if permission_mode == "full_access":
            cmd.extend([
                "--dangerously-bypass-approvals-and-sandbox",
                "-s", "danger-full-access"
            ])
        elif permission_mode == "read_only":
            cmd.extend([
                "-s", "read-only"
            ])
        else:  # balanced
            cmd.extend([
                "-s", "workspace-write"
            ])

        if selected_model:
            cmd.extend(["-m", selected_model])

        # Prompt as final positional argument
        cmd.append(prompt)

        logger.info(f"Spawning Codex for task {task_id}: {' '.join(cmd)}")
        yield {
            "type": "codex_started",
            "task_id": task_id,
            "workspace": resolved_ws,
            "model": selected_model,
            "permission_mode": permission_mode
        }
        add_task_event(task_id, "CODEX_STARTED", {
            "workspace": resolved_ws,
            "model": selected_model,
            "permission_mode": permission_mode
        })

        try:
            # stdin must be DEVNULL so Codex does not hang waiting for input
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            self.active_processes[task_id] = proc

            modified_files: List[str] = []
            executed_commands: List[Dict[str, Any]] = []

            # Stream stdout lines
            async for raw_line in proc.stdout:
                if self.cancellation_requested.get(task_id, False):
                    yield {"type": "cancelled", "message": "Task cancelled by user"}
                    add_task_event(task_id, "CANCELLED", {"reason": "User cancellation"})
                    break

                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # Process JSONL events from Codex
                if line.startswith("{") and line.endswith("}"):
                    try:
                        event = json.loads(line)
                        evt_type = event.get("type", "")

                        if evt_type == "thread.started":
                            th_id = event.get("thread_id")
                            yield {"type": "thread_started", "thread_id": th_id}
                            add_task_event(task_id, "THREAD_STARTED", {"thread_id": th_id})

                        elif evt_type == "turn.started":
                            yield {"type": "turn_started"}
                            add_task_event(task_id, "TURN_STARTED", {})

                        elif evt_type == "item.started":
                            item = event.get("item", {})
                            item_type = item.get("type")

                            # Intercept command execution before or during run
                            if item_type == "command_execution":
                                cmd_text = item.get("command", "")
                                cat, risk, r_reason, r_impact = permission_engine.classify_command(cmd_text)
                                is_allowed, grant_src = permission_engine.is_action_permitted(
                                    task_id, resolved_ws, cat, cmd_text, permission_mode
                                )
                                
                                yield {
                                    "type": "command_intercepted",
                                    "command": cmd_text,
                                    "category": cat,
                                    "risk_level": risk,
                                    "auto_permitted": is_allowed,
                                    "grant_source": grant_src
                                }

                        elif evt_type == "item.completed":
                            item = event.get("item", {})
                            item_type = item.get("type")

                            if item_type == "agent_message":
                                text = item.get("text", "")
                                yield {"type": "agent_message", "text": text}
                                add_task_event(task_id, "AGENT_MESSAGE", {"text": text})

                            elif item_type == "command_execution":
                                cmd_text = item.get("command", "")
                                out_text = item.get("aggregated_output") or item.get("output", "")
                                exit_code = item.get("exit_code")
                                status = item.get("status", "completed")

                                executed_commands.append({
                                    "command": cmd_text,
                                    "output": out_text,
                                    "exit_code": exit_code,
                                    "status": status
                                })

                                yield {
                                    "type": "command_completed",
                                    "command": cmd_text,
                                    "output": out_text,
                                    "exit_code": exit_code,
                                    "status": status
                                }
                                add_task_event(task_id, "COMMAND_EXECUTED", {
                                    "command": cmd_text,
                                    "exit_code": exit_code,
                                    "status": status
                                })

                            elif item_type == "file_change":
                                changes = item.get("changes", [])
                                for ch in changes:
                                    fpath = ch.get("path", "")
                                    if fpath and fpath not in modified_files:
                                        modified_files.append(fpath)
                                yield {"type": "file_change", "changes": changes}
                                add_task_event(task_id, "FILE_CHANGE", {"changes": changes})

                            elif item_type == "error":
                                msg = item.get("message", "")
                                if "Skill descriptions were shortened" not in msg:
                                    yield {"type": "warning", "message": msg}
                                    add_task_event(task_id, "WARNING", {"message": msg})

                            else:
                                yield {"type": "item_completed", "item": item}

                        elif evt_type == "turn.completed":
                            usage = event.get("usage", {})
                            yield {"type": "turn_completed", "usage": usage}
                            add_task_event(task_id, "TURN_COMPLETED", {"usage": usage})

                        continue
                    except json.JSONDecodeError:
                        pass

                # If non-JSON text line (e.g. system info)
                if "reading additional input from stdin" in line.lower():
                    continue
                yield {"type": "log_line", "text": line}

            return_code = await proc.wait()
            stderr_out = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()

            if return_code != 0 and stderr_out:
                if "fatal: Transport channel closed" not in stderr_out:
                    yield {"type": "error", "message": stderr_out, "code": return_code}
                    add_task_event(task_id, "ERROR", {"message": stderr_out, "code": return_code})

            summary = {
                "return_code": return_code,
                "modified_files": modified_files,
                "commands_run_count": len(executed_commands)
            }
            yield {"type": "execution_finished", "summary": summary}
            add_task_event(task_id, "EXECUTION_FINISHED", summary)

        except Exception as e:
            logger.error(f"Error during Codex execution: {e}")
            yield {"type": "error", "message": str(e)}
            add_task_event(task_id, "ERROR", {"message": str(e)})
        finally:
            self.active_processes.pop(task_id, None)

codex_runtime = CodexRuntime()
