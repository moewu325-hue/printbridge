import logging
import queue
import types
import unittest
import threading
import time
from unittest.mock import Mock, patch

from wechat.bridge import WeChatBridge
from wechat.commands import CommandExecutor
from wechat.notifier import Notifier, NOTIFY_TEMPLATES
import wechat.bridge as bridge_module


class WeChatTests(unittest.TestCase):
    def bridge(self):
        return WeChatBridge(Mock(), queue.Queue(), queue.Queue(), queue.Queue(), logging.getLogger('test'))

    def test_first_snapshot_is_not_a_command(self):
        bridge = self.bridge()
        messages = [types.SimpleNamespace(Name='/cancel all', GetRuntimeId=lambda: [1])]
        control = Mock()
        control.Exists.return_value = True
        control.GetChildren.return_value = messages
        bridge._wx_window = Mock()
        bridge._wx_window.ListControl.return_value = control
        bridge._read_latest_message('allowed')
        self.assertTrue(bridge.command_queue.empty())

    def test_unauthorized_sender_cannot_pause(self):
        config = Mock()
        config.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['allowed'], 'selected_printer': 'P'}.get(k, d)
        controller = Mock()
        executor = CommandExecutor(controller, Mock(), config, logging.getLogger('test'))
        executor.execute('/pause', 'outsider')
        controller.pause_printer.assert_not_called()

    def test_disabled_wechat_cannot_pause(self):
        config = Mock()
        config.get.side_effect = lambda k, d=None: {'wechat_enabled': False, 'wechat_listen_targets': ['allowed']}.get(k, d)
        controller = Mock()
        CommandExecutor(controller, Mock(), config, logging.getLogger('test')).execute('/pause', 'allowed')
        controller.pause_printer.assert_not_called()

    def test_unassociated_latest_file_is_unavailable(self):
        bridge = self.bridge()
        with patch('wechat.bridge.os.listdir', side_effect=AssertionError('must not scan accounts')):
            self.assertIsNone(bridge.find_latest_received_file())

    def test_batched_new_commands_and_independent_chat_baselines(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['A', 'B']}.get(k, d)
        control = Mock()
        control.Exists.return_value = True
        bridge._wx_window = Mock()
        bridge._wx_window.ListControl.return_value = control
        def messages(values):
            return [types.SimpleNamespace(Name=text, GetRuntimeId=lambda i=i: [i]) for i, text in values]
        control.GetChildren.return_value = messages([(1, '/pause')])
        bridge._read_latest_message('A')
        control.GetChildren.return_value = messages([(5, '/pause')])
        bridge._read_latest_message('B')
        control.GetChildren.return_value = messages([(1, '/pause'), (2, '/status'), (3, '/list')])
        bridge._read_latest_message('A')
        self.assertEqual([bridge.command_queue.get_nowait(), bridge.command_queue.get_nowait()], [('A', '/status'), ('A', '/list')])
        self.assertTrue(bridge.command_queue.empty())

    def test_missing_message_identity_is_never_executed(self):
        bridge = self.bridge()
        control = Mock()
        control.Exists.return_value = True
        control.GetChildren.return_value = [types.SimpleNamespace(Name='/pause')]
        bridge._wx_window = Mock()
        bridge._wx_window.ListControl.return_value = control
        bridge._read_latest_message('A')
        bridge._read_latest_message('A')
        self.assertTrue(bridge.command_queue.empty())

    def test_send_text_is_literal_and_no_generic_edit_fallback(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['A']}.get(k, d)
        bridge.running = bridge.available = True
        bridge._wx_window = Mock()
        bridge._open_chat = Mock(return_value=True)
        bridge._verify_chat = Mock(return_value=True)
        pattern = bridge._wx_window.EditControl.return_value.GetValuePattern.return_value
        pattern.SetValue.return_value = True
        pattern.Value = 'report{Enter}.pdf'
        self.assertTrue(bridge._send_now('A', 'report{Enter}.pdf'))
        edit = bridge._wx_window.EditControl.return_value
        edit.GetValuePattern.return_value.SetValue.assert_called_once_with('report{Enter}.pdf')
        edit.SendKeys.assert_called_once_with('{Enter}')
        bridge._wx_window.EditControl.assert_called_once_with(Name='输入')

    def test_start_is_idempotent(self):
        bridge = self.bridge()
        bridge._thread = Mock()
        bridge._thread.is_alive.return_value = True
        with patch('wechat.bridge.threading.Thread') as thread:
            bridge.start()
        thread.assert_not_called()

    def test_removed_notification_does_not_claim_success(self):
        self.assertIn('removed', NOTIFY_TEMPLATES)
        self.assertNotIn('打印完成', NOTIFY_TEMPLATES['removed'])

    def test_notifier_exception_does_not_kill_following_event(self):
        config = Mock()
        config.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_notify_target': 'A'}.get(k, d)
        bridge = Mock()
        bridge.is_available = True
        notifier = Notifier(bridge, config, queue.Queue(), logging.getLogger('test'))
        notifier.notify_queue.put({'status': 'printed', 'document': 'one'})
        notifier.notify_queue.put({'status': 'printed', 'document': 'two'})
        calls = []
        def send(target, text):
            calls.append(text)
            if 'one' in text:
                raise RuntimeError('transient')
            notifier.running = False
            return True
        bridge.send_message.side_effect = send
        notifier.running = True
        try:
            notifier._run_loop()
        except RuntimeError:
            pass
        self.assertEqual(len(calls), 4)
        self.assertIn('two', calls[-1])

    def test_revoke_authorization_while_opening_chat_prevents_send(self):
        bridge = self.bridge()
        values = {'wechat_enabled': True, 'wechat_listen_targets': ['A']}
        bridge.config_mgr.get.side_effect = lambda k, d=None: values.get(k, d)
        bridge.running = bridge.available = True
        bridge._wx_window = Mock()
        bridge._verify_chat = Mock(return_value=True)
        pattern = bridge._wx_window.EditControl.return_value.GetValuePattern.return_value
        pattern.SetValue.return_value = True
        pattern.Value = 'test'
        def open_chat(target):
            values['wechat_listen_targets'] = []
            return True
        bridge._open_chat = open_chat
        self.assertFalse(bridge._send_now('A', 'test'))
        bridge._wx_window.EditControl.return_value.SendKeys.assert_not_called()

    def test_worker_owns_com_and_stop_interrupts_reconnect(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True}.get(k, d)
        entered = threading.Event()
        thread_ids = []
        class Com:
            def __enter__(self):
                entered.set()
                thread_ids.append(threading.get_ident())
            def __exit__(self, *args):
                thread_ids.append(threading.get_ident())
        api = Mock()
        api.UIAutomationInitializerInThread.side_effect = Com
        api.WindowControl.return_value.Exists.return_value = False
        with patch('wechat.bridge.importlib.import_module', return_value=api):
            bridge.start()
            self.assertTrue(entered.wait(1))
            bridge.start()
            started = time.monotonic()
            self.assertTrue(bridge.stop())
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(thread_ids), 2)
        self.assertEqual(thread_ids[0], thread_ids[1])
        self.assertNotEqual(thread_ids[0], threading.get_ident())

    def test_notifier_retry_is_bounded(self):
        config = Mock()
        config.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_notify_target': 'A'}.get(k, d)
        bridge = Mock()
        bridge.send_message.return_value = False
        notifier = Notifier(bridge, config, queue.Queue(), logging.getLogger('test'))
        notifier.running = True
        with patch.object(notifier._stop, 'wait', return_value=False):
            notifier._deliver({'status': 'removed'})
        self.assertEqual(bridge.send_message.call_count, 3)

    def test_failed_set_value_never_presses_enter(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['A']}.get(k, d)
        bridge.running = bridge.available = True
        bridge._wx_window = Mock()
        bridge._open_chat = Mock(return_value=True)
        bridge._verify_chat = Mock(return_value=True)
        edit = bridge._wx_window.EditControl.return_value
        edit.GetValuePattern.return_value.SetValue.return_value = False
        self.assertFalse(bridge._send_now('A', 'new'))
        edit.SendKeys.assert_not_called()

    def test_failed_search_set_value_does_not_open_result(self):
        bridge = self.bridge()
        bridge._wx_window = Mock()
        search = bridge._wx_window.EditControl.return_value
        search.GetValuePattern.return_value.SetValue.return_value = False
        self.assertFalse(bridge._open_chat('A'))
        bridge._wx_window.ListItemControl.assert_not_called()

    def test_print_rechecks_authorization_after_file_lookup(self):
        values = {'wechat_enabled': True, 'wechat_listen_targets': ['A'], 'selected_printer': 'P', 'allowed_file_types': ['pdf']}
        config = Mock()
        config.get.side_effect = lambda k, d=None: values.get(k, d)
        controller = Mock()
        executor = CommandExecutor(controller, Mock(), config, logging.getLogger('test'))
        def exists(path):
            values['wechat_enabled'] = False
            values['wechat_listen_targets'] = []
            return True
        with patch('os.path.isfile', side_effect=exists):
            executor.execute('/print file.pdf', 'A')
        controller.print_file.assert_not_called()

    def test_mutating_commands_recheck_after_printer_resolution(self):
        for command, method in [('/pause', 'pause_printer'), ('/resume', 'resume_printer'), ('/cancel 1', 'cancel_job'), ('/cancel all', 'purge_queue')]:
            with self.subTest(command=command):
                values = {'wechat_enabled': True, 'wechat_listen_targets': ['A'], 'selected_printer': 'P'}
                config = Mock()
                config.get.side_effect = lambda k, d=None: values.get(k, d)
                controller = Mock()
                def resolve(config):
                    values['wechat_enabled'] = False
                    return 'P'
                with patch('app_paths.resolve_printer', side_effect=resolve):
                    CommandExecutor(controller, Mock(), config, logging.getLogger('test')).execute(command, 'A')
                getattr(controller, method).assert_not_called()

    def test_timeout_after_enter_is_unknown_not_retryable_false(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['A']}.get(k, d)
        bridge.running = bridge.available = True
        bridge._wx_window = Mock()
        bridge._open_chat = Mock(return_value=True)
        bridge._verify_chat = Mock(return_value=True)
        edit = bridge._wx_window.EditControl.return_value
        edit.GetValuePattern.return_value.SetValue.return_value = True
        edit.GetValuePattern.return_value.Value = 'new'
        entered, release = threading.Event(), threading.Event()
        def enter(*args):
            entered.set()
            release.wait(1)
            return True
        edit.SendKeys.side_effect = enter
        original_request = bridge_module._SendRequest
        workers = []
        def request_factory(*args):
            request = original_request(*args)
            request.done = Mock()
            request.done.wait.side_effect = lambda timeout: entered.wait(1) and False
            return request
        def dispatch(request):
            def work():
                try:
                    bridge._send_now(request.target, request.text, request)
                except Exception:
                    pass
            worker = threading.Thread(target=work)
            workers.append(worker)
            worker.start()
        exception = None
        try:
            with patch.object(bridge_module, '_SendRequest', side_effect=request_factory), patch.object(bridge._outgoing, 'put_nowait', side_effect=dispatch):
                try:
                    bridge.send_message('A', 'new')
                except Exception as exc:
                    exception = exc
        finally:
            release.set()
            for worker in workers:
                worker.join(1)
        self.assertEqual(type(exception).__name__, 'DeliveryUnknown')
        edit.SendKeys.assert_called_once_with('{Enter}')

    def test_unknown_delivery_is_not_retried(self):
        config = Mock()
        config.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_notify_target': 'A'}.get(k, d)
        bridge = Mock()
        exception = getattr(bridge_module, 'DeliveryUnknown', RuntimeError)
        bridge.send_message.side_effect = exception('unknown')
        notifier = Notifier(bridge, config, queue.Queue(), logging.getLogger('test'))
        notifier.running = True
        with patch.object(notifier._stop, 'wait', return_value=False):
            notifier._deliver({'status': 'removed'})
        self.assertEqual(bridge.send_message.call_count, 1)

    def test_set_value_readback_mismatch_never_sends(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['A']}.get(k, d)
        bridge.running = bridge.available = True
        bridge._wx_window = Mock()
        bridge._open_chat = Mock(return_value=True)
        edit = bridge._wx_window.EditControl.return_value
        edit.GetValuePattern.return_value.SetValue.return_value = True
        edit.GetValuePattern.return_value.Value = 'stale draft'
        self.assertFalse(bridge._send_now('A', 'new'))
        edit.SendKeys.assert_not_called()

    def test_print_rejects_changed_printer_or_type_after_lookup(self):
        for updates in ({'selected_printer': 'Other'}, {'allowed_file_types': ['txt']}):
            with self.subTest(updates=updates):
                values = {'wechat_enabled': True, 'wechat_listen_targets': ['A'], 'selected_printer': 'P', 'allowed_file_types': ['pdf']}
                config = Mock()
                config.get.side_effect = lambda k, d=None: values.get(k, d)
                controller = Mock()
                def exists(path):
                    values.update(updates)
                    return True
                with patch('os.path.isfile', side_effect=exists):
                    CommandExecutor(controller, Mock(), config, logging.getLogger('test')).execute('/print file.pdf', 'A')
                controller.print_file.assert_not_called()

    def test_send_exception_after_enter_is_unknown(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['A']}.get(k, d)
        bridge.running = bridge.available = True
        bridge._wx_window = Mock()
        bridge._open_chat = Mock(return_value=True)
        bridge._verify_chat = Mock(return_value=True)
        edit = bridge._wx_window.EditControl.return_value
        edit.GetValuePattern.return_value.SetValue.return_value = True
        edit.GetValuePattern.return_value.Value = 'new'
        edit.SendKeys.side_effect = RuntimeError('failed after entering keyboard code')
        with self.assertRaises(bridge_module.DeliveryUnknown):
            bridge._send_now('A', 'new')

    def test_pending_timeout_is_cancelled_before_any_enter(self):
        bridge = self.bridge()
        bridge.config_mgr.get.side_effect = lambda k, d=None: {'wechat_enabled': True, 'wechat_listen_targets': ['A']}.get(k, d)
        bridge.running = bridge.available = True
        bridge._open_chat = Mock(return_value=True)
        original_request = bridge_module._SendRequest
        def request_factory(*args):
            request = original_request(*args)
            request.done = Mock()
            request.done.wait.return_value = False
            return request
        with patch.object(bridge_module, '_SendRequest', side_effect=request_factory):
            self.assertFalse(bridge.send_message('A', 'new'))
        request = bridge._outgoing.get_nowait()
        self.assertTrue(request.cancelled.is_set())
        self.assertFalse(bridge._send_now('A', 'new', request))
