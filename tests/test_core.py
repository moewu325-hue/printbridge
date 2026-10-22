"""配置、历史与进程锁的回归，全部使用临时数据。"""
import gc
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import ConfigManager
from db import PrintHistoryDB
from utils import singleton_lock


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'config.json'
        detector = patch('config._auto_detect_default_printer', return_value='')
        detector.start()
        self.addCleanup(detector.stop)

    def test_reads_return_independent_snapshots(self):
        manager = ConfigManager(str(self.path))
        before = manager.get('wechat_listen_targets')[:]
        manager.get('wechat_listen_targets').append('untrusted')
        manager.data['wechat_listen_targets'].append('untrusted2')
        self.assertEqual(manager.get('wechat_listen_targets'), before)

    def test_replace_failure_keeps_disk_and_memory(self):
        manager = ConfigManager(str(self.path))
        before, snapshot = self.path.read_bytes(), manager.data
        with patch('os.replace', side_effect=OSError('injected replace failure')):
            with self.assertRaises(OSError):
                manager.update({'selected_printer': 'new-printer'})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(manager.data, snapshot)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_invalid_update_is_rejected_without_state_change(self):
        manager = ConfigManager(str(self.path))
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            manager.update({'poll_interval_sec': 0})
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_existing_config_is_not_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            ConfigManager(str(self.path))
        self.assertEqual(self.path.read_text(), '{broken')

    def test_non_object_config_is_rejected(self):
        self.path.write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            ConfigManager(str(self.path))
        self.assertEqual(self.path.read_text(), '[]')

    def test_read_only_config_does_not_create_missing_file(self):
        manager = ConfigManager(str(self.path), create=False)
        self.assertFalse(self.path.exists())
        self.assertFalse(manager.get('wechat_enabled'))
        self.assertFalse(manager.get('auto_print_wechat_files'))

    def test_existing_explicit_preferences_are_preserved(self):
        self.path.write_text(json.dumps({'wechat_enabled': True,
            'auto_print_wechat_files': True, 'allowed_file_types': ['docx']}))
        manager = ConfigManager(str(self.path))
        self.assertTrue(manager.get('wechat_enabled'))
        self.assertEqual(manager.get('allowed_file_types'), ['docx'])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = PrintHistoryDB(str(Path(self.tmp.name) / 'history.db'))

    def test_status_update_works_without_sqlite_update_limit_extension(self):
        row = self.db.add_job(7, 'A', 'test')
        self.db.update_job_status(7, 'printing')
        self.assertEqual(self.db.get_recent_jobs()[0]['status'], 'printing')

    def test_recoverable_error_can_become_printed(self):
        row = self.db.add_job(7, 'A', 'test')
        for status in ('paperout', 'offline', 'error', 'printing'):
            self.db.update_job_status(7, status, printer_name='A', record_id=row)
            self.assertIsNone(self.db.get_recent_jobs()[0]['completed_time'])
        self.db.update_job_status(7, 'printed', printer_name='A', record_id=row)
        self.assertIsNotNone(self.db.get_recent_jobs()[0]['completed_time'])

    def test_cross_printer_job_id_never_updates_other_record(self):
        a = self.db.add_job(7, 'A', 'A-doc')
        b = self.db.add_job(7, 'B', 'B-doc')
        self.db.update_job_status(7, 'cancelled', printer_name='A', record_id=a)
        rows = {r['id']: r for r in self.db.get_recent_jobs()}
        self.assertEqual(rows[a]['status'], 'cancelled')
        self.assertEqual(rows[b]['status'], 'spooling')

    def test_initial_terminal_job_has_completion_time(self):
        self.db.add_job(7, 'A', 'test', status='printed')
        self.assertIsNotNone(self.db.get_recent_jobs()[0]['completed_time'])

    def test_current_directory_database_path_is_supported(self):
        old = os.getcwd()
        try:
            os.chdir(self.tmp.name)
            database = PrintHistoryDB('relative.db')
            self.assertEqual(database.get_stats()['total'], 0)
        finally:
            os.chdir(old)


class LockTests(unittest.TestCase):
    def test_lock_stays_held_until_released(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        self.assertTrue(singleton_lock(port))
        gc.collect()
        code = 'import sys;sys.path.insert(0,sys.argv[1]);from utils import singleton_lock;print(singleton_lock(int(sys.argv[2])))'
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
            str(ROOT), str(port)], capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(result.stdout.strip(), 'False')
        import utils
        if hasattr(utils, 'release_singleton_lock'):
            utils.release_singleton_lock()


if __name__ == '__main__':
    unittest.main()
