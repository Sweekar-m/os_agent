import asyncio
import os
import re
import shlex
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

from db import (
    create_approval_request,
    resolve_approval_request,
    get_approval,
    create_permission_rule,
    revoke_permission_rule,
    list_permission_rules,
    log_audit
)

logger = logging.getLogger("permission_engine")
logger.setLevel(logging.INFO)

# Command & Agent Action categories
class ActionCategory:
    SAFE_READ = "SAFE_READ"
    BUILD_TEST = "BUILD_TEST"
    PACKAGE_INSTALL = "PACKAGE_INSTALL"
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    FILE_DELETE = "FILE_DELETE"
    BROWSER_ACTION = "BROWSER_ACTION"
    SYSTEM_LAUNCH = "SYSTEM_LAUNCH"
    SYSTEM_KILL = "SYSTEM_KILL"
    SYSTEM_COMMAND = "SYSTEM_COMMAND"
    VISION_CAPTURE = "VISION_CAPTURE"
    GIT_WRITE = "GIT_WRITE"
    NETWORK_CALL = "NETWORK_CALL"
    HIGH_RISK_SYSTEM = "HIGH_RISK_SYSTEM"

class RiskLevel:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

# Safe read-only commands that cannot mutate files or system
SAFE_COMMAND_PREFIXES = {
    "dir", "ls", "cat", "type", "echo", "pwd", "cd", "where", "which",
    "git status", "git log", "git diff", "git branch", "git show", "git rev-parse",
    "python --version", "python -V", "node --version", "node -v", "npm --version", "npm -v",
    "cargo --version", "git --version"
}

# Critical high risk commands that require strict approval
HIGH_RISK_PATTERNS = [
    r"\bformat\b",
    r"\brmdir\s+/[sS]",
    r"\bdel\s+/[fFsS]",
    r"\brm\s+-r[fF]",
    r"\breg\s+(add|delete)",
    r"\bpowershell.*-enc",
    r"\bInvoke-Expression\b",
    r"\biex\b",
    r"\bnet\s+user\b",
    r"\bcurl.*\|.*(bash|sh|powershell)",
    r"\bshutdown\b",
    r"\btaskkill\s+/[fF]",
    r"\bgit\s+reset\s+--hard",
    r"\bgit\s+clean\s+-[fF]"
]

class PermissionDecision:
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_PROJECT = "allow_project"
    ALLOW_FULL_ACCESS = "allow_full_access"
    DENY = "deny"
    CANCEL = "cancel"

class PermissionEngine:
    def __init__(self):
        # In-memory session grants per task_id: task_id -> Set[ActionCategory]
        self._session_grants: Dict[str, Set[str]] = {}
        # In-memory full access grants per task_id: task_id -> bool
        self._full_access_grants: Dict[str, bool] = {}
        # Pending approval futures: approval_id -> asyncio.Future
        self._pending_futures: Dict[str, asyncio.Future] = {}
        # Emergency stop flag: halts all execution immediately
        self._emergency_stopped: bool = False

    def trigger_emergency_stop(self) -> Dict[str, Any]:
        """Engages system-wide emergency stop, blocking all in-flight and future actions."""
        self._emergency_stopped = True
        logger.warning("EMERGENCY STOP TRIGGERED: All actions blocked.")
        # Cancel all pending approval futures
        for appr_id, fut in list(self._pending_futures.items()):
            if not fut.done():
                fut.set_result((PermissionDecision.CANCEL, {"reason": "Emergency stop triggered"}))
        return {
            "emergency_stopped": True,
            "message": "Emergency stop activated. All active operations halted and future actions blocked."
        }

    def clear_emergency_stop(self) -> Dict[str, Any]:
        """Disengages system-wide emergency stop, returning to normal permission governance."""
        self._emergency_stopped = False
        logger.info("Emergency stop cleared.")
        return {
            "emergency_stopped": False,
            "message": "Emergency stop cleared. Normal permission governance restored."
        }

    def is_emergency_stopped(self) -> bool:
        return self._emergency_stopped

    def reset_session(self, task_id: str):
        self._session_grants.pop(task_id, None)
        self._full_access_grants.pop(task_id, None)

    # ----------------- Path & Boundary Validation -----------------

    def validate_path_in_workspace(self, target_path: str, workspace_root: str) -> Tuple[bool, str]:
        """
        Validates that target_path resolves strictly within workspace_root.
        Prevents path traversal, symlink jumping, and unauthorized root access.
        """
        try:
            ws_canonical = Path(workspace_root).resolve()
            target_p = Path(target_path)
            
            # If relative, resolve relative to workspace_root
            if not target_p.is_absolute():
                target_canonical = (ws_canonical / target_p).resolve()
            else:
                target_canonical = target_p.resolve()

            # Check boundary
            try:
                target_canonical.relative_to(ws_canonical)
                return True, str(target_canonical)
            except ValueError:
                return False, f"Path '{target_path}' resolves outside authorized workspace '{workspace_root}'"
        except Exception as e:
            return False, f"Invalid path validation: {str(e)}"

    # ----------------- Command Classification -----------------

    def classify_command(self, cmd_str: str) -> Tuple[str, str, str, str]:
        """
        Classifies a shell command into:
        (category, risk_level, reason, impact)
        """
        cleaned = cmd_str.strip()
        cleaned_lower = cleaned.lower()

        # Check critical high risk patterns
        for pattern in HIGH_RISK_PATTERNS:
            if re.search(pattern, cleaned_lower):
                return (
                    ActionCategory.HIGH_RISK_SYSTEM,
                    RiskLevel.CRITICAL,
                    "Command contains dangerous or potentially destructive system operations.",
                    "May delete files permanently, alter system registry, or bypass security controls."
                )

        # Check safe read commands
        for safe in SAFE_COMMAND_PREFIXES:
            if cleaned_lower == safe or cleaned_lower.startswith(safe + " ") or cleaned_lower.startswith(safe + "|"):
                return (
                    ActionCategory.SAFE_READ,
                    RiskLevel.LOW,
                    "Read-only inspection command.",
                    "Observes project metadata or environment state without modifying files."
                )

        # Package managers
        if any(cleaned_lower.startswith(p) for p in ["npm install", "npm i ", "pnpm install", "yarn add", "pip install", "cargo add"]):
            return (
                ActionCategory.PACKAGE_INSTALL,
                RiskLevel.MEDIUM,
                "Package installation downloads external dependencies.",
                "Executes package lifecycle scripts and alters node_modules or python virtualenv."
            )

        # Build & test commands
        if any(cleaned_lower.startswith(p) for p in [
            "npm test", "npm run test", "npx jest", "npx vitest", "pytest", "python -m unittest",
            "cargo test", "npm run build", "npx tsc", "cargo build"
        ]):
            return (
                ActionCategory.BUILD_TEST,
                RiskLevel.LOW,
                "Runs project build, test suite, or type check.",
                "Compiles code or executes test assertions within the workspace."
            )

        # Git write commands
        if any(cleaned_lower.startswith(p) for p in ["git commit", "git push", "git merge", "git rebase"]):
            return (
                ActionCategory.GIT_WRITE,
                RiskLevel.MEDIUM,
                "Modifies git history or interacts with remote repository.",
                "Creates new commits or pushes changes."
            )

        # File deletion
        if any(k in cleaned_lower for k in ["rm ", "del ", "remove-item", "erase"]):
            return (
                ActionCategory.FILE_DELETE,
                RiskLevel.HIGH,
                "Deletes files or directories within workspace.",
                "Removes files which may lead to uncommitted data loss."
            )

        # General file write or build script
        return (
            ActionCategory.FILE_WRITE,
            RiskLevel.MEDIUM,
            "Executes custom tool, script, or file write operation.",
            "May modify workspace files or run local development processes."
        )

    # ----------------- Permission Evaluation -----------------

    def is_action_permitted(
        self,
        task_id: str,
        workspace_root: str,
        category: str,
        command: Optional[str] = None,
        permission_mode: str = "balanced"
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluates whether an action is authorized under current grants.
        Returns (is_permitted, grant_source).
        """
        if self._emergency_stopped:
            return False, "emergency_stop_active"

        # Critical destructive actions ALWAYS require individual approval
        if category in [ActionCategory.HIGH_RISK_SYSTEM, ActionCategory.FILE_DELETE, ActionCategory.SYSTEM_KILL]:
            # Only allow if explicitly granted for this session
            task_sessions = self._session_grants.get(task_id, set())
            if category in task_sessions:
                return True, f"session_grant({category})"
            return False, None

        # Autonomous / Full Access grant for this task
        if self._full_access_grants.get(task_id, False) or permission_mode == "full_access":
            return True, "task_full_access"

        # Safe read and observation operations are allowed by default in balanced mode
        safe_defaults = [ActionCategory.SAFE_READ, ActionCategory.FILE_READ, ActionCategory.VISION_CAPTURE]
        if category in safe_defaults and permission_mode in ["balanced", "full_access"]:
            return True, "safe_observation_policy"

        # Non-destructive browser and app launch operations allowed in balanced mode
        if category in [ActionCategory.BROWSER_ACTION, ActionCategory.SYSTEM_LAUNCH] and permission_mode == "balanced":
            return True, "standard_os_interaction_policy"

        # Check active session grants
        task_sessions = self._session_grants.get(task_id, set())
        if category in task_sessions:
            return True, f"session_grant({category})"

        # Check persistent project rules in DB
        canonical_ws = str(Path(workspace_root).resolve())
        rules = list_permission_rules(project_path=canonical_ws, include_revoked=False)
        for r in rules:
            if r["scope"] == "FULL_ACCESS" and category != ActionCategory.HIGH_RISK_SYSTEM:
                return True, "project_persistent_full_access"
            if r["action_category"] == category:
                # If command pattern matches
                pat = r.get("pattern", "*")
                if pat == "*" or not command:
                    return True, f"project_rule({r['id']})"
                try:
                    if re.search(pat, command, re.IGNORECASE):
                        return True, f"project_rule({r['id']})"
                except Exception:
                    pass

        # In strict "ask_permission" mode, even tests or writes require approval
        return False, None

    # ----------------- Request & Resolve Approvals -----------------

    async def request_approval(
        self,
        task_id: str,
        workspace_root: str,
        action_type: str,
        command: Optional[str] = None,
        target_path: Optional[str] = None,
        reason: Optional[str] = None,
        impact: Optional[str] = None,
        risk_level: Optional[str] = None,
        timeout_seconds: float = 300.0
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Creates a pending approval request in DB, publishes event,
        and pauses execution until user responds.
        Returns (decision, decision_meta).
        """
        if self._emergency_stopped:
            return PermissionDecision.CANCEL, {"reason": "Emergency stop is active"}

        approval_id = f"appr-{uuid.uuid4().hex[:12]}"
        
        # If classification not provided, compute it
        if command and not (reason and impact and risk_level):
            cat, r_level, r_reason, r_impact = self.classify_command(command)
            reason = reason or r_reason
            impact = impact or r_impact
            risk_level = risk_level or r_level
        else:
            risk_level = risk_level or RiskLevel.MEDIUM
            reason = reason or "Action requires human approval."
            impact = impact or "Modifies workspace files or executes shell environment."

        # Register in DB
        appr_record = create_approval_request(
            approval_id=approval_id,
            task_id=task_id,
            action_type=action_type,
            command=command,
            target_path=target_path,
            working_dir=workspace_root,
            reason=reason,
            impact=impact,
            risk_level=risk_level
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_futures[approval_id] = future

        logger.info(f"[Approval Required] ID={approval_id} Task={task_id} Action={action_type} Cmd='{command}'")

        try:
            # Wait for user decision with timeout
            decision, meta = await asyncio.wait_for(future, timeout=timeout_seconds)
            return decision, meta
        except asyncio.TimeoutError:
            logger.warning(f"[Approval Timeout] ID={approval_id} expired after {timeout_seconds}s")
            self.submit_decision(approval_id, PermissionDecision.DENY, {"reason": "Approval request timed out"})
            return PermissionDecision.DENY, {"reason": "Timeout"}
        finally:
            self._pending_futures.pop(approval_id, None)

    def submit_decision(
        self,
        approval_id: str,
        decision: str,
        meta: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Processes human response to an approval request:
        1. allow_once
        2. allow_session
        3. allow_project
        4. allow_full_access
        5. deny
        6. cancel
        """
        meta = meta or {}
        appr = get_approval(approval_id)
        if not appr:
            logger.error(f"Approval ID {approval_id} not found")
            return False

        task_id = appr["task_id"]
        working_dir = appr["working_dir"]
        canonical_ws = str(Path(working_dir).resolve())
        cmd = appr.get("command")
        action_type = appr.get("action_type")
        category = meta.get("category")
        if not category and cmd:
            category, _, _, _ = self.classify_command(cmd)
        category = category or ActionCategory.FILE_WRITE

        # Apply grants based on decision
        if decision == PermissionDecision.ALLOW_ONCE:
            logger.info(f"Granted single-use permission for {approval_id}")

        elif decision == PermissionDecision.ALLOW_SESSION:
            if task_id not in self._session_grants:
                self._session_grants[task_id] = set()
            self._session_grants[task_id].add(category)
            logger.info(f"Granted session permission for task {task_id}, category {category}")

        elif decision == PermissionDecision.ALLOW_PROJECT:
            rule_id = f"rule-{uuid.uuid4().hex[:8]}"
            pattern = meta.get("pattern", "*")
            create_permission_rule(
                rule_id=rule_id,
                project_path=canonical_ws,
                action_category=category,
                pattern=pattern,
                scope="PROJECT",
                description=f"Auto-granted for {category} by user approval on {appr.get('id')}"
            )
            logger.info(f"Saved persistent project rule {rule_id} for {canonical_ws} ({category})")

        elif decision == PermissionDecision.ALLOW_FULL_ACCESS:
            self._full_access_grants[task_id] = True
            if meta.get("persist_project", False):
                rule_id = f"rule-full-{uuid.uuid4().hex[:8]}"
                create_permission_rule(
                    rule_id=rule_id,
                    project_path=canonical_ws,
                    action_category="ALL",
                    pattern="*",
                    scope="FULL_ACCESS",
                    description=f"Full access project grant for {canonical_ws}"
                )
            logger.info(f"Granted full access for task {task_id}")

        elif decision == PermissionDecision.DENY:
            logger.info(f"Denied action {approval_id}")

        elif decision == PermissionDecision.CANCEL:
            logger.info(f"Cancelled task {task_id} via approval {approval_id}")

        # Update DB
        resolve_approval_request(approval_id, decision, meta)

        # Resolve waiting future
        fut = self._pending_futures.get(approval_id)
        if fut and not fut.done():
            fut.set_result((decision, meta))

        return True

    # ----------------- Rule Management & Revocation -----------------

    def revoke_rule(self, rule_id: str) -> bool:
        success = revoke_permission_rule(rule_id)
        logger.info(f"Revoked permission rule {rule_id}: {success}")
        return success

    def revoke_session_grants(self, task_id: str):
        self._session_grants.pop(task_id, None)
        self._full_access_grants.pop(task_id, None)
        logger.info(f"Revoked all session grants for {task_id}")

    def get_active_grants(self, task_id: str, workspace_root: str) -> Dict[str, Any]:
        canonical_ws = str(Path(workspace_root).resolve())
        return {
            "task_id": task_id,
            "session_categories": list(self._session_grants.get(task_id, set())),
            "full_access_active": self._full_access_grants.get(task_id, False),
            "project_persistent_rules": list_permission_rules(project_path=canonical_ws, include_revoked=False)
        }

permission_engine = PermissionEngine()
