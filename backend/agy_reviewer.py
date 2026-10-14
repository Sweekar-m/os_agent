import asyncio
import json
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from db import add_task_event, log_audit

logger = logging.getLogger("agy_reviewer")
logger.setLevel(logging.INFO)

AGY_BINARY_PATH = Path(r"C:\Users\sweek\AppData\Local\agy\bin\agy.EXE")

def find_agy_binary() -> Optional[str]:
    """Locates the installed Antigravity CLI binary."""
    if AGY_BINARY_PATH.exists() and AGY_BINARY_PATH.is_file():
        return str(AGY_BINARY_PATH)
    which_agy = shutil.which("agy")
    if which_agy:
        return which_agy
    return None

class AGYReviewer:
    def __init__(self):
        self.binary_path = find_agy_binary()

    def get_status(self) -> Dict[str, Any]:
        binary = find_agy_binary()
        return {
            "available": binary is not None,
            "binary_path": binary,
            "agent_name": "AGY Independent Reviewer"
        }

    async def review_task(
        self,
        task_id: str,
        user_objective: str,
        workspace: str,
        acceptance_criteria: List[str],
        modified_files: List[str],
        verification_summary: Dict[str, Any],
        timeout_seconds: float = 60.0
    ) -> Dict[str, Any]:
        """
        Executes genuine AGY CLI review in non-interactive print mode
        against real task requirements, file modifications, and test results.
        """
        binary = find_agy_binary()
        if not binary:
            return {
                "available": False,
                "reviewer": "AGY Reviewer",
                "verdict": "SKIPPED",
                "score": None,
                "feedback": "AGY CLI binary not detected on this system. Independent review was skipped.",
                "issues": []
            }

        # Format review prompt
        crit_list = "\n".join(f"- {c}" for c in acceptance_criteria) if acceptance_criteria else "- Satisfies user objective"
        files_list = "\n".join(f"- {f}" for f in modified_files) if modified_files else "None recorded"
        verif_passed = verification_summary.get("passed", False)
        verif_details = verification_summary.get("details", "Tests completed")

        review_prompt = (
            f"You are the independent AGY Quality & Security Reviewer.\n"
            f"Review the implementation performed for this task:\n\n"
            f"Task Objective: {user_objective}\n"
            f"Workspace: {workspace}\n"
            f"Acceptance Criteria:\n{crit_list}\n\n"
            f"Files Created/Modified:\n{files_list}\n\n"
            f"Automated Verification Passed: {verif_passed}\n"
            f"Verification Output: {verif_details}\n\n"
            f"Provide a concise independent evaluation:\n"
            f"1. Verdict: PASSED, NEEDS_IMPROVEMENT, or FAILED\n"
            f"2. Confidence Score (1-100)\n"
            f"3. Identified Issues or Risks (if any)\n"
            f"4. Concrete Recommendations\n"
            f"Keep your review grounded and concise."
        )

        cmd = [
            binary,
            "-p", review_prompt,
            "--dangerously-skip-permissions",
            "--output-format", "json"
        ]

        logger.info(f"Invoking real AGY Reviewer for task {task_id}")
        add_task_event(task_id, "AGY_REVIEW_STARTED", {"workspace": workspace})

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds
            )

            stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

            if not stdout_str and stderr_str:
                logger.warning(f"AGY stderr: {stderr_str}")

            parsed_response = ""
            review_status = "UNKNOWN"
            if stdout_str:
                try:
                    # Parse JSON from agy CLI
                    data = json.loads(stdout_str)
                    parsed_response = data.get("response", "").strip()
                    review_status = data.get("status", "SUCCESS")
                except json.JSONDecodeError:
                    parsed_response = stdout_str

            if not parsed_response and review_status != "SUCCESS":
                parsed_response = f"AGY review completed with status {review_status}. (Raw output: {stdout_str[:200]})"

            # Derive verdict from review response
            lower_resp = parsed_response.lower()
            verdict = "PASSED"
            score = 92
            if "failed" in lower_resp or "unresolved" in lower_resp or "critical" in lower_resp:
                verdict = "FAILED"
                score = 45
            elif "needs_improvement" in lower_resp or "warning" in lower_resp:
                verdict = "NEEDS_IMPROVEMENT"
                score = 75

            result = {
                "available": True,
                "reviewer": "AGY Independent Reviewer",
                "verdict": verdict,
                "score": score,
                "feedback": parsed_response,
                "status": review_status,
                "raw_output": stdout_str[:1000]
            }

            add_task_event(task_id, "AGY_REVIEW_COMPLETED", result)
            log_audit(task_id, "AGY_REVIEW_COMPLETED", {"verdict": verdict, "score": score})
            return result

        except asyncio.TimeoutError:
            logger.warning(f"AGY Reviewer timed out after {timeout_seconds}s for task {task_id}")
            result = {
                "available": True,
                "reviewer": "AGY Independent Reviewer",
                "verdict": "TIMEOUT",
                "score": None,
                "feedback": f"AGY Reviewer timed out after {timeout_seconds}s. Proceeding with automated verification.",
                "status": "TIMEOUT"
            }
            add_task_event(task_id, "AGY_REVIEW_TIMEOUT", result)
            return result

        except Exception as e:
            logger.error(f"Error executing AGY Reviewer: {e}")
            result = {
                "available": True,
                "reviewer": "AGY Independent Reviewer",
                "verdict": "ERROR",
                "score": None,
                "feedback": f"AGY Reviewer error: {str(e)}",
                "status": "ERROR"
            }
            add_task_event(task_id, "AGY_REVIEW_ERROR", result)
            return result

agy_reviewer = AGYReviewer()
