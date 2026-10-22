import logging
import os
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from printer import monitor


class MemoryDB:
    def __init__(self):
        self.rows = []
        self.updates = []

    def add_job(self, **job):
        self.rows.append(dict(job))
        return len(self.rows)

    def update_job_status(self, job_id, status, printer_name=None, record_id=None):
        self.updates.append((job_id, status, printer_name, record_id))
        self.rows[record_id - 1]['status'] = status


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDB()
        self.jobs = queue.Queue()
        self.notifications = queue.Queue()
        self.monitor = monitor.PrintMonitor('A', 60, self.jobs, self.notifications,
                                            self.db, logging.getLogger('printer-test'))
        self.patches = [patch.object(monitor.win32print, 'OpenPrinter', return_value=123),
                        patch.object(monitor.win32print, 'ClosePrinter'),
                        patch.object(monitor.win32print, 'GetPrinter', return_value={'Status': 0})]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.monitor.stop)

    def raw(self, status=0, jid=7, submitted='first'):
        return {'JobId': jid, 'Status': status, 'pDocument': 'doc', 'Submitted': submitted}

    def poll(self, rows):
        with patch.object(monitor.win32print, 'EnumJobs', return_value=rows):
            self.monitor._poll_once()

    def test_disappearance_is_not_success_and_cancel_is_preserved(self):
        for status, expected in [(0, 'removed'), (monitor.win32print.JOB_STATUS_DELETING, 'cancelled')]:
            with self.subTest(status=status):
                self.poll([self.raw(status)])
                self.poll([])
                self.assertEqual(self.db.rows[-1]['status'], expected)
                self.assertNotIn('printed', [j['status'] for j in self.notifications.queue])

    def test_recovery_updates_unique_row_and_notifies_once_on_completion(self):
        for status in (monitor.win32print.JOB_STATUS_PAPEROUT, 0,
                       monitor.win32print.JOB_STATUS_OFFLINE, monitor.win32print.JOB_STATUS_PRINTED):
            self.poll([self.raw(status)])
        self.poll([])
        self.assertEqual(self.db.rows[0]['status'], 'printed')
        self.assertTrue(all(update[2:] == ('A', 1) for update in self.db.updates))
        self.assertEqual(sum(j['status'] == 'printed' for j in self.notifications.queue), 1)

    def test_job_id_reuse_has_new_history_identity(self):
        self.poll([self.raw(submitted='one')])
        self.poll([self.raw(submitted='two')])
        self.assertEqual(len(self.db.rows), 2)
        self.assertEqual(self.db.rows[0]['status'], 'removed')
        self.assertEqual(self.monitor.get_status_summary()['jobs'][0]['record_id'], 2)

    def test_device_status_and_snapshot_are_independent(self):
        with patch.object(monitor.win32print, 'GetPrinter', return_value={'Status': monitor.win32print.PRINTER_STATUS_OFFLINE}):
            self.poll([self.raw()])
        self.assertIn('脱机', self.monitor.printer_status)
        summary = self.monitor.get_status_summary()
        summary['jobs'][0]['status'] = 'corrupt'
        self.assertNotEqual(self.monitor.get_status_summary()['jobs'][0]['status'], 'corrupt')

    def test_full_queue_and_failed_snapshot_dont_invent_completion(self):
        rows = [self.raw(jid=n) for n in range(235)]
        with patch.object(monitor.win32print, 'EnumJobs', side_effect=lambda h,s,c,l: rows[s:s+c]):
            self.monitor._poll_once()
        self.assertEqual(len(self.db.rows), 235)
        with patch.object(monitor.win32print, 'EnumJobs', side_effect=RuntimeError('snapshot unavailable')):
            with self.assertRaises(RuntimeError):
                self.monitor._poll_once()
        self.assertEqual(len(self.monitor.get_status_summary()['jobs']), 235)
        self.assertFalse(self.db.updates)

    def test_switch_discards_blocked_old_generation(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args):
            entered.set()
            release.wait(2)
            return [self.raw()]
        with patch.object(monitor.win32print, 'EnumJobs', side_effect=blocked):
            worker = threading.Thread(target=self.monitor._poll_once)
            worker.start()
            self.assertTrue(entered.wait(1))
            self.monitor.switch_printer('B')
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.db.rows)
        self.assertEqual(list(self.jobs.queue), [('RESET', None)])

    def test_start_is_idempotent_stop_interrupts_long_wait(self):
        polled = threading.Event()
        def enum(*args):
            polled.set()
            return []
        with patch.object(monitor.win32print, 'EnumJobs', side_effect=enum):
            self.monitor.start()
            first = self.monitor._thread
            self.monitor.start()
            self.assertIs(self.monitor._thread, first)
            self.assertTrue(polled.wait(1))
            self.monitor.stop()
            self.assertFalse(first.is_alive())

    def test_complete_flag_is_not_printed(self):
        self.assertNotEqual(monitor.decode_status(monitor.win32print.JOB_STATUS_COMPLETE), 'printed')

    def test_stop_does_not_close_a_handle_while_driver_uses_it(self):
        entered, release = threading.Event(), threading.Event()
        closed_by = []
        def blocked(*args):
            entered.set()
            release.wait(2)
            return [self.raw()]
        with patch.object(monitor.win32print, 'EnumJobs', side_effect=blocked), \
                patch.object(monitor.win32print, 'ClosePrinter', side_effect=lambda h: closed_by.append(threading.get_ident())):
            self.monitor.start()
            self.assertTrue(entered.wait(1))
            worker = self.monitor._thread
            # 模拟驱动超过join时限；stop不能从调用线程释放在用句柄。
            with patch.object(worker, 'join'):
                self.assertFalse(self.monitor.stop())
            self.assertTrue(self.monitor.running)
            self.monitor.start()
            self.assertIs(self.monitor._thread, worker)
            self.assertFalse(closed_by)
            release.set()
            worker.join(2)
            self.assertEqual(closed_by, [worker.ident])
        self.assertFalse(self.db.rows)

    def test_database_fault_retries_and_refresh_wakes_worker(self):
        failed, recovered = threading.Event(), threading.Event()
        original = self.db.add_job
        def add(**job):
            if not failed.is_set():
                failed.set()
                raise RuntimeError('database busy')
            value = original(**job)
            recovered.set()
            return value
        with patch.object(monitor.win32print, 'EnumJobs', return_value=[self.raw()]), \
                patch.object(self.db, 'add_job', side_effect=add), \
                self.assertLogs('printer-test', level='ERROR'):
            self.monitor.start()
            self.assertTrue(failed.wait(1))
            self.monitor.request_refresh()
            self.assertTrue(recovered.wait(1))
            self.monitor.stop()
        self.assertEqual(len(self.db.rows), 1)
        self.assertEqual(self.db.rows[0]['status'], 'spooling')

    def test_repeated_connection_failures_keep_selected_device(self):
        with patch.object(monitor.win32print, 'OpenPrinter', side_effect=RuntimeError('offline')):
            for _ in range(4):
                with self.assertRaises(RuntimeError):
                    self.monitor._poll_once()
        self.assertEqual(self.monitor.printer_name, 'A')
        self.assertFalse(self.db.rows)

    def test_switch_reused_id_uses_another_history_row(self):
        self.poll([self.raw()])
        self.monitor.switch_printer('B')
        self.poll([self.raw(monitor.win32print.JOB_STATUS_OFFLINE)])
        self.poll([self.raw()])
        self.assertEqual([j['printer_name'] for j in self.db.rows], ['A', 'B'])
        self.assertEqual(self.db.updates[-1][2:], ('B', 2))

    def test_real_history_database_recovers_and_scopes_same_ids(self):
        from db import PrintHistoryDB
        with tempfile.TemporaryDirectory() as temp:
            self.monitor.db = PrintHistoryDB(os.path.join(temp, 'history.db'))
            self.poll([self.raw(monitor.win32print.JOB_STATUS_PAPEROUT)])
            self.poll([self.raw()])
            first = self.monitor.db.get_recent_jobs()[0]
            self.assertEqual(first['status'], 'spooling')
            self.assertIsNone(first['completed_time'])
            self.monitor.switch_printer('B')
            self.poll([self.raw()])
            self.poll([self.raw(monitor.win32print.JOB_STATUS_PRINTED)])
            rows = self.monitor.db.get_recent_jobs()
            self.assertEqual([(r['printer_name'], r['status']) for r in rows],
                             [('B', 'printed'), ('A', 'spooling')])
            self.assertIsNotNone(rows[0]['completed_time'])
            self.assertIsNone(rows[1]['completed_time'])

    def test_observed_printed_survives_database_failure_then_disappearance(self):
        self.poll([self.raw()])
        with patch.object(self.db, 'update_job_status', side_effect=RuntimeError('busy')):
            with self.assertRaises(RuntimeError):
                self.poll([self.raw(monitor.win32print.JOB_STATUS_PRINTED)])
        self.poll([])
        self.assertEqual(self.db.rows[0]['status'], 'printed')
        self.assertEqual([j['status'] for j in self.notifications.queue], ['printed'])

    def test_switch_back_reuses_identity_and_history_record(self):
        self.poll([self.raw()])
        self.monitor.switch_printer('B')
        self.monitor.switch_printer('A')
        self.poll([self.raw()])
        self.assertEqual(len(self.db.rows), 1)
        self.assertEqual(self.monitor.get_status_summary()['jobs'][0]['record_id'], 1)
        self.assertEqual(list(self.jobs.queue)[-1][0], 'NEW')

    def test_printed_is_monotonic_when_driver_clears_flags(self):
        self.poll([self.raw()])
        self.poll([self.raw(monitor.win32print.JOB_STATUS_PRINTED)])
        self.poll([self.raw()])
        self.assertEqual(self.monitor.get_status_summary()['jobs'][0]['status'], 'printed')
        self.poll([])
        self.assertEqual(self.db.rows[0]['status'], 'printed')
        self.assertEqual([j['status'] for j in self.notifications.queue], ['printed'])

    def test_first_printed_observation_survives_failed_insert(self):
        with patch.object(self.db, 'add_job', side_effect=RuntimeError('busy')):
            with self.assertRaises(RuntimeError):
                self.poll([self.raw(monitor.win32print.JOB_STATUS_PRINTED)])
        self.poll([])
        self.assertEqual([j['status'] for j in self.db.rows], ['printed'])
        self.assertEqual([j['status'] for j in self.notifications.queue], ['printed'])

    def test_pending_old_device_is_persisted_without_polluting_new_display(self):
        self.poll([self.raw()])
        with patch.object(self.db, 'update_job_status', side_effect=RuntimeError('busy')):
            with self.assertRaises(RuntimeError):
                self.poll([self.raw(monitor.win32print.JOB_STATUS_PRINTED)])
        self.monitor.switch_printer('B')
        self.poll([])
        self.assertEqual(self.db.rows[0]['status'], 'printed')
        actions = list(self.jobs.queue)
        reset = actions.index(('RESET', None))
        self.assertEqual(actions[reset + 1:], [])


if __name__ == '__main__':
    unittest.main()
