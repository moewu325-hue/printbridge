from contextlib import nullcontext
import unittest
from unittest.mock import patch

import win32print
from panel.printer_backend import PrinterBackend, describe_job


class PanelBackendTests(unittest.TestCase):
    def setUp(self):
        self.enum = patch('panel.printer_backend.win32print.EnumPrinters', return_value=[{
            'pPrinterName': 'A', 'pPortName': 'USB001', 'Attributes': 1024, 'cJobs': 8}]).start()
        patch('panel.printer_backend.win32print.GetDefaultPrinter', return_value='A').start()
        patch('panel.printer_backend.printer_handle', side_effect=lambda name: nullcontext(name)).start()
        patch('panel.printer_backend.win32print.GetPrinter', return_value={
            'Status': 0, 'Attributes': 0, 'cJobs': 0}).start()
        self.jobs = patch('panel.printer_backend.enumerate_jobs', return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_complete_is_sent_and_printed_is_success(self):
        self.assertEqual(describe_job(win32print.JOB_STATUS_COMPLETE), ('已发送至打印机', 'info'))
        self.assertEqual(describe_job(win32print.JOB_STATUS_PRINTED), ('已打印', 'ok'))
        self.assertEqual(describe_job(win32print.JOB_STATUS_DELETING), ('正在取消', 'warn'))

    def test_new_zero_values_replace_enumeration_values(self):
        printer = PrinterBackend().snapshot().printers[0]
        self.assertEqual((printer.attributes, printer.job_count, printer.offline), (0, 0, False))

    def test_queue_failure_marks_cached_jobs_stale(self):
        backend = PrinterBackend()
        self.jobs.return_value = [{'JobId': 7, 'pDocument': 'report.pdf', 'Status': 0}]
        first = backend.snapshot()
        self.jobs.side_effect = OSError('queue unavailable')
        failed = backend.snapshot()
        self.assertEqual(failed.jobs, first.jobs)
        self.assertFalse(failed.spooler_ok)
        self.assertFalse(failed.jobs_fresh)
        self.assertIn('队列', failed.error)
        self.assertEqual(first.jobs[0].printer_name, 'A')


if __name__ == '__main__':
    unittest.main()
