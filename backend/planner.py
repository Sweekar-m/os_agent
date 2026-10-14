import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from config import settings
from llm_client import nim_client

logger = logging.getLogger("planner")
logger.setLevel(logging.INFO)

class ImplementationStep(BaseModel):
    step_number: int
    title: str
    description: str
    relevant_files: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    permission_required: bool = False
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL

class TaskPlan(BaseModel):
    task_id: str
    user_objective: str
    workspace: str
    context_gathered: Dict[str, Any] = Field(default_factory=dict)
    implementation_steps: List[ImplementationStep] = Field(default_factory=list)
    relevant_files: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    permission_requirements: List[str] = Field(default_factory=list)
    risks_and_dependencies: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    verification_commands: List[str] = Field(default_factory=list)
    status: str = "PLANNING"
    codex_instructions: str = ""

def inspect_workspace_context(workspace_root: str) -> Dict[str, Any]:
    """
    Inspects the project context to provide real architectural grounding
    for the planner before generating steps.
    """
    ws = Path(workspace_root).resolve()
    context = {
        "workspace_path": str(ws),
        "exists": ws.exists(),
        "project_type": "generic",
        "key_manifests": [],
        "file_tree_sample": [],
        "git_detected": (ws / ".git").exists()
    }

    if not ws.exists():
        return context

    # Check for known manifests
    manifest_map = {
        "package.json": "node_javascript",
        "requirements.txt": "python",
        "pyproject.toml": "python",
        "Cargo.toml": "rust",
        "go.mod": "go",
        "pom.xml": "java_maven",
        "build.gradle": "java_gradle",
        "Makefile": "c_cpp_make"
    }

    for manifest, ptype in manifest_map.items():
        if (ws / manifest).exists():
            context["key_manifests"].append(manifest)
            if context["project_type"] == "generic":
                context["project_type"] = ptype

    # Sample top level directory files
    try:
        sample = []
        for item in sorted(ws.iterdir()):
            if item.name.startswith(".") and item.name != ".env":
                continue
            if item.name in ["node_modules", "venv", "__pycache__", "target", "dist", "build"]:
                sample.append(f"{item.name}/ (dir, skipped)")
                continue
            if item.is_dir():
                # list children up to 5
                sub_count = len(list(item.iterdir())) if item.is_dir() else 0
                sample.append(f"{item.name}/ ({sub_count} items)")
            else:
                sample.append(item.name)
        context["file_tree_sample"] = sample[:40]
    except Exception as e:
        context["file_tree_error"] = str(e)

    # If package.json exists, check dependencies and scripts
    pkg_json = ws / "package.json"
    if pkg_json.exists():
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg_data = json.load(f)
                context["scripts"] = list(pkg_data.get("scripts", {}).keys())
                context["dependencies"] = list(pkg_data.get("dependencies", {}).keys())[:15]
        except Exception:
            pass

    return context

class PlannerAI:
    def __init__(self):
        pass

    async def create_plan(
        self,
        task_id: str,
        user_prompt: str,
        workspace: str,
        permission_mode: str = "balanced"
    ) -> TaskPlan:
        """
        Translates user prompt + real workspace context into an actionable,
        structured TaskPlan validated with Pydantic.
        """
        ws_context = inspect_workspace_context(workspace)
        ptype = ws_context.get("project_type", "generic")
        manifests = ws_context.get("key_manifests", [])
        files_sample = ws_context.get("file_tree_sample", [])

        # Construct planning prompt for LLM
        system_prompt = (
            "You are the Primary Planning AI for an Autonomous OS Agent running real Codex and AGY.\n"
            "Your job is to translate the user's objective into a structured, highly actionable execution plan.\n"
            "You must return ONLY a valid JSON object matching the TaskPlan schema.\n"
            "Be precise, realistic, and do not invent fake files. Base your plan on the real workspace context provided."
        )

        user_content = f"""
Task ID: {task_id}
User Objective: {user_prompt}
Working Directory: {workspace}
Workspace Context:
- Project Type: {ptype}
- Key Manifests: {manifests}
- Top-level files: {files_sample}
- Git Repository Detected: {ws_context.get('git_detected')}
- Permission Mode: {permission_mode}

Generate a structured execution plan JSON with these exact fields:
{{
  "user_objective": "concise objective",
  "implementation_steps": [
    {{
      "step_number": 1,
      "title": "short title",
      "description": "precise action for Codex to take",
      "relevant_files": ["path/to/file"],
      "required_tools": ["file_write", "command", "etc"],
      "permission_required": true/false,
      "risk_level": "LOW|MEDIUM|HIGH"
    }}
  ],
  "relevant_files": ["list", "of", "target", "files"],
  "required_tools": ["shell_exec", "file_edit", "etc"],
  "permission_requirements": ["shell command execution", "file creation"],
  "risks_and_dependencies": ["potential dependency issues", "breaking changes"],
  "acceptance_criteria": ["criterion 1", "criterion 2"],
  "verification_commands": ["npm test", "pytest", "or build/verification script"],
  "codex_instructions": "precise instructions formatted for Codex execution"
}}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        raw_plan = {}
        try:
            resp = await nim_client.chat_complete(messages=messages, model="nvidia/llama-3.1-nemotron-70b-instruct")
            content = resp.get("content", "")
            # Clean markdown JSON fences
            cleaned = content.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
            
            raw_plan = json.loads(cleaned)
        except Exception as e:
            logger.warning(f"LLM planning fallback triggered: {e}")
            raw_plan = self._generate_fallback_plan(user_prompt, workspace, ws_context)

        # Assemble and validate Pydantic TaskPlan
        steps = []
        for idx, s in enumerate(raw_plan.get("implementation_steps", [])):
            steps.append(ImplementationStep(
                step_number=s.get("step_number", idx + 1),
                title=s.get("title", f"Step {idx + 1}"),
                description=s.get("description", ""),
                relevant_files=s.get("relevant_files", []),
                required_tools=s.get("required_tools", []),
                permission_required=bool(s.get("permission_required", False)),
                risk_level=s.get("risk_level", "LOW")
            ))

        # Default verification commands if not set
        verif_cmds = raw_plan.get("verification_commands", [])
        if not verif_cmds:
            if "package.json" in manifests and "test" in ws_context.get("scripts", []):
                verif_cmds = ["npm test"]
            elif "requirements.txt" in manifests or "pyproject.toml" in manifests:
                verif_cmds = ["python -m pytest"]

        # Default acceptance criteria if not set
        acc_criteria = raw_plan.get("acceptance_criteria", [])
        if not acc_criteria:
            acc_criteria = [
                f"Fulfill request: {user_prompt}",
                "All created or modified files pass syntax and build validation",
                "Verification commands exit cleanly with code 0"
            ]

        # Construct high-fidelity instructions for Codex
        codex_instr = raw_plan.get("codex_instructions", "")
        if not codex_instr:
            step_bullets = "\n".join(f"{s.step_number}. {s.title}: {s.description}" for s in steps)
            verif_bullets = "\n".join(f"- `{vc}`" for vc in verif_cmds) if verif_cmds else "- Inspect file syntax and output"
            codex_instr = (
                f"Working Directory: {workspace}\n"
                f"Objective: {user_prompt}\n\n"
                f"Execution Steps:\n{step_bullets}\n\n"
                f"Verification Instructions:\n{verif_bullets}\n\n"
                f"Perform the work completely and verify changes before reporting results."
            )

        task_plan = TaskPlan(
            task_id=task_id,
            user_objective=user_prompt,
            workspace=workspace,
            context_gathered=ws_context,
            implementation_steps=steps,
            relevant_files=raw_plan.get("relevant_files", []),
            required_tools=raw_plan.get("required_tools", ["file_editor", "terminal"]),
            permission_requirements=raw_plan.get("permission_requirements", ["workspace_write"]),
            risks_and_dependencies=raw_plan.get("risks_and_dependencies", []),
            acceptance_criteria=acc_criteria,
            verification_commands=verif_cmds,
            status="READY_FOR_EXECUTION",
            codex_instructions=codex_instr
        )

        return task_plan

    def _generate_fallback_plan(self, prompt: str, workspace: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Provides deterministic, structured fallback plan if LLM is unavailable."""
        ptype = context.get("project_type", "generic")
        steps = [
            {
                "step_number": 1,
                "title": "Analyze and Inspect Workspace",
                "description": f"Examine files in {workspace} to determine dependencies and target components.",
                "relevant_files": context.get("key_manifests", []),
                "required_tools": ["file_reader"],
                "permission_required": False,
                "risk_level": "LOW"
            },
            {
                "step_number": 2,
                "title": "Implement Changes",
                "description": f"Perform required code implementation to satisfy: {prompt}",
                "relevant_files": [],
                "required_tools": ["file_editor", "terminal"],
                "permission_required": True,
                "risk_level": "MEDIUM"
            },
            {
                "step_number": 3,
                "title": "Verify Implementation",
                "description": "Run project tests or check file integrity to ensure zero syntax or runtime errors.",
                "relevant_files": [],
                "required_tools": ["terminal"],
                "permission_required": False,
                "risk_level": "LOW"
            }
        ]

        verif_cmds = []
        if "package.json" in context.get("key_manifests", []):
            verif_cmds = ["npm test"]
        elif ptype == "python":
            verif_cmds = ["python -m unittest"]

        return {
            "user_objective": prompt,
            "implementation_steps": steps,
            "relevant_files": [],
            "required_tools": ["file_editor", "terminal"],
            "permission_requirements": ["workspace_write", "shell_execution"],
            "risks_and_dependencies": ["Potential compilation or syntax errors"],
            "acceptance_criteria": [f"Fulfill objective: {prompt}", "All changes pass validation"],
            "verification_commands": verif_cmds,
            "codex_instructions": f"Objective: {prompt}\nWorking Dir: {workspace}\nExecute all steps safely and verify."
        }

planner = PlannerAI()
