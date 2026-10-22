import os
import tempfile
import unittest
from unittest.mock import patch

from printer import controller


class ControllerTests(unittest.TestCase):
    def test_changing_queue_does_not_skip_jobs_between_offset_pages(self):
        live = [{'JobId': n} for n in range(235)]
        calls = []
        def enum(handle, start, count, level):
            result = live[start:start + count]
            if not calls:
                del live[:20]
            calls.append(start)
            return result
        with patch.object(controller.win32print, 'OpenPrinter', return_value=123), \
                patch.object(controller.win32print, 'ClosePrinter'), \
                patch.object(controller.win32print, 'EnumJobs', side_effect=enum):
            observed = controller.PrintController.get_jobs('A')
        self.assertTrue({j['JobId'] for j in live}.issubset({j['JobId'] for j in observed}))

    def test_handles_close_when_control_fails(self):
        for method in ('pause_job', 'resume_job', 'cancel_job', 'restart_job',
                       'purge_queue', 'pause_printer', 'resume_printer'):
            with self.subTest(method=method), \
                    patch.object(controller.win32print, 'OpenPrinter', return_value=123), \
                    patch.object(controller.win32print, 'ClosePrinter') as close, \
                    patch.object(controller.win32print, 'SetJob', side_effect=RuntimeError('fault')), \
                    patch.object(controller.win32print, 'SetPrinter', side_effect=RuntimeError('fault')):
                args = ('A', 1) if method.endswith('_job') else ('A',)
                self.assertFalse(getattr(controller.PrintController, method)(*args))
                close.assert_called_once_with(123)

    def test_info_failure_closes_handle(self):
        with patch.object(controller.win32print, 'OpenPrinter', return_value=123), \
                patch.object(controller.win32print, 'ClosePrinter') as close, \
                patch.object(controller.win32print, 'GetPrinter', side_effect=RuntimeError('fault')):
            self.assertIsNone(controller.PrintController.get_printer_info('A'))
            close.assert_called_once_with(123)

    def test_get_jobs_reads_full_snapshot_and_closes(self):
        rows = [{'JobId': n} for n in range(235)]
        def enum(handle, start, count, level):
            return rows[start:start + count]
        with patch.object(controller.win32print, 'OpenPrinter', return_value=123), \
                patch.object(controller.win32print, 'ClosePrinter') as close, \
                patch.object(controller.win32print, 'EnumJobs', side_effect=enum) as enumerate_call:
            self.assertEqual(controller.PrintController.get_jobs('A'), rows)
            close.assert_called_once_with(123)
            enumerate_call.assert_called_once_with(123, 0, 0x7FFFFFFF, 2)

    def test_snapshot_failure_propagates_and_closes(self):
        with patch.object(controller.win32print, 'OpenPrinter', return_value=123), \
                patch.object(controller.win32print, 'ClosePrinter') as close, \
                patch.object(controller.win32print, 'EnumJobs', side_effect=RuntimeError('snapshot unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'snapshot unavailable'):
                controller.PrintController.get_jobs('A')
            close.assert_called_once_with(123)

    def test_printto_checks_result_and_exact_target(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'sample.txt')
            with open(path, 'w', encoding='utf-8') as stream:
                stream.write('test')
            for result in (0, 31, 32, 33):
                with self.subTest(result=result), \
                        patch.object(controller.win32api, 'ShellExecute', return_value=result) as shell:
                    self.assertEqual(controller.PrintController.print_file(path, 'Office Printer'), result > 32)
                    args = shell.call_args.args
                    self.assertEqual(args[1:4], ('printto', path, '"Office Printer"'))

    def test_directory_and_invalid_target_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(controller.win32api, 'ShellExecute') as shell:
            self.assertFalse(controller.PrintController.print_file(temp, 'A'))
            self.assertFalse(controller.PrintController.print_file(__file__, 'bad"printer'))
            self.assertFalse(controller.PrintController.print_file(__file__, ''))
            shell.assert_not_called()

    def test_explicit_virtual_printers_can_be_listed(self):
        with patch.object(controller.win32print, 'EnumPrinters', return_value=[
            {'pPrinterName': 'Microsoft Print to PDF'}, {'pPrinterName': 'Office'}
        ]):
            names = [p['name'] for p in controller.PrintController.get_all_printers(include_virtual=True)]
            self.assertIn('Microsoft Print to PDF', names)

    def test_print_request_initializes_and_releases_thread_com_on_failure(self):
        with patch.object(controller, 'pythoncom', create=True) as com, \
                patch.object(controller.win32api, 'ShellExecute', side_effect=RuntimeError('no association')):
            self.assertFalse(controller.PrintController.print_file(__file__, 'A'))
            com.CoInitialize.assert_called_once_with()
            com.CoUninitialize.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
