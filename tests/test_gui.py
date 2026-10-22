import queue
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from gui.main_window import MainWindow

class BoundedEmpty:
    def __init__(self): self.calls = 0
    def get_nowait(self):
        self.calls += 1
        if self.calls > 101: raise AssertionError('GUI spins on empty SafeQueue')
        return None

class GuiTests(unittest.TestCase):
    def setUp(self):
        try: self.root = tk.Tk(); self.root.withdraw()
        except tk.TclError as exc: self.skipTest(str(exc))
        self.config = Mock(); self.config.get.return_value = False
        self.monitor = SimpleNamespace(printer_name='A', printer_status='ready', request_refresh=Mock(), switch_printer=Mock())
        self.window = MainWindow(self.root,self.config,Mock(),self.monitor,{'job_queue':queue.Queue(),'status_queue':queue.Queue()},Mock())
    def tearDown(self):
        if hasattr(self,'root'):
            self.root.update_idletasks()
            self.root.destroy()
    def job(self, record=1, printer='A', status='printing'):
        return dict(job_id=7, record_id=record,printer_name=printer,status=status)
    def test_safequeue_empty_returns(self):
        empty=BoundedEmpty(); self.window.queues['job_queue']=empty
        self.window._poll_queues(); self.assertEqual(empty.calls,1)
    def test_poll_is_bounded(self):
        for _ in range(150): self.window.queues['status_queue'].put(('printer','ok'))
        self.window._poll_queues(); self.assertEqual(self.window.queues['status_queue'].qsize(),50)
    def test_identity_includes_printer_and_record(self):
        for job in [self.job(),self.job(2),self.job(1,'B')]: self.window._add_job_to_tree(job)
        self.assertEqual(len(self.window.tree.get_children()),3)
    def test_clear_keeps_recoverable_errors(self):
        for n,status in enumerate(['paperout','offline','error','printed','cancelled','removed']):
            self.window._add_job_to_tree(self.job(n,status=status))
        self.window._on_clear_completed(); self.assertEqual(len(self.window.tree.get_children()),3)
    def test_reset_clears_rows(self):
        self.window._add_job_to_tree(self.job()); self.window.queues['job_queue'].put(('RESET',None))
        self.window._poll_queues(); self.assertFalse(self.window.tree.get_children())
    def test_refresh_wakes_monitor(self):
        self.window._on_refresh(); self.monitor.request_refresh.assert_called_once()

    def test_close_without_tray_requests_exit(self):
        self.window.request_exit=Mock(); self.config.get.return_value=True
        self.window._on_close(); self.window.request_exit.assert_called_once()
    def test_job_control_uses_row_printer_in_worker(self):
        import threading
        done=threading.Event(); calls=[]
        def cancel(printer, job_id):
            calls.append((printer,job_id,threading.current_thread().name)); done.set(); return True
        self.window.controller=SimpleNamespace(cancel_job=cancel)
        job=self.job(printer='B'); self.window._add_job_to_tree(job)
        self.window._context_job_id=self.window.tree.get_children()[0]
        self.window._on_cancel_job(); self.assertTrue(done.wait(2))
        self.assertEqual(calls[0][:2],('B',7)); self.assertEqual(calls[0][2],'PrintControl')

    def test_sent_and_cancelling_remain_active_with_chinese_labels(self):
        for n, status in enumerate(('sent', 'cancelling')):
            self.window._add_job_to_tree(self.job(n, status=status))
        self.window._on_clear_completed()
        self.assertEqual(len(self.window.tree.get_children()), 2)
        self.assertIn('活跃: 2', self.window._stats_label.cget('text'))
        labels = {self.window.tree.item(i, 'values')[2] for i in self.window.tree.get_children()}
        self.assertEqual(labels, {'已发送至打印机', '正在取消'})

    def test_filters_keep_hidden_rows_and_recovering_jobs(self):
        self.window._search_var.set('ALICE')
        self.window._filter_var.set('需处理')
        self.window._add_job_to_tree(dict(self.job(), document='测试长文件.pdf', owner='Alice', status='offline'))
        self.assertEqual(len(self.window.tree.get_children()), 1)
        self.window._update_job_in_tree(self.job(status='printing'))
        self.assertFalse(self.window.tree.get_children())
        self.window._filter_var.set('进行中')
        self.assertEqual(len(self.window.tree.get_children()), 1)
        self.assertEqual(len(self.window._jobs), 1)

    def test_clearing_finished_in_filtered_view_clears_hidden_finished(self):
        self.window._filter_var.set('需处理')
        self.window._add_job_to_tree(self.job(1, status='printed'))
        self.window._add_job_to_tree(self.job(2, status='offline'))
        self.window._on_clear_completed()
        self.assertEqual(len(self.window._jobs), 1)

    def test_operation_feedback_survives_job_update(self):
        self.window.queues['status_queue'].put(('operation', ('暂停打印机', True, '')))
        self.window._poll_queues()
        self.window._add_job_to_tree(self.job())
        self.assertEqual(self.window._feedback_label.cget('text'), '暂停打印机：成功')

    def test_selected_terminal_job_disables_controls(self):
        self.window._add_job_to_tree(self.job(status='printed'))
        self.window.tree.selection_set(self.window.tree.get_children()[0])
        self.window._on_selection_changed()
        self.assertTrue(all(button.instate(['disabled']) for button in self.window._job_buttons.values()))

    def test_long_printer_name_stays_on_one_line_at_minimum_size(self):
        self.monitor.printer_name = '合成测试长打印机名称' * 20
        self.root.geometry('760x500')
        self.root.deiconify()
        self.window._update_stats()
        self.root.update()
        self.assertLess(self.window._printer_label.winfo_height(), 40)
        self.assertGreater(self.window.tree.winfo_height(), 100)
        self.assertTrue(self.window._printer_label.cget('text').endswith('…'))

    def test_destroy_cancels_owned_poll_callback(self):
        callback = self.window._poll_id
        self.root.update_idletasks()
        self.root.destroy()
        self.assertNotIn(callback, self.root.tk.call('after', 'info'))
        del self.root

    def test_high_dpi_keeps_controls_and_a_useful_table_height(self):
        self.root.update_idletasks()
        self.root.destroy()
        self.root = tk.Tk()
        self.root.tk.call('tk', 'scaling', 2.5)
        self.window = MainWindow(self.root, self.config, Mock(), self.monitor,
                                 {'job_queue': queue.Queue(), 'status_queue': queue.Queue()}, Mock())
        self.root.geometry('760x500')
        self.root.update()
        self.assertGreaterEqual(self.window.tree.winfo_height(), 100)
        for button in self.window._job_buttons.values():
            self.assertEqual(button.winfo_width(), button.winfo_reqwidth())

    def test_narrow_table_preserves_columns_with_horizontal_scroll(self):
        self.root.geometry('760x500')
        self.root.deiconify()
        self.root.update()
        self.assertLess(self.window.tree.xview()[1], 1)

class SettingsTests(unittest.TestCase):
    setUp = GuiTests.setUp
    tearDown = GuiTests.tearDown
    def make_dialog(self):
        from gui.settings_dialog import SettingsDialog
        with patch.object(SettingsDialog, 'wait_window'), patch('gui.settings_dialog.PrintController.get_all_printers', return_value=[]):
            self.config.data = {'allowed_file_types': ['docx', 'rtf', 'tiff'], 'poll_interval_sec': 2}
            return SettingsDialog(self.root,self.config,self.monitor,Mock())
    def test_extensions_preserve_exact_permissions(self):
        dialog=self.make_dialog()
        with patch('gui.settings_dialog.messagebox.showinfo'):
            dialog._on_save()
        self.assertEqual(set(self.config.update.call_args.args[0]['allowed_file_types']), {'docx', 'rtf', 'tiff'})
    def test_invalid_interval_keeps_config(self):
        for value in ('abc','0','11'):
            dialog=self.make_dialog(); dialog._poll_var.set(value)
            with patch('gui.settings_dialog.messagebox.showerror') as error:
                dialog._on_save()
            self.config.update.assert_not_called(); error.assert_called_once()
            dialog.destroy()

    def test_safe_defaults_and_unavailable_auto_collection(self):
        dialog = self.make_dialog()
        self.assertFalse(dialog._wechat_var.get())
        self.assertFalse(dialog._auto_print_var.get())
        self.assertTrue(dialog._auto_print_check.instate(['disabled']))
        dialog.destroy()

    def test_unchecked_custom_extension_is_not_silently_restored(self):
        dialog = self.make_dialog()
        dialog._type_vars['rtf'].set(False)
        with patch('gui.settings_dialog.messagebox.showinfo'):
            dialog._on_save()
        self.assertEqual(set(self.config.update.call_args.args[0]['allowed_file_types']), {'docx', 'tiff'})

    def test_disabled_auto_collection_preserves_existing_configuration(self):
        from gui.settings_dialog import SettingsDialog
        self.config.data = {'allowed_file_types': ['pdf'], 'auto_print_wechat_files': True}
        with patch.object(SettingsDialog, 'wait_window'), patch('gui.settings_dialog.PrintController.get_all_printers', return_value=[]):
            dialog = SettingsDialog(self.root, self.config, self.monitor, Mock())
        with patch('gui.settings_dialog.messagebox.showinfo'):
            dialog._on_save()
        self.assertTrue(self.config.update.call_args.args[0]['auto_print_wechat_files'])

class TrayTests(unittest.TestCase):
    def test_late_setup_cannot_revive_failed_event_loop(self):
        import threading
        from gui.tray import SystemTray
        entered, released, finished = threading.Event(), threading.Event(), threading.Event()
        class FailedIcon:
            @property
            def visible(self):
                return False
            @visible.setter
            def visible(self, value):
                entered.set()
                released.wait(2)
            def run(self, setup=None):
                def delayed_setup():
                    try:
                        setup(self)
                    finally:
                        finished.set()
                threading.Thread(target=delayed_setup, daemon=True).start()
                entered.wait(2)
                raise OSError('injected event loop failure')
            def stop(self):
                released.set()
        module = Mock()
        module.Icon.return_value = FailedIcon()
        tray = SystemTray(Mock(), Mock(), Mock(), Mock())
        try:
            with patch.dict('sys.modules', {'pystray': module}):
                tray.setup()
            self.assertTrue(entered.wait(2))
            tray._thread.join(2)
            self.assertFalse(tray._thread.is_alive())
            released.set()
            self.assertTrue(finished.wait(2))
            self.assertFalse(tray.is_ready)
        finally:
            released.set()
            tray.stop()

    def test_async_setup_does_not_claim_ready_before_icon_is_visible(self):
        import threading
        from gui.tray import SystemTray
        released = threading.Event()
        fake_icon = Mock()
        fake_icon.run.side_effect = lambda setup=None: released.wait(1)
        fake_icon.stop.side_effect = released.set
        module = Mock()
        module.Icon.return_value = fake_icon
        tray = SystemTray(SimpleNamespace(queues={'status_queue': queue.Queue()}), Mock(), Mock(), Mock())
        try:
            with patch.dict('sys.modules', {'pystray': module}):
                self.assertFalse(tray.setup())
            self.assertFalse(getattr(tray, 'is_ready', False))
        finally:
            released.set()
            tray.stop()

    def test_readiness_requires_visible_icon_and_resets_on_stop(self):
        import threading
        from gui.tray import SystemTray
        released, initialized = threading.Event(), threading.Event()
        fake_icon = Mock()
        def run(setup=None):
            setup(fake_icon)
            initialized.set()
            released.wait(1)
        fake_icon.run.side_effect = run
        fake_icon.stop.side_effect = released.set
        module = Mock()
        module.Icon.return_value = fake_icon
        tray = SystemTray(SimpleNamespace(queues={'status_queue': queue.Queue()}), Mock(), Mock(), Mock())
        try:
            with patch.dict('sys.modules', {'pystray': module}):
                tray.setup()
            self.assertTrue(initialized.wait(1))
            self.assertTrue(tray.is_ready)
            self.assertIs(fake_icon.visible, True)
        finally:
            released.set()
            tray.stop()
        self.assertFalse(tray.is_ready)

    def test_callbacks_only_signal_queues_and_shutdown(self):
        from gui.tray import SystemTray
        window=SimpleNamespace(queues={'status_queue':queue.Queue()})
        shutdown=Mock(); tray=SystemTray(window,Mock(),shutdown,Mock())
        tray._on_show(None,None); self.assertEqual(window.queues['status_queue'].get_nowait(),('ui','show'))
        tray._on_exit(None,None); shutdown.set.assert_called_once()
    def test_setup_failure_returns_false(self):
        from gui.tray import SystemTray
        tray=SystemTray(Mock(),Mock(),Mock(),Mock())
        with patch.dict('sys.modules',{'pystray': None}):
            self.assertFalse(tray.setup())

if __name__=='__main__': unittest.main()
