import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

import printbridge_cli as cli


class CliTests(unittest.TestCase):
    def test_spooler_enumeration_failure_is_not_empty_success(self):
        from contextlib import redirect_stdout
        import io
        import json
        import printbridge_cli
        output = io.StringIO()
        with patch('printer.controller.win32print.EnumPrinters', side_effect=RuntimeError('spooler unavailable')), redirect_stdout(output):
            code = printbridge_cli.main(['--status', '--json'])
        result = json.loads(output.getvalue())
        self.assertNotEqual(code, 0)
        self.assertFalse(result['ok'])
        self.assertIsNotNone(result['error'])

    def test_json_status_is_one_document(self):
        controller = type('Controller', (), {'get_all_printers': staticmethod(lambda **kwargs: [])})
        import sys, types
        module = types.ModuleType('printer.controller')
        module.PrintController = controller
        with patch.dict(sys.modules, {'printer.controller': module}):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli.cmd_status(True)
        self.assertEqual(json.loads(output.getvalue())['command'], 'status')

    def test_duplicate_name_is_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for month in ('old', 'new'):
                (root / month).mkdir()
                (root / month / 'report.pdf').write_text('x')
            with patch.object(cli, 'find_wechat_file_storage', return_value=str(root)), patch.object(cli, '_get_search_dirs', return_value=[]):
                with self.assertRaises(ValueError):
                    cli.find_file('report.pdf')

    def test_fuzzy_name_is_not_printable_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'month').mkdir()
            (root / 'month' / 'report-final.pdf').write_text('x')
            with patch.object(cli, 'find_wechat_file_storage', return_value=str(root)), patch.object(cli, '_get_search_dirs', return_value=[]):
                self.assertEqual(cli.find_file('report'), '')

    def test_cancel_failure_has_json_and_nonzero_code(self):
        controller = Mock()
        controller.cancel_job.return_value = False
        output = io.StringIO()
        with patch.object(cli, '_controller', return_value=controller), patch.object(cli, '_printer', return_value='chosen'), contextlib.redirect_stdout(output):
            code = cli.main(['--cancel', '4', '--json'])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())['ok'])
        controller.cancel_job.assert_called_once_with('chosen', 4)

    def test_invalid_arguments_with_json_are_machine_readable(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                code = cli.main(['--cancel', '4', '--status', '--json'])
            except SystemExit as exc:
                code = exc.code
        self.assertNotEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_missing_printer_info_is_an_error(self):
        controller = Mock()
        controller.get_printer_info.return_value = None
        output = io.StringIO()
        with patch.object(cli, '_controller', return_value=controller), contextlib.redirect_stdout(output):
            code = cli.cmd_status(True, 'missing')
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_depth_boundary_is_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a' / 'b' / 'c').mkdir(parents=True)
            (root / 'a' / 'b' / 'visible.pdf').write_text('x')
            (root / 'a' / 'b' / 'c' / 'hidden.pdf').write_text('x')
            with patch.object(cli, 'find_wechat_file_storage', return_value=str(root)), patch.object(cli, '_get_search_dirs', return_value=[]):
                files = cli.search_files('.pdf')
            self.assertEqual([Path(p).name for p in files], ['visible.pdf'])

    def test_print_uses_explicit_printer_and_allowed_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.pdf'
            path.write_text('x')
            controller = Mock()
            controller.print_file.return_value = True
            config = Mock()
            config.get.return_value = ['pdf']
            with patch.object(cli, '_controller', return_value=controller), patch.object(cli, '_config', return_value=config), patch.object(cli, '_printer', return_value='explicit') as printer, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--print', str(path), '--printer', 'explicit', '--json']), 0)
            controller.print_file.assert_called_once_with(str(path.resolve()), 'explicit')
            printer.assert_called_once_with('explicit')

    def test_disallowed_file_never_reaches_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.pdf'
            path.write_text('x')
            config = Mock()
            config.get.return_value = ['txt']
            with patch.object(cli, '_controller') as controller, patch.object(cli, '_config', return_value=config), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.cmd_print(str(path), True), 1)
            controller.assert_not_called()

    def test_bare_cwd_name_still_checks_configured_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cwd, storage = root / 'cwd', root / 'storage'
            cwd.mkdir()
            storage.mkdir()
            (cwd / 'report.pdf').write_text('old')
            (storage / 'report.pdf').write_text('new')
            previous = Path.cwd()
            try:
                os.chdir(cwd)
                with patch.object(cli, 'find_wechat_file_storage', return_value=str(storage)), patch.object(cli, '_get_search_dirs', return_value=[]):
                    with self.assertRaises(ValueError):
                        cli.find_file('report.pdf')
                    self.assertEqual(cli.find_file('./report.pdf'), str((cwd / 'report.pdf').resolve()))
            finally:
                os.chdir(previous)
