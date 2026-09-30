import logging
import threading
from unittest import TestCase
from .diagnostics import LOG, phase, log_failure


class DiagnosticsTests(TestCase):
    def test_waiting_stage_emits_heartbeat_and_stops_after_exit(self):
        received = threading.Event()
        class Watch(logging.Handler):
            def emit(self, record):
                if 'still_running' in record.getMessage():
                    received.set()
        handler = Watch()
        LOG.addHandler(handler)
        try:
            with self.assertLogs(LOG, level='INFO') as output:
                # assertLogs replaces handlers; attach the watcher inside it.
                LOG.addHandler(handler)
                with phase('job-test', 'write_snapshot', interval=0.01):
                    self.assertTrue(received.wait(2))
                self.assertIn('finished', output.output[-1])
        finally:
            LOG.removeHandler(handler)
        self.assertFalse(any(t.name == 'collection-diagnostics' for t in threading.enumerate()))

    def test_exception_diagnostics_do_not_expose_message_or_source_line(self):
        with self.assertLogs(LOG, level='INFO') as output:
            try:
                with phase('job-test', 'write_snapshot'):
                    raise ValueError('password=never-log-this')
            except ValueError as exc:
                log_failure('job-test', exc)
        combined = '\n'.join(output.output)
        self.assertIn('interrupted', combined)
        self.assertIn('exception=ValueError', combined)
        self.assertNotIn('never-log-this', combined)
        self.assertNotIn(' finished ', combined)
