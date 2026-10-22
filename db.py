"""打印历史；连接按次关闭，历史行身份与驱动作业ID分离。"""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import threading

TERMINAL_STATUSES = frozenset(('printed', 'cancelled', 'removed'))
SCHEMA = """
CREATE TABLE IF NOT EXISTS print_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
 printer_name TEXT NOT NULL, document_name TEXT NOT NULL,
 owner TEXT DEFAULT '', pages INTEGER DEFAULT 0, status TEXT DEFAULT 'spooling',
 submitted_time TEXT NOT NULL, completed_time TEXT,
 source TEXT DEFAULT 'local', wechat_sender TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_job_id ON print_history(job_id);
CREATE INDEX IF NOT EXISTS idx_history_submitted ON print_history(submitted_time);
CREATE INDEX IF NOT EXISTS idx_history_status ON print_history(status);
CREATE INDEX IF NOT EXISTS idx_history_printer_job ON print_history(printer_name,job_id,id);
"""


class PrintHistoryDB:
    def __init__(self, db_path: str):
        self._path = str(Path(db_path).resolve())
        self._lock = threading.RLock()
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._get_conn()) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript(SCHEMA)
            conn.commit()

    def _get_conn(self):
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def add_job(self, job_id: int, printer_name: str, document_name: str,
                owner: str = '', pages: int = 0, status: str = 'spooling',
                submitted_time: str = '', source: str = 'local', wechat_sender: str = '') -> int:
        submitted_time = submitted_time or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        completed = datetime.now().strftime('%Y-%m-%d %H:%M:%S') if status in TERMINAL_STATUSES else None
        with self._lock, closing(self._get_conn()) as conn, conn:
            cur = conn.execute('''INSERT INTO print_history
                (job_id,printer_name,document_name,owner,pages,status,submitted_time,
                 completed_time,source,wechat_sender) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (job_id, printer_name, document_name, owner, pages, status,
                 submitted_time, completed, source, wechat_sender))
            return cur.lastrowid

    def update_job_status(self, job_id: int, status: str, printer_name=None, record_id=None):
        conditions, values = ['job_id=?', 'completed_time IS NULL'], [job_id]
        if printer_name is not None:
            conditions.append('printer_name=?')
            values.append(printer_name)
        if record_id is not None:
            conditions.append('id=?')
            values.append(record_id)
        completed = datetime.now().strftime('%Y-%m-%d %H:%M:%S') if status in TERMINAL_STATUSES else None
        query = '''UPDATE print_history SET status=?,completed_time=? WHERE id=(
            SELECT id FROM print_history WHERE ''' + ' AND '.join(conditions) + ' ORDER BY id DESC LIMIT 1)'
        with self._lock, closing(self._get_conn()) as conn, conn:
            conn.execute(query, [status, completed, *values])

    def get_recent_jobs(self, limit: int = 100) -> list[dict]:
        with closing(self._get_conn()) as conn:
            return [dict(row) for row in conn.execute(
                'SELECT * FROM print_history ORDER BY id DESC LIMIT ?', (max(0, int(limit)),))]

    def get_jobs_since(self, since_hours: int = 24) -> list[dict]:
        since = (datetime.now() - timedelta(hours=since_hours)).strftime('%Y-%m-%d %H:%M:%S')
        with closing(self._get_conn()) as conn:
            return [dict(row) for row in conn.execute(
                'SELECT * FROM print_history WHERE submitted_time>=? ORDER BY id DESC', (since,))]

    def get_stats(self) -> dict:
        with closing(self._get_conn()) as conn:
            row = conn.execute('''SELECT COUNT(*) AS total,
                COALESCE(SUM(status='printed'),0) AS success,
                COALESCE(SUM(status IN ('error','paperout','offline')),0) AS failed
                FROM print_history''').fetchone()
            return dict(row)

    def cleanup(self, keep_days: int = 30):
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d %H:%M:%S')
        with self._lock, closing(self._get_conn()) as conn, conn:
            conn.execute('DELETE FROM print_history WHERE submitted_time<?', (cutoff,))
