import asyncio
import json
import os
import shutil
import logging
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional
import httpx

from config import settings

logger = logging.getLogger("codex_agent")
logger.setLevel(logging.INFO)

# Default Codex CLI paths
POTENTIAL_CODEX_PATHS = [
    Path(r"C:\Users\sweek\AppData\Local\Programs\OpenAI\Codex\bin\codex.EXE"),
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

class CodexAgent:
    def __init__(self):
        self.binary_path = find_codex_binary()
        self.default_workspace = str(Path(__file__).resolve().parent.parent)
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.pending_permissions: Dict[str, Dict[str, Any]] = {}

    def get_status(self) -> Dict[str, Any]:
        binary = find_codex_binary()
        self.binary_path = binary
        return {
            "available": binary is not None,
            "binary_path": binary,
            "default_workspace": self.default_workspace,
            "models": [
                {"id": "gpt-5.6-terra", "name": "5.6 Terra Light (Recommended)", "desc": "Default Codex Fast Agent"},
                {"id": "nvidia_nim/nvidia/nemotron-3-super-120b-a12b", "name": "Nemotron 3 Super", "desc": "High reasoning via NVIDIA NIM"},
                {"id": "meta/llama-3.3-70b-instruct", "name": "Meta Llama 3.3 70B", "desc": "Via NVIDIA NIM"}
            ]
        }

    async def guide_user_prompt(
        self,
        prompt: str,
        workspace: Optional[str] = None,
        permission_mode: str = "full_access"
    ) -> Dict[str, Any]:
        """
        Stage 1: The 'First AI' Orchestrator analyzes the user's natural prompt
        and formulates structured, expert guidance for Codex.
        """
        cwd = workspace or self.default_workspace
        
        # Check intent types
        prompt_lower = prompt.lower()
        is_browser = any(k in prompt_lower for k in ["browser", "chrome", "website", "linkedin", "scrape", "search online", "url", "http"])
        is_ppt = any(k in prompt_lower for k in ["ppt", "presentation", "powerpoint", "slides"])
        is_doc = any(k in prompt_lower for k in ["doc", "docx", "word", "document"])
        is_file_op = any(k in prompt_lower for k in ["delete", "remove", "rename", "move", "organize", "clean"])
        is_coding = any(k in prompt_lower for k in ["code", "script", "debug", "test", "install", "npm", "pip", "git", "build", "run"])

        plugins_needed = []
        if is_browser:
            plugins_needed.append("browser")
            plugins_needed.append("chrome")
        if is_ppt:
            plugins_needed.append("presentations")
        if is_doc:
            plugins_needed.append("documents")
        if is_coding:
            plugins_needed.append("code-review")

        # Determine risk and permission requirements
        requires_permission = False
        action_summary = "Execute task via Codex"
        
        if is_browser:
            action_summary = "Launch browser automation & navigate web pages"
            if permission_mode == "ask_permission":
                requires_permission = True
        elif is_file_op and any(k in prompt_lower for k in ["delete", "remove", "rm", "clean"]):
            action_summary = "File system deletion or cleanup operation"
            if permission_mode == "ask_permission":
                requires_permission = True
        elif "git push" in prompt_lower or "commit" in prompt_lower:
            action_summary = "Git write & repository update"
            if permission_mode == "ask_permission":
                requires_permission = True
        elif permission_mode == "ask_permission":
            # In strict ask_permission mode, any complex action requests approval
            requires_permission = True
            action_summary = f"Execute instructions in {Path(cwd).name}"

        # Build precise step-by-step guidance for Codex
        guidance_steps = []
        if is_browser:
            guidance_steps.append("1. Initialize browser/chrome plugin in non-headless or debugging session.")
            guidance_steps.append(f"2. Execute query: {prompt}")
            guidance_steps.append("3. Extract findings and return clean summary with screenshots/URLs.")
        elif is_ppt:
            guidance_steps.append(f"1. Target PowerPoint workspace: {cwd}")
            guidance_steps.append("2. Use presentations plugin or python-pptx to inspect/modify presentation.")
            guidance_steps.append("3. Validate slide layout and save cleanly.")
        elif is_coding:
            guidance_steps.append(f"1. Target project workspace: {cwd}")
            guidance_steps.append("2. Inspect files, plan edits, and run commands safely.")
            guidance_steps.append("3. Validate syntax and tests.")
        else:
            guidance_steps.append(f"1. Operate within directory: {cwd}")
            guidance_steps.append(f"2. Fulfill user directive: {prompt}")
            guidance_steps.append("3. Provide concise outcome summary.")

        # Construct optimized Codex prompt
        refined_codex_prompt = (
            f"You are executing a task for the user in working directory: {cwd}\n"
            f"User Goal: {prompt}\n"
            f"Guidance Instructions:\n"
            + "\n".join(guidance_steps) + "\n"
            f"Always summarize changes clearly upon completion."
        )

        return {
            "original_prompt": prompt,
            "refined_prompt": refined_codex_prompt,
            "guidance_steps": guidance_steps,
            "action_summary": action_summary,
            "plugins": plugins_needed,
            "workspace": cwd,
            "requires_permission": requires_permission,
            "permission_mode": permission_mode
        }

    async def run_codex_stream(
        self,
        prompt: str,
        workspace: Optional[str] = None,
        permission_mode: str = "full_access",
        model: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stage 2: Runs codex non-interactively with --json and streams progress
        back to the desktop UI.
        """
        binary = find_codex_binary()
        if not binary:
            yield {
                "type": "error",
                "message": "Codex binary not found on this system. Please check OpenAI Codex CLI installation."
            }
            return

        cwd = workspace or self.default_workspace
        selected_model = model or "gpt-5.6-terra"

        # Construct CLI flags
        cmd = [
            binary,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-C", cwd
        ]

        if permission_mode == "full_access":
            cmd.extend([
                "--dangerously-bypass-approvals-and-sandbox",
                "-s", "danger-full-access",
                "-a", "never"
            ])
        elif permission_mode == "read_only":
            cmd.extend([
                "-s", "read-only",
                "-a", "untrusted"
            ])
        else:  # ask_permission
            cmd.extend([
                "-s", "workspace-write",
                "-a", "on-request"
            ])

        if selected_model:
            cmd.extend(["-m", selected_model])

        # Append prompt as the final positional argument
        cmd.append(prompt)

        logger.info(f"Spawning Codex: {' '.join(cmd)}")

        yield {
            "type": "codex_starting",
            "model": selected_model,
            "workspace": cwd,
            "permission_mode": permission_mode
        }

        try:
            # We must pass stdin=asyncio.subprocess.DEVNULL so it doesn't block on stdin
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Stream stdout line by line
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # Handle JSONL events from --json
                if line.startswith("{") and line.endswith("}"):
                    try:
                        event = json.loads(line)
                        evt_type = event.get("type", "")
                        
                        if evt_type == "thread.started":
                            yield {"type": "thread_started", "thread_id": event.get("thread_id")}
                        
                        elif evt_type == "turn.started":
                            yield {"type": "turn_started"}
                        
                        elif evt_type == "item.completed":
                            item = event.get("item", {})
                            item_type = item.get("type")
                            if item_type == "agent_message":
                                yield {
                                    "type": "agent_message",
                                    "text": item.get("text", "")
                                }
                            elif item_type == "command_execution":
                                yield {
                                    "type": "command_executed",
                                    "command": item.get("command", ""),
                                    "output": item.get("output", "")
                                }
                            elif item_type == "error":
                                # Filter non-fatal skills warning
                                msg = item.get("message", "")
                                if "Skill descriptions were shortened" not in msg:
                                    yield {"type": "warning", "message": msg}
                            else:
                                yield {"type": "item_completed", "item": item}
                        
                        elif evt_type == "turn.completed":
                            yield {
                                "type": "turn_completed",
                                "usage": event.get("usage", {})
                            }
                        else:
                            yield {"type": "raw_event", "event": event}
                        continue
                    except json.JSONDecodeError:
                        pass

                # If non-JSON text output
                if "reading additional input from stdin" in line.lower():
                    continue
                yield {"type": "output_line", "text": line}

            # Wait for completion
            return_code = await proc.wait()
            stderr_output = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
            
            if return_code != 0 and stderr_output:
                # Omit benign auth/rmcp noise
                if "fatal: Transport channel closed" not in stderr_output:
                    yield {"type": "error", "message": stderr_output, "code": return_code}

            yield {"type": "done", "return_code": return_code}

        except Exception as e:
            logger.error(f"Error executing codex: {e}")
            yield {"type": "error", "message": str(e)}

codex_agent = CodexAgent()
