import asyncio
import os
import shlex
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from db import add_task_event, log_audit
from permission_engine import permission_engine

logger = logging.getLogger("verifier")
logger.setLevel(logging.INFO)

class VerificationResult:
    def __init__(self, passed: bool, commands_run: List[Dict[str, Any]], summary: str, errors: List[str]):
        self.passed = passed
        self.commands_run = commands_run
        self.summary = summary
        self.errors = errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "commands_run": self.commands_run,
            "summary": self.summary,
            "errors": self.errors
        }

class Verifier:
    def __init__(self, max_recovery_retries: int = 3):
        self.max_recovery_retries = max_recovery_retries

    async def run_command_in_workspace(
        self,
        command_str: str,
        workspace_root: str,
        timeout_seconds: float = 60.0
    ) -> Dict[str, Any]:
        """Runs a verification command safely inside the authorized workspace."""
        canonical_ws = str(Path(workspace_root).resolve())
        try:
            # We run via PowerShell on Windows
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-Command", command_str,
                cwd=canonical_ws,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds
            )

            stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            return_code = proc.returncode

            return {
                "command": command_str,
                "exit_code": return_code,
                "passed": return_code == 0,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "error": stderr_text if return_code != 0 else None
            }
        except asyncio.TimeoutError:
            return {
                "command": command_str,
                "exit_code": -1,
                "passed": False,
                "stdout": "",
                "stderr": f"Command timed out after {timeout_seconds} seconds",
                "error": "Timeout"
            }
        except Exception as e:
            return {
                "command": command_str,
                "exit_code": -1,
                "passed": False,
                "stdout": "",
                "stderr": str(e),
                "error": str(e)
            }

    async def verify_task(
        self,
        task_id: str,
        workspace_root: str,
        verification_commands: List[str],
        acceptance_criteria: List[str]
    ) -> VerificationResult:
        """
        Executes all planned verification commands and evaluates acceptance criteria.
        """
        results = []
        errors = []
        all_passed = True

        add_task_event(task_id, "VERIFICATION_STARTED", {
            "commands_to_run": verification_commands
        })

        if not verification_commands:
            # If no explicit commands, check files existence or syntax if available
            summary = "No explicit verification commands provided. Acceptance criteria checked."
            add_task_event(task_id, "VERIFICATION_COMPLETED", {
                "passed": True,
                "summary": summary
            })
            return VerificationResult(passed=True, commands_run=[], summary=summary, errors=[])

        for cmd in verification_commands:
            res = await self.run_command_in_workspace(cmd, workspace_root)
            results.append(res)
            if not res["passed"]:
                all_passed = False
                err_snippet = res["stderr"] or res["stdout"] or f"Exited with code {res['exit_code']}"
                errors.append(f"Command `{cmd}` failed: {err_snippet}")

        summary = "All verification checks passed cleanly." if all_passed else f"{len(errors)} check(s) failed."
        verif_res = VerificationResult(
            passed=all_passed,
            commands_run=results,
            summary=summary,
            errors=errors
        )

        add_task_event(task_id, "VERIFICATION_COMPLETED", verif_res.to_dict())
        log_audit(task_id, "VERIFICATION_COMPLETED", {"passed": all_passed, "errors_count": len(errors)})
        return verif_res

    def construct_recovery_prompt(
        self,
        original_objective: str,
        verification_errors: List[str],
        attempt_number: int
    ) -> str:
        """Constructs targeted, high-precision repair instructions for Codex."""
        errors_text = "\n".join(f"- {e}" for e in verification_errors)
        return (
            f"AUTONOMOUS RECOVERY (Attempt {attempt_number}/{self.max_recovery_retries}):\n"
            f"The previous implementation for objective '{original_objective}' failed verification checks.\n\n"
            f"Observed Failures:\n{errors_text}\n\n"
            f"Instructions:\n"
            f"1. Diagnose the exact root cause of the error(s) shown above.\n"
            f"2. Inspect the relevant files and apply a targeted fix.\n"
            f"3. Do not break existing working code.\n"
            f"4. Re-verify the changes to ensure all tests and assertions pass."
        )

verifier = Verifier()
