"""SQLite数据库: 打印历史记录存储"""
import sqlite3
import os
import threading
from datetime import datetime, timedelta
from typing import Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS print_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    printer_name TEXT NOT NULL,
    document_name TEXT NOT NULL,
    owner TEXT DEFAULT '',
    pages INTEGER DEFAULT 0,
    status TEXT DEFAULT 'spooling',
    submitted_time TEXT NOT NULL,
    completed_time TEXT,
    source TEXT DEFAULT 'local',
    wechat_sender TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_history_job_id ON print_history(job_id);
CREATE INDEX IF NOT EXISTS idx_history_submitted ON print_history(submitted_time);
CREATE INDEX IF NOT EXISTS idx_history_status ON print_history(status);
"""


class PrintHistoryDB:
    """打印历史数据库, 线程安全"""

    def __init__(self, db_path: str):
        self._path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.executescript(SCHEMA)
            conn.commit()
            conn.close()

    def add_job(self, job_id: int, printer_name: str, document_name: str,
                owner: str = "", pages: int = 0, status: str = "spooling",
                submitted_time: str = "", source: str = "local",
                wechat_sender: str = "") -> int:
        """添加打印记录, 返回row id"""
        if not submitted_time:
            submitted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """INSERT INTO print_history
                   (job_id, printer_name, document_name, owner, pages, status,
                    submitted_time, source, wechat_sender)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, printer_name, document_name, owner, pages, status,
                 submitted_time, source, wechat_sender),
            )
            conn.commit()
            row_id = cur.lastrowid
            conn.close()
            return row_id

    def update_job_status(self, job_id: int, status: str):
        """更新作业状态, 终态时记录完成时间"""
        with self._lock:
            conn = self._get_conn()
            if status in ("printed", "error", "cancelled", "paperout", "offline"):
                completed = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    """UPDATE print_history SET status=?, completed_time=?
                       WHERE job_id=? AND completed_time IS NULL
                       ORDER BY id DESC LIMIT 1""",
                    (status, completed, job_id),
                )
            else:
                conn.execute(
                    """UPDATE print_history SET status=?
                       WHERE job_id=? AND completed_time IS NULL
                       ORDER BY id DESC LIMIT 1""",
                    (status, job_id),
                )
            conn.commit()
            conn.close()

    def get_recent_jobs(self, limit: int = 100) -> list[dict]:
        """获取最近的打印记录"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM print_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_jobs_since(self, since_hours: int = 24) -> list[dict]:
        """获取指定小时内的打印记录"""
        since = (datetime.now() - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM print_history WHERE submitted_time >= ? ORDER BY id DESC",
            (since,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """统计打印数量"""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM print_history").fetchone()[0]
        success = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE status='printed'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE status IN ('error','paperout','offline')"
        ).fetchone()[0]
        conn.close()
        return {"total": total, "success": success, "failed": failed}

    def cleanup(self, keep_days: int = 30):
        """清理旧记录, 保留最近N天"""
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM print_history WHERE submitted_time < ?", (cutoff,))
            conn.commit()
            conn.execute("VACUUM")
            conn.close()
