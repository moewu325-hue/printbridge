import logging
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from panel.panel_window import PrinterPanel
from panel.printer_backend import JobInfo, Snapshot


class MemoryConfig:
    def __init__(self):
        self.data = {'panel_mode': 'full', 'panel_visible': False, 'poll_interval_sec': 2}
    def get(self, key, default=None):
        return self.data.get(key, default)
    def update(self, values):
        self.data.update(values)


class PanelWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.panel = PrinterPanel(self.root, MemoryConfig(), logging.getLogger('PanelTests'))
        with patch('panel.panel_window.set_toolwindow'):
            self.panel._create()
        self.job = JobInfo(7, 'report.pdf', '等待中', 'info', 1, '', '')

    def tearDown(self):
        if hasattr(self, 'panel'):
            self.panel.stop()
            self.panel.shutdown()
        if hasattr(self, 'root'):
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def test_action_event_opens_toast_and_pump_remains_usable(self):
        self.panel._events.put(('action', True, '任务已提交'))
        self.panel._pump()
        self.assertTrue(any(isinstance(child, tk.Toplevel) for child in self.panel._top.winfo_children()))
        self.panel._events.put(('action', False, '权限不足'))
        self.panel._pump()

    def test_pump_limits_each_batch(self):
        self.panel._render = Mock()
        for _ in range(150):
            self.panel._events.put(('snapshot', Snapshot()))
        self.panel._pump()
        self.assertEqual(self.panel._events.qsize(), 50)

    def test_cancel_menu_keeps_original_printer_after_new_snapshot(self):
        self.panel._commands.submit = Mock()
        self.panel._snapshot = Snapshot(selected_name='A', jobs=(self.job,))
        menu = tk.Menu(self.panel._top, tearoff=False)
        self.panel._build_job_menu(menu, self.job)
        self.panel._snapshot = Snapshot(selected_name='B')
        with patch('panel.panel_window.messagebox.askyesno', return_value=True):
            menu.invoke(1)
        args = self.panel._commands.submit.call_args.args
        self.assertEqual(args[-2:], ('A', 7))
        menu.destroy()

    def test_failed_queue_disables_job_actions(self):
        self.panel._snapshot = Snapshot(selected_name='A', jobs=(self.job,), jobs_fresh=False)
        menu = tk.Menu(self.panel._top, tearoff=False)
        self.panel._build_job_menu(menu, self.job)
        self.assertEqual(menu.entrycget(0, 'state'), 'disabled')
        self.assertEqual(menu.entrycget(1, 'state'), 'disabled')
        menu.destroy()

    def test_external_destroy_still_allows_nonblocking_stop(self):
        self.root.destroy()
        self.panel.stop()
        self.assertIsNone(self.panel._top)

    def test_hidden_panel_cancels_callbacks_and_restarts(self):
        self.panel._backend.snapshot = lambda: Snapshot()
        self.panel.show()
        self.panel.hide()
        self.assertEqual(self.panel._after_ids, set())
        self.panel.show()
        self.assertTrue(self.panel._after_ids)
        self.panel.stop()
        self.assertEqual(self.panel._after_ids, set())


if __name__ == '__main__':
    unittest.main()
