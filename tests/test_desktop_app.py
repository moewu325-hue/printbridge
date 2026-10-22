import importlib.util
import json
import logging
from pathlib import Path
import queue
import tempfile
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock


class DesktopApplicationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def application(self):
        self.assertIsNotNone(importlib.util.find_spec('desktop_app'), 'desktop_app coordinator is missing')
        from desktop_app import DesktopApplication
        self.monitor = SimpleNamespace(printer_name='A', printer_status='就绪', running=False)
        self.runtime = SimpleNamespace(bridge=SimpleNamespace(running=False))
        def start(allow_wechat):
            self.calls.append(('start', allow_wechat))
            self.monitor.running = True
        def stop():
            self.calls.append(('runtime.stop', threading.get_ident()))
            self.monitor.running = False
        self.runtime.start, self.runtime.stop, self.runtime.apply_settings = start, stop, Mock()
        self.panel = SimpleNamespace(toggle=Mock(), restore_if_enabled=Mock(),
            stop=lambda: self.calls.append(('panel.stop', threading.get_ident())),
            shutdown=lambda: self.calls.append(('panel.shutdown', threading.get_ident())))
        self.tray = SimpleNamespace(setup=lambda: False, stop=Mock())
        self.window = SimpleNamespace(tree=SimpleNamespace(get_children=lambda: ()), tray_available=False)
        return DesktopApplication(Path(self.directory.name), Mock(), Mock(), self.monitor,
            self.runtime, {'status_queue': queue.Queue()}, logging.getLogger('DesktopTests'), 'test',
            root=self.root, panel_factory=lambda *a: self.panel,
            window_factory=lambda *a, **kw: self.window, tray_factory=lambda *a, **kw: self.tray)

    def test_smoke_uses_isolated_services_and_tk_shutdown_thread(self):
        app = self.application()
        self.assertEqual(app.run(smoke_test=True, smoke_delay_ms=30), 0)
        result = json.loads((Path(self.directory.name) / 'smoke.json').read_text(encoding='utf-8'))
        self.assertTrue(result['ok'] and result['stopped'])
        self.assertFalse(result['wechat_started'])
        self.assertIn(('start', False), self.calls)
        self.panel.restore_if_enabled.assert_not_called()
        main_thread = threading.get_ident()
        self.assertIn(('panel.stop', main_thread), self.calls)
        self.assertTrue(all(ident != main_thread for name, ident in self.calls if name.endswith('shutdown') or name == 'runtime.stop'))

    def test_exit_returns_while_services_are_blocked_and_is_idempotent(self):
        app = self.application()
        release, entered = threading.Event(), threading.Event()
        def stop():
            entered.set()
            release.wait(2)
        self.runtime.stop = stop
        started = time.monotonic()
        app.request_exit()
        app.request_exit()
        self.assertLess(time.monotonic() - started, .2)
        self.assertTrue(entered.wait(1))
        release.set()
        self.assertEqual(app.run(), 0)
        self.assertEqual(sum(name == 'panel.stop' for name, _ in self.calls), 1)

    def test_external_destroy_still_stops_services(self):
        app = self.application()
        self.root.after(20, self.root.destroy)
        self.assertEqual(app.run(), 0)
        self.assertTrue(any(name == 'runtime.stop' for name, _ in self.calls))

    def test_smoke_fails_when_monitor_never_started(self):
        app = self.application()
        self.runtime.start = Mock()
        self.assertEqual(app.run(smoke_test=True, smoke_delay_ms=30), 1)

    def test_service_start_exception_is_a_failed_smoke(self):
        app = self.application()
        self.runtime.start = Mock(side_effect=RuntimeError('injected startup failure'))
        self.root.report_callback_exception = Mock()
        self.assertEqual(app.run(smoke_test=True, smoke_delay_ms=30), 1)
        self.assertTrue(any(name == 'runtime.stop' for name, _ in self.calls))

    def test_lost_tray_restores_hidden_main_window(self):
        app = self.application()
        self.window.tray_available = True
        self.window.show = Mock()
        self.tray.is_ready = False
        self.root.withdraw()
        app._check_lifecycle()
        self.assertFalse(self.window.tray_available)
        self.window.show.assert_called_once()
        app.request_exit()
        app.run()


if __name__ == '__main__':
    unittest.main()
