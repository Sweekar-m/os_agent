import sys
import os
import asyncio
import unittest
import shutil
import uuid
from pathlib import Path

# Add backend directory to path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import db
from permission_engine import (
    permission_engine,
    PermissionDecision,
    ActionCategory,
    RiskLevel
)
from planner import planner, inspect_workspace_context
from verifier import verifier
from codex_runtime import codex_runtime, find_codex_binary
from agy_reviewer import agy_reviewer
from orchestrator import orchestrator, TaskState

TEST_WORKSPACE = Path(__file__).resolve().parent.parent / "scratch" / "test_suite_workspace"

class TestFCCAutonomousOSAgent(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Create fresh test workspace
        if TEST_WORKSPACE.exists():
            shutil.rmtree(TEST_WORKSPACE, ignore_errors=True)
        TEST_WORKSPACE.mkdir(parents=True, exist_ok=True)
        db.init_db()

    async def asyncTearDown(self):
        # Clean up
        if TEST_WORKSPACE.exists():
            shutil.rmtree(TEST_WORKSPACE, ignore_errors=True)

    # -------------------------------------------------------------
    # Test A: Normal prompt & planning schema validation
    # -------------------------------------------------------------
    async def test_a_normal_prompt_and_planning(self):
        print("\n--- Running Test A: Normal prompt & planning ---")
        prompt = "Create a hello world microservice in nodejs with index.js and package.json"
        plan = await planner.create_plan(
            task_id="test-plan-001",
            user_prompt=prompt,
            workspace=str(TEST_WORKSPACE),
            permission_mode="balanced"
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan.user_objective, prompt)
        self.assertGreaterEqual(len(plan.implementation_steps), 1)
        self.assertIn("READY_FOR_EXECUTION", plan.status)
        self.assertTrue(len(plan.codex_instructions) > 0)
        print("✓ Test A Passed: Structured plan generated successfully.")

    # -------------------------------------------------------------
    # Test B: Real file operations inside authorized workspace
    # -------------------------------------------------------------
    async def test_b_real_file_operations(self):
        print("\n--- Running Test B: Real file operations ---")
        test_file = TEST_WORKSPACE / "output_test.txt"
        test_content = "Autonomous Agent File Creation Test"
        
        # Test real file creation in workspace
        test_file.write_text(test_content, encoding="utf-8")
        self.assertTrue(test_file.exists())
        self.assertEqual(test_file.read_text(encoding="utf-8"), test_content)

        # Test path validation
        is_valid, resolved = permission_engine.validate_path_in_workspace(str(test_file), str(TEST_WORKSPACE))
        self.assertTrue(is_valid)
        self.assertEqual(Path(resolved), test_file.resolve())
        print("✓ Test B Passed: Real file operations verified within authorized boundary.")

    # -------------------------------------------------------------
    # Test C: Permission interception & waiting state
    # -------------------------------------------------------------
    async def test_c_permission_interception(self):
        print("\n--- Running Test C: Permission interception ---")
        task_id = f"test-task-{uuid.uuid4().hex[:6]}"
        db.create_task(task_id, "Prompt C", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")
        
        # Background coroutine that requests approval
        async def do_request():
            decision, meta = await permission_engine.request_approval(
                task_id=task_id,
                workspace_root=str(TEST_WORKSPACE),
                action_type="SHELL_EXECUTION",
                command="npm install express",
                reason="Install required express framework",
                impact="Downloads packages from npm",
                risk_level=RiskLevel.MEDIUM,
                timeout_seconds=5.0
            )
            return decision

        task_coro = asyncio.create_task(do_request())
        await asyncio.sleep(0.2)  # Give time to register

        # Verify approval is registered as pending
        pending = db.get_pending_approvals(task_id=task_id)
        self.assertEqual(len(pending), 1)
        appr_id = pending[0]["id"]
        self.assertEqual(pending[0]["command"], "npm install express")

        # Resolve approval with allow_once
        permission_engine.submit_decision(appr_id, PermissionDecision.ALLOW_ONCE)
        decision = await task_coro
        self.assertEqual(decision, PermissionDecision.ALLOW_ONCE)
        print("✓ Test C Passed: Intercepted permission request, waited, and resolved.")

    # -------------------------------------------------------------
    # Test D: Allow once behavior
    # -------------------------------------------------------------
    async def test_d_allow_once(self):
        print("\n--- Running Test D: Allow once behavior ---")
        task_id = f"test-task-{uuid.uuid4().hex[:6]}"
        db.create_task(task_id, "Prompt D", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")
        appr_id = f"appr-{uuid.uuid4().hex[:6]}"

        db.create_approval_request(
            approval_id=appr_id,
            task_id=task_id,
            action_type="COMMAND",
            command="npm run build",
            target_path=None,
            working_dir=str(TEST_WORKSPACE),
            reason="Build bundle",
            impact="Compiles code",
            risk_level=RiskLevel.LOW
        )

        permission_engine.submit_decision(appr_id, PermissionDecision.ALLOW_ONCE)

        # Allow once does NOT grant session or persistent rules
        is_permitted, grant = permission_engine.is_action_permitted(
            task_id=task_id,
            workspace_root=str(TEST_WORKSPACE),
            category=ActionCategory.FILE_WRITE,
            command="npm run build",
            permission_mode="ask_permission"
        )
        self.assertFalse(is_permitted, "Allow once must not grant subsequent operations")
        print("✓ Test D Passed: Allow once strictly scopes to the single action.")

    # -------------------------------------------------------------
    # Test E: Session grant behavior
    # -------------------------------------------------------------
    async def test_e_session_grant(self):
        print("\n--- Running Test E: Session grant behavior ---")
        task_id = f"test-task-{uuid.uuid4().hex[:6]}"
        db.create_task(task_id, "Prompt E", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")
        appr_id = f"appr-{uuid.uuid4().hex[:6]}"

        db.create_approval_request(
            approval_id=appr_id,
            task_id=task_id,
            action_type="COMMAND",
            command="npm test",
            target_path=None,
            working_dir=str(TEST_WORKSPACE),
            reason="Run tests",
            impact="Executes unit tests",
            risk_level=RiskLevel.LOW
        )

        # User chooses allow for this session
        permission_engine.submit_decision(
            appr_id,
            PermissionDecision.ALLOW_SESSION,
            meta={"category": ActionCategory.BUILD_TEST}
        )

        # Subsequent matching command in same session should be automatically permitted
        is_permitted, grant = permission_engine.is_action_permitted(
            task_id=task_id,
            workspace_root=str(TEST_WORKSPACE),
            category=ActionCategory.BUILD_TEST,
            command="npm test --watch",
            permission_mode="ask_permission"
        )
        self.assertTrue(is_permitted)
        self.assertIn("session_grant", grant)
        print("✓ Test E Passed: Session grant permits subsequent matching action without prompting.")

    # -------------------------------------------------------------
    # Test F: Persistent project grant persistence & revocation
    # -------------------------------------------------------------
    async def test_f_persistent_project_grant(self):
        print("\n--- Running Test F: Persistent project grant ---")
        rule_id = f"rule-{uuid.uuid4().hex[:6]}"
        canonical_ws = str(TEST_WORKSPACE.resolve())

        # Create persistent rule
        db.create_permission_rule(
            rule_id=rule_id,
            project_path=canonical_ws,
            action_category=ActionCategory.PACKAGE_INSTALL,
            pattern="npm install.*",
            scope="PROJECT",
            description="Allow npm installs for this project"
        )

        # Check rule is active
        is_permitted, grant = permission_engine.is_action_permitted(
            task_id="another-task-id",
            workspace_root=canonical_ws,
            category=ActionCategory.PACKAGE_INSTALL,
            command="npm install lodash",
            permission_mode="ask_permission"
        )
        self.assertTrue(is_permitted)
        self.assertIn("project_rule", grant)

        # Revoke the rule from Settings
        revoked = permission_engine.revoke_rule(rule_id)
        self.assertTrue(revoked)

        # Now it should be denied
        is_permitted_after, _ = permission_engine.is_action_permitted(
            task_id="another-task-id",
            workspace_root=canonical_ws,
            category=ActionCategory.PACKAGE_INSTALL,
            command="npm install lodash",
            permission_mode="ask_permission"
        )
        self.assertFalse(is_permitted_after, "Revoked rule must no longer permit action")
        print("✓ Test F Passed: Persistent project rule works and was successfully revoked.")

    # -------------------------------------------------------------
    # Test G: Denial handling
    # -------------------------------------------------------------
    async def test_g_denial_handling(self):
        print("\n--- Running Test G: Denial handling ---")
        task_id = f"test-task-{uuid.uuid4().hex[:6]}"
        db.create_task(task_id, "Prompt G", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")

        async def do_request():
            decision, meta = await permission_engine.request_approval(
                task_id=task_id,
                workspace_root=str(TEST_WORKSPACE),
                action_type="COMMAND",
                command="del /s /q important_dir",
                reason="Delete files",
                impact="Removes files",
                risk_level=RiskLevel.HIGH,
                timeout_seconds=5.0
            )
            return decision

        task_coro = asyncio.create_task(do_request())
        await asyncio.sleep(0.1)

        pending = db.get_pending_approvals(task_id=task_id)
        self.assertEqual(len(pending), 1)
        appr_id = pending[0]["id"]

        # Human clicks Deny
        permission_engine.submit_decision(appr_id, PermissionDecision.DENY)
        decision = await task_coro
        self.assertEqual(decision, PermissionDecision.DENY)
        print("✓ Test G Passed: Human denial correctly returned to execution runtime.")

    # -------------------------------------------------------------
    # Test H: Full access grant with explicit confirmation
    # -------------------------------------------------------------
    async def test_h_full_access_grant(self):
        print("\n--- Running Test H: Full access grant ---")
        task_id = f"test-task-{uuid.uuid4().hex[:6]}"
        db.create_task(task_id, "Prompt H", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")
        appr_id = f"appr-{uuid.uuid4().hex[:6]}"

        db.create_approval_request(
            approval_id=appr_id,
            task_id=task_id,
            action_type="ALL",
            command=None,
            target_path=None,
            working_dir=str(TEST_WORKSPACE),
            reason="Grant broad autonomous execution for this session",
            impact="Permits normal project write and shell actions",
            risk_level=RiskLevel.HIGH
        )

        permission_engine.submit_decision(appr_id, PermissionDecision.ALLOW_FULL_ACCESS)

        # Normal write is permitted under full access
        is_permitted, grant = permission_engine.is_action_permitted(
            task_id=task_id,
            workspace_root=str(TEST_WORKSPACE),
            category=ActionCategory.FILE_WRITE,
            command="write code",
            permission_mode="balanced"
        )
        self.assertTrue(is_permitted)
        self.assertEqual(grant, "task_full_access")

        # But critical system commands are STILL protected
        is_crit_permitted, _ = permission_engine.is_action_permitted(
            task_id=task_id,
            workspace_root=str(TEST_WORKSPACE),
            category=ActionCategory.HIGH_RISK_SYSTEM,
            command="format C:",
            permission_mode="balanced"
        )
        self.assertFalse(is_crit_permitted, "Critical commands must not be bypassed by full access")
        print("✓ Test H Passed: Full access granted project privileges while preserving critical safeguards.")

    # -------------------------------------------------------------
    # Test I: Iterative verification & failure recovery loop
    # -------------------------------------------------------------
    async def test_i_failure_recovery(self):
        print("\n--- Running Test I: Iterative verification failure detection ---")
        # Run a command known to fail
        failing_res = await verifier.run_command_in_workspace(
            command_str="powershell -Command 'Write-Error \"Simulated syntax failure\"; exit 1'",
            workspace_root=str(TEST_WORKSPACE)
        )
        self.assertFalse(failing_res["passed"])
        self.assertEqual(failing_res["exit_code"], 1)

        # Verifier constructs recovery prompt
        recovery_prompt = verifier.construct_recovery_prompt(
            original_objective="Build microservice",
            verification_errors=[failing_res["error"]],
            attempt_number=1
        )
        self.assertIn("AUTONOMOUS RECOVERY", recovery_prompt)
        self.assertIn("Simulated syntax failure", recovery_prompt)
        print("✓ Test I Passed: Verification failure captured and targeted recovery prompt formulated.")

    # -------------------------------------------------------------
    # Test J: Task cancellation
    # -------------------------------------------------------------
    async def test_j_cancellation(self):
        print("\n--- Running Test J: Task cancellation ---")
        task_id = f"test-cancel-{uuid.uuid4().hex[:6]}"
        db.create_task(task_id, "Long build task", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")
        db.update_task_status(task_id, TaskState.EXECUTING)

        # Cancel task
        cancelled = await orchestrator.cancel_task(task_id)
        self.assertTrue(cancelled)

        task = db.get_task(task_id)
        self.assertEqual(task["status"], TaskState.CANCELLED)
        print("✓ Test J Passed: Task cancellation stopped process and updated state.")

    # -------------------------------------------------------------
    # Test K: Workspace isolation & path traversal blocking
    # -------------------------------------------------------------
    async def test_k_workspace_isolation(self):
        print("\n--- Running Test K: Workspace isolation ---")
        ws = str(TEST_WORKSPACE.resolve())

        # Inside workspace
        inside_path = str((TEST_WORKSPACE / "subdir" / "code.js"))
        valid, _ = permission_engine.validate_path_in_workspace(inside_path, ws)
        self.assertTrue(valid)

        # Path traversal with ..
        traversal_path = str(TEST_WORKSPACE / ".." / ".." / "Windows" / "System32" / "cmd.exe")
        valid, msg = permission_engine.validate_path_in_workspace(traversal_path, ws)
        self.assertFalse(valid, "Path traversal must be strictly blocked")
        self.assertIn("resolves outside authorized workspace", msg)
        print("✓ Test K Passed: Path traversal blocked by backend enforcement.")

    # -------------------------------------------------------------
    # Test L: Concurrent tasks isolation
    # -------------------------------------------------------------
    async def test_l_concurrent_tasks_isolation(self):
        print("\n--- Running Test L: Concurrent tasks isolation ---")
        task_id_1 = f"task-con-1-{uuid.uuid4().hex[:6]}"
        task_id_2 = f"task-con-2-{uuid.uuid4().hex[:6]}"

        db.create_task(task_id_1, "Prompt 1", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")
        db.create_task(task_id_2, "Prompt 2", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")

        appr_1 = f"appr-1-{uuid.uuid4().hex[:6]}"
        appr_2 = f"appr-2-{uuid.uuid4().hex[:6]}"

        # Approval for task 1
        db.create_approval_request(appr_1, task_id_1, "CMD", "cmd1", None, str(TEST_WORKSPACE), "R1", "I1", "LOW")
        # Approval for task 2
        db.create_approval_request(appr_2, task_id_2, "CMD", "cmd2", None, str(TEST_WORKSPACE), "R2", "I2", "LOW")

        p1 = db.get_pending_approvals(task_id=task_id_1)
        p2 = db.get_pending_approvals(task_id=task_id_2)

        self.assertEqual(len(p1), 1)
        self.assertEqual(p1[0]["id"], appr_1)
        self.assertEqual(len(p2), 1)
        self.assertEqual(p2[0]["id"], appr_2)
        print("✓ Test L Passed: Multi-task approvals and states strictly isolated.")

    # -------------------------------------------------------------
    # Test M: Real AGY review integration
    # -------------------------------------------------------------
    async def test_m_real_agy_review(self):
        print("\n--- Running Test M: Real AGY Reviewer ---")
        agy_status = agy_reviewer.get_status()
        self.assertTrue(agy_status["available"])
        self.assertIsNotNone(agy_status["binary_path"])
        task_id_m = f"task-agy-{uuid.uuid4().hex[:6]}"
        db.create_task(task_id_m, "Verify test setup", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")

        # Verify AGY integration interface
        review = await agy_reviewer.review_task(
            task_id=task_id_m,
            user_objective="Verify test setup",
            workspace=str(TEST_WORKSPACE),
            acceptance_criteria=["Tests pass cleanly"],
            modified_files=["test_file.txt"],
            verification_summary={"passed": True, "details": "All tests passed."},
            timeout_seconds=15.0
        )
        self.assertTrue(review["available"])
        self.assertIn("reviewer", review)
        self.assertIn(review["verdict"], ["PASSED", "NEEDS_IMPROVEMENT", "TIMEOUT", "FAILED", "ERROR", "SKIPPED"])
        print(f"✓ Test M Passed: AGY Reviewer evaluated context with verdict: {review['verdict']}")

    # -------------------------------------------------------------
    # Test N: Restart recovery
    # -------------------------------------------------------------
    async def test_n_restart_recovery(self):
        print("\n--- Running Test N: Restart recovery ---")
        stale_id = f"task-stale-{uuid.uuid4().hex[:6]}"
        db.create_task(stale_id, "In-flight task before crash", str(TEST_WORKSPACE), "balanced", "gpt-5.6-terra")
        db.update_task_status(stale_id, TaskState.EXECUTING)

        # Simulate server restart recovery
        db.recover_stale_tasks()

        recovered_task = db.get_task(stale_id)
        self.assertEqual(recovered_task["status"], TaskState.BLOCKED)
        self.assertIn("Interrupted by application restart", recovered_task.get("error_message", ""))
        print("✓ Test N Passed: Stale task safely transitioned to BLOCKED on restart.")

if __name__ == "__main__":
    unittest.main()
