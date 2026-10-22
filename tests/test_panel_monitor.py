import queue
import threading
import time
import unittest
from types import SimpleNamespace

from panel.monitor import CommandWorker, PrinterMonitor
from panel.printer_backend import Snapshot


class PanelMonitorTests(unittest.TestCase):
    def test_repeated_restart_keeps_one_blocked_poll(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def snapshot():
            calls.append(1)
            entered.set()
            release.wait(2)
            return Snapshot()
        monitor = PrinterMonitor(SimpleNamespace(snapshot=snapshot), queue.Queue(), interval=30)
        monitor.start()
        self.assertTrue(entered.wait(1))
        try:
            for _ in range(10):
                monitor.stop()
                monitor.start()
            self.assertEqual(len(calls), 1)
        finally:
            monitor.stop()
            release.set()
            monitor.join(timeout=1)

    def test_stop_is_nonblocking_and_restart_discards_old_result(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def snapshot():
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(2)
                return Snapshot(selected_name='old')
            return Snapshot(selected_name='new')
        events = queue.Queue()
        monitor = PrinterMonitor(SimpleNamespace(snapshot=snapshot), events, interval=30)
        monitor.start()
        self.assertTrue(entered.wait(1))
        timer = threading.Timer(.4, release.set)
        timer.start()
        try:
            started = time.monotonic()
            monitor.stop()
            self.assertLess(time.monotonic() - started, .2)
            monitor.start()
            self.assertEqual(events.get(timeout=1)[1].selected_name, 'new')
            release.set()
            monitor.stop()
            monitor.join(timeout=1)
            self.assertTrue(events.empty())
        finally:
            release.set()
            monitor.stop()
            timer.join()

    def test_switch_discards_blocked_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        selected = ['A']
        def snapshot():
            captured = selected[0]
            if captured == 'A':
                entered.set()
                release.wait(2)
            return Snapshot(selected_name=captured)
        backend = SimpleNamespace(snapshot=snapshot, set_selected=lambda name: selected.__setitem__(0, name))
        events = queue.Queue()
        monitor = PrinterMonitor(backend, events, interval=30)
        monitor.start()
        self.assertTrue(entered.wait(1))
        try:
            monitor.set_selected('B')
            release.set()
            self.assertEqual(events.get(timeout=1)[1].selected_name, 'B')
        finally:
            release.set()
            monitor.stop()

    def test_worker_stop_discards_pending_and_late_completion(self):
        entered, release = threading.Event(), threading.Event()
        events, calls = queue.Queue(), []
        def blocked():
            entered.set()
            release.wait(2)
            return True
        worker = CommandWorker(events)
        worker.submit('running', blocked, after=lambda: calls.append('after'))
        self.assertTrue(entered.wait(1))
        worker.submit('pending', lambda: calls.append('pending'))
        timer = threading.Timer(.4, release.set)
        timer.start()
        try:
            started = time.monotonic()
            worker.stop()
            self.assertLess(time.monotonic() - started, .2)
            self.assertFalse(worker.submit('late', lambda: calls.append('late')))
            release.set()
            worker.join(timeout=1)
            self.assertEqual(calls, [])
            self.assertTrue(events.empty())
            worker.start()
            worker.submit('again', lambda: (True, 'done'))
            self.assertEqual(events.get(timeout=1), ('action', True, 'again：done'))
        finally:
            release.set()
            worker.stop()
            timer.join()

    def test_restarted_worker_waits_for_inflight_system_call(self):
        entered, release, second = threading.Event(), threading.Event(), threading.Event()
        def blocked():
            entered.set()
            release.wait(2)
            return True
        worker = CommandWorker(queue.Queue())
        worker.submit('first', blocked)
        self.assertTrue(entered.wait(1))
        try:
            worker.stop()
            worker.start()
            worker.submit('second', lambda: second.set())
            self.assertFalse(second.wait(.05))
            release.set()
            self.assertTrue(second.wait(1))
        finally:
            release.set()
            worker.stop()
            worker.join(timeout=1)


if __name__ == '__main__':
    unittest.main()
