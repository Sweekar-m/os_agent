import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

logger = logging.getLogger("db")
logger.setLevel(logging.INFO)

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "agent.db"

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    """Initializes tables for tasks, events, approvals, permission rules, and audit logs."""
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                user_prompt TEXT NOT NULL,
                workspace TEXT NOT NULL,
                permission_mode TEXT NOT NULL DEFAULT 'balanced',
                model TEXT NOT NULL DEFAULT 'gpt-5.6-terra',
                status TEXT NOT NULL DEFAULT 'QUEUED',
                plan_json TEXT,
                verification_json TEXT,
                review_json TEXT,
                completion_report_json TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                command TEXT,
                target_path TEXT,
                working_dir TEXT NOT NULL,
                reason TEXT,
                impact TEXT,
                risk_level TEXT NOT NULL DEFAULT 'MEDIUM',
                status TEXT NOT NULL DEFAULT 'PENDING',
                decision TEXT,
                decision_meta_json TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decided_at TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS permission_rules (
                id TEXT PRIMARY KEY,
                project_path TEXT NOT NULL,
                action_category TEXT NOT NULL,
                pattern TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'PROJECT',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMP
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                event_name TEXT NOT NULL,
                details_json TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # Index for fast lookup
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_proj ON permission_rules(project_path);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_logs(task_id);")
            
        logger.info(f"Database initialized at {DB_PATH}")
    finally:
        conn.close()

# ----------------- Tasks CRUD -----------------

def create_task(task_id: str, prompt: str, workspace: str, permission_mode: str, model: str) -> Dict[str, Any]:
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            conn.execute("""
            INSERT INTO tasks (id, user_prompt, workspace, permission_mode, model, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?)
            """, (task_id, prompt, workspace, permission_mode, model, now, now))
        log_audit(task_id, "TASK_CREATED", {"prompt": prompt, "workspace": workspace, "mode": permission_mode, "model": model})
        return get_task(task_id)
    finally:
        conn.close()

def update_task_status(task_id: str, status: str, error_message: Optional[str] = None):
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            if error_message:
                conn.execute("""
                UPDATE tasks SET status = ?, error_message = ?, updated_at = ? WHERE id = ?
                """, (status, error_message, now, task_id))
            else:
                conn.execute("""
                UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?
                """, (status, now, task_id))
        log_audit(task_id, "STATUS_CHANGE", {"status": status, "error": error_message})
    finally:
        conn.close()

def save_task_plan(task_id: str, plan_dict: Any):
    if hasattr(plan_dict, "to_dict"):
        plan_dict = plan_dict.to_dict()
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            conn.execute("""
            UPDATE tasks SET plan_json = ?, updated_at = ? WHERE id = ?
            """, (json.dumps(plan_dict), now, task_id))
        log_audit(task_id, "PLAN_SAVED", {"steps_count": len(plan_dict.get("implementation_steps", plan_dict.get("steps", [])))})
    finally:
        conn.close()

def save_task_verification(task_id: str, verification_dict: Dict[str, Any]):
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            conn.execute("""
            UPDATE tasks SET verification_json = ?, updated_at = ? WHERE id = ?
            """, (json.dumps(verification_dict), now, task_id))
        log_audit(task_id, "VERIFICATION_SAVED", {"passed": verification_dict.get("passed", False)})
    finally:
        conn.close()

def save_task_review(task_id: str, review_dict: Dict[str, Any]):
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            conn.execute("""
            UPDATE tasks SET review_json = ?, updated_at = ? WHERE id = ?
            """, (json.dumps(review_dict), now, task_id))
        log_audit(task_id, "REVIEW_SAVED", {"reviewer": review_dict.get("reviewer")})
    finally:
        conn.close()

def save_completion_report(task_id: str, report_dict: Dict[str, Any], status: Optional[str] = None):
    conn = get_db_connection()
    now = utc_now_iso()
    final_status = status or report_dict.get("final_status") or "COMPLETED"
    try:
        with conn:
            conn.execute("""
            UPDATE tasks SET completion_report_json = ?, status = ?, updated_at = ? WHERE id = ?
            """, (json.dumps(report_dict), final_status, now, task_id))
        log_audit(task_id, "TASK_COMPLETED" if final_status == "COMPLETED" else "TASK_FAILED", {"status": final_status})
    finally:
        conn.close()

def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ["plan_json", "verification_json", "review_json", "completion_report_json"]:
            if d.get(k):
                try:
                    d[k.replace("_json", "")] = json.loads(d[k])
                except Exception:
                    d[k.replace("_json", "")] = None
            else:
                d[k.replace("_json", "")] = None
        # Result convenience alias
        if d.get("completion_report") and isinstance(d["completion_report"], dict):
            d["result"] = d["completion_report"].get("summary", "")
        elif d.get("error_message"):
            d["result"] = d.get("error_message")
        else:
            d["result"] = ""
        return d
    finally:
        conn.close()

def list_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for k in ["plan_json", "verification_json", "review_json", "completion_report_json"]:
                if d.get(k):
                    try:
                        d[k.replace("_json", "")] = json.loads(d[k])
                    except Exception:
                        d[k.replace("_json", "")] = None
                else:
                    d[k.replace("_json", "")] = None
            result.append(d)
        return result
    finally:
        conn.close()

# ----------------- Events -----------------

def add_task_event(task_id: str, event_type: str, payload: Dict[str, Any]):
    conn = get_db_connection()
    try:
        with conn:
            conn.execute("""
            INSERT INTO task_events (task_id, event_type, payload_json)
            VALUES (?, ?, ?)
            """, (task_id, event_type, json.dumps(payload)))
    finally:
        conn.close()

def get_task_events(task_id: str, since_id: int = 0) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        rows = conn.execute("""
        SELECT id, task_id, event_type, payload_json, created_at
        FROM task_events WHERE task_id = ? AND id > ? ORDER BY id ASC
        """, (task_id, since_id)).fetchall()
        events = []
        for r in rows:
            events.append({
                "id": r["id"],
                "task_id": r["task_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"]
            })
        return events
    finally:
        conn.close()

# ----------------- Approvals -----------------

def create_approval_request(
    approval_id: str,
    task_id: str,
    action_type: str,
    command: Optional[str],
    target_path: Optional[str],
    working_dir: str,
    reason: str,
    impact: str,
    risk_level: str
) -> Dict[str, Any]:
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            conn.execute("""
            INSERT INTO approvals (
                id, task_id, action_type, command, target_path, working_dir,
                reason, impact, risk_level, status, requested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """, (
                approval_id, task_id, action_type, command, target_path, working_dir,
                reason, impact, risk_level, now
            ))
        log_audit(task_id, "APPROVAL_REQUESTED", {
            "approval_id": approval_id,
            "action_type": action_type,
            "command": command,
            "risk_level": risk_level
        })
        return get_approval(approval_id)
    finally:
        conn.close()

def resolve_approval_request(
    approval_id: str,
    decision: str,
    decision_meta: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            conn.execute("""
            UPDATE approvals
            SET status = ?, decision = ?, decision_meta_json = ?, decided_at = ?
            WHERE id = ?
            """, (
                decision.upper(), decision, json.dumps(decision_meta or {}), now, approval_id
            ))
        appr = get_approval(approval_id)
        if appr:
            log_audit(appr.get("task_id"), "APPROVAL_DECIDED", {
                "approval_id": approval_id,
                "decision": decision,
                "meta": decision_meta
            })
        return appr
    finally:
        conn.close()

def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("decision_meta_json"):
            try:
                d["decision_meta"] = json.loads(d["decision_meta_json"])
            except Exception:
                d["decision_meta"] = {}
        return d
    finally:
        conn.close()

def get_pending_approvals(task_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        if task_id:
            rows = conn.execute("""
            SELECT * FROM approvals WHERE status = 'PENDING' AND task_id = ? ORDER BY requested_at ASC
            """, (task_id,)).fetchall()
        else:
            rows = conn.execute("""
            SELECT * FROM approvals WHERE status = 'PENDING' ORDER BY requested_at ASC
            """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("decision_meta_json"):
                try:
                    d["decision_meta"] = json.loads(d["decision_meta_json"])
                except Exception:
                    d["decision_meta"] = {}
            result.append(d)
        return result
    finally:
        conn.close()

# ----------------- Permission Rules -----------------

def create_permission_rule(
    rule_id: str,
    project_path: str,
    action_category: str,
    pattern: str,
    scope: str = "PROJECT",
    description: Optional[str] = None
) -> Dict[str, Any]:
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            conn.execute("""
            INSERT INTO permission_rules (
                id, project_path, action_category, pattern, scope, description, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (rule_id, project_path, action_category, pattern, scope, description, now))
        log_audit(None, "PERMISSION_RULE_CREATED", {
            "rule_id": rule_id,
            "project_path": project_path,
            "category": action_category,
            "pattern": pattern,
            "scope": scope
        })
        return get_permission_rule(rule_id)
    finally:
        conn.close()

def revoke_permission_rule(rule_id: str) -> bool:
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            cur = conn.execute("""
            UPDATE permission_rules SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL
            """, (now, rule_id))
            revoked = cur.rowcount > 0
        if revoked:
            log_audit(None, "PERMISSION_RULE_REVOKED", {"rule_id": rule_id})
        return revoked
    finally:
        conn.close()

def get_permission_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM permission_rules WHERE id = ?", (rule_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def list_permission_rules(project_path: Optional[str] = None, include_revoked: bool = False) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        query = "SELECT * FROM permission_rules WHERE 1=1"
        params = []
        if not include_revoked:
            query += " AND revoked_at IS NULL"
        if project_path:
            query += " AND project_path = ?"
            params.append(project_path)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# ----------------- Audit Logs -----------------

def log_audit(task_id: Optional[str], event_name: str, details: Dict[str, Any]):
    try:
        conn = get_db_connection()
        now = utc_now_iso()
        with conn:
            conn.execute("""
            INSERT INTO audit_logs (task_id, event_name, details_json, timestamp)
            VALUES (?, ?, ?, ?)
            """, (task_id, event_name, json.dumps(details), now))
        conn.close()
    except Exception as e:
        logger.error(f"Audit log error: {e}")

def get_audit_logs(task_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        if task_id:
            rows = conn.execute("""
            SELECT * FROM audit_logs WHERE task_id = ? ORDER BY timestamp DESC LIMIT ?
            """, (task_id, limit)).fetchall()
        else:
            rows = conn.execute("""
            SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?
            """, (limit,)).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d["details_json"])
            except Exception:
                d["details"] = {}
            res.append(d)
        return res
    finally:
        conn.close()

# ----------------- Restart Recovery -----------------

def recover_stale_tasks():
    """Recovers any tasks that were left in EXECUTING or PLANNING during unexpected server shutdown."""
    conn = get_db_connection()
    now = utc_now_iso()
    try:
        with conn:
            rows = conn.execute("""
            SELECT id, status FROM tasks WHERE status IN ('PLANNING', 'EXECUTING', 'VERIFYING', 'AWAITING_APPROVAL')
            """).fetchall()
            for r in rows:
                tid = r["id"]
                conn.execute("""
                UPDATE tasks SET status = 'BLOCKED', error_message = 'Interrupted by application restart', updated_at = ?
                WHERE id = ?
                """, (now, tid))
                conn.execute("""
                INSERT INTO audit_logs (task_id, event_name, details_json, timestamp)
                VALUES (?, 'TASK_RECOVERED_AFTER_RESTART', ?, ?)
                """, (tid, json.dumps({"previous_status": r["status"]}), now))
                logger.info(f"Recovered stale task {tid} from status {r['status']} -> BLOCKED")
    finally:
        conn.close()

# Auto-initialize DB on import
init_db()
