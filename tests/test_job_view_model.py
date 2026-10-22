import unittest

from gui.job_view_model import JobViewModel


class JobViewModelTests(unittest.TestCase):
    def job(self, record, status, **extra):
        return dict(job_id=7, record_id=record, printer_name='A', status=status, **extra)

    def test_identity_separates_printers_and_reused_job_ids(self):
        model = JobViewModel('A')
        for job in (self.job(1, 'printing'), self.job(2, 'sent'),
                    dict(self.job(1, 'offline'), printer_name='B')):
            model.upsert(job)
        self.assertEqual(len(model.jobs), 3)

    def test_counts_and_clear_preserve_recoverable_states(self):
        model = JobViewModel('A')
        statuses = ('spooling', 'sent', 'cancelling', 'paperout', 'offline',
                    'error', 'paused', 'printed', 'cancelled', 'removed')
        for record, status in enumerate(statuses):
            model.upsert(self.job(record, status))
        self.assertEqual(model.counts(), dict(active=3, attention=4, finished=3, total=10))
        self.assertEqual(model.clear_finished(), 3)
        self.assertEqual(len(model.jobs), 7)

    def test_filter_combines_casefolded_document_owner_and_status(self):
        model = JobViewModel('A')
        first = model.upsert(self.job(1, 'paperout', document='季度报告.PDF', owner='Alice'))
        model.upsert(self.job(2, 'printed', document='季度报告.PDF', owner='Bob'))
        self.assertEqual([key for key, _ in model.visible('ALIce', 'attention')], [first])
        self.assertEqual(len(model.visible('报告', 'finished')), 1)
        self.assertEqual(model.visible('不存在', 'all'), [])

    def test_partial_update_keeps_fields_and_recovery_reappears(self):
        model = JobViewModel('A')
        key = model.upsert(self.job(1, 'offline', document='长文档名称.pdf', owner='测试'))
        model.upsert(self.job(1, 'printing'))
        self.assertEqual(model.jobs[key]['document'], '长文档名称.pdf')
        self.assertEqual(model.visible('', 'attention'), [])
        self.assertEqual([item[0] for item in model.visible('', 'active')], [key])

    def test_actions_respect_paused_terminal_and_cancelling_jobs(self):
        self.assertEqual(JobViewModel.actions_for('paused'), {'resume', 'restart', 'cancel'})
        for status in ('printed', 'removed', 'cancelled', 'cancelling'):
            self.assertEqual(JobViewModel.actions_for(status), set())
        self.assertIn('pause', JobViewModel.actions_for('sent'))

    def test_missing_record_identity_falls_back_to_distinct_job_ids(self):
        model = JobViewModel('A')
        model.upsert(dict(job_id=1, record_id=None, status='printing'))
        model.upsert(dict(job_id=2, record_id=None, status='printing'))
        self.assertEqual(len(model.jobs), 2)


if __name__ == '__main__':
    unittest.main()
