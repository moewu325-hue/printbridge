"""入口路径与自动文件消费者的安全边界。"""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PathTests(unittest.TestCase):
    def test_explicit_profile_is_not_relative_to_bundle(self):
        from app_paths import get_data_dir, config_path
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PRINTBRIDGE_DATA_DIR': directory}):
            self.assertEqual(get_data_dir(), Path(directory).resolve())
            self.assertEqual(config_path(), Path(directory).resolve() / 'config.json')

    def test_configured_printer_has_priority_over_system_default(self):
        from app_paths import resolve_printer
        config = Mock()
        config.get.return_value = 'Chosen'
        self.assertEqual(resolve_printer(config), 'Chosen')
        self.assertEqual(resolve_printer(config, 'Explicit'), 'Explicit')

    def test_default_profile_uses_local_appdata(self):
        from app_paths import get_data_dir
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PRINTBRIDGE_DATA_DIR': '', 'LOCALAPPDATA': directory}):
            self.assertEqual(get_data_dir(), Path(directory).resolve() / 'PrintBridge')

    def test_interrupted_legacy_config_copy_does_not_publish_partial_file(self):
        from app_paths import prepare_data_dir
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / 'legacy'
            legacy.mkdir()
            (legacy / 'config.json').write_text('{"selected_printer":"legacy"}')
            local = Path(directory) / 'local'
            with patch.dict(os.environ, {'PRINTBRIDGE_DATA_DIR': '', 'LOCALAPPDATA': str(local)}), \
                    patch('app_paths.__file__', str(legacy / 'app_paths.py')), \
                    patch('shutil.copyfileobj', side_effect=OSError('injected interruption')):
                with self.assertRaises(OSError):
                    prepare_data_dir()
            self.assertFalse((local / 'PrintBridge/config.json').exists())
            self.assertEqual(list((local / 'PrintBridge').glob('*.tmp')), [])


class RuntimePolicyTests(unittest.TestCase):
    def make_runtime(self, directory):
        from app_runtime import AppRuntime
        from config import ConfigManager
        from utils import SafeQueue
        manager = ConfigManager(str(Path(directory) / 'config.json'))
        queues = {key: SafeQueue(maxsize=10) for key in
                  ('job_queue', 'notify_queue', 'command_queue', 'file_queue', 'status_queue')}
        runtime = AppRuntime(manager, Mock(), Mock(), queues, Mock())
        return manager, runtime

    def test_disabled_or_unauthorized_queued_command_never_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, runtime = self.make_runtime(directory)
            runtime.executor = Mock()
            runtime.dispatch_command(('文件传输助手', '/cancel all'))
            runtime.executor.execute.assert_not_called()
            manager.update({'wechat_enabled': True, 'wechat_listen_targets': ['other']})
            runtime.dispatch_command(('文件传输助手', '/cancel all'))
            runtime.executor.execute.assert_not_called()

    def test_auto_print_requires_switch_sender_extension_and_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, runtime = self.make_runtime(directory)
            document = Path(directory) / 'test.pdf'
            document.write_bytes(b'synthetic test')
            manager.update({'wechat_enabled': True})
            runtime.dispatch_file(('文件传输助手', str(document), 'pdf'))
            runtime.controller.print_file.assert_not_called()
            manager.update({'auto_print_wechat_files': True, 'allowed_file_types': ['txt']})
            runtime.dispatch_file(('文件传输助手', str(document), 'txt'))
            runtime.controller.print_file.assert_not_called()
            manager.update({'allowed_file_types': ['pdf']})
            runtime.dispatch_file(('unauthorized', str(document), 'pdf'))
            runtime.dispatch_file(('文件传输助手', directory, 'pdf'))
            runtime.controller.print_file.assert_not_called()

    def test_valid_file_is_staged_without_overwriting_existing_name(self):
        with tempfile.TemporaryDirectory() as directory:
            manager, runtime = self.make_runtime(directory)
            document = Path(directory) / 'test.pdf'
            document.write_bytes(b'new')
            manager.update({'wechat_enabled': True, 'auto_print_wechat_files': True,
                            'selected_printer': 'Chosen', 'allowed_file_types': ['pdf']})
            temp = Path(manager.get('temp_dir'))
            temp.mkdir()
            original = temp / 'test.pdf'
            original.write_bytes(b'keep')
            runtime.bridge = Mock()
            runtime.dispatch_file(('文件传输助手', str(document), 'pdf'))
            staged = Path(runtime.controller.print_file.call_args.args[0])
            self.assertNotEqual(staged, original)
            self.assertEqual(staged.read_bytes(), b'new')
            self.assertEqual(original.read_bytes(), b'keep')
            self.assertEqual(runtime.controller.print_file.call_args.args[1], 'Chosen')

    def test_notification_failure_does_not_remove_file_already_requested_for_print(self):
        from wechat.bridge import DeliveryUnknown
        with tempfile.TemporaryDirectory() as directory:
            manager, runtime = self.make_runtime(directory)
            document = Path(directory) / 'test.pdf'
            document.write_bytes(b'pending external print application')
            manager.update({'wechat_enabled': True, 'auto_print_wechat_files': True,
                            'selected_printer': 'Chosen', 'allowed_file_types': ['pdf']})
            runtime.controller.print_file.return_value = True
            runtime.bridge = Mock()
            runtime.bridge.send_message.side_effect = DeliveryUnknown('reply unknown')
            with self.assertRaises(DeliveryUnknown):
                runtime.dispatch_file(('文件传输助手', str(document), 'pdf'))
            staged = Path(runtime.controller.print_file.call_args.args[0])
            self.assertTrue(staged.is_file())
            self.assertEqual(staged.read_bytes(), document.read_bytes())


if __name__ == '__main__':
    unittest.main()
