"""
Tests for logging behaviour (src/logging_config.py) and that key events
are emitted at the correct level with the expected fields.
"""
import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from src.logging_config import (
    LOGGER_NAME,
    ScanAdapter,
    _SyslogFormatter,
    configure_logging,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

class _CapturingHandler(logging.Handler):
    """Accumulates LogRecord objects for assertion."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]

    def levels(self) -> list[str]:
        return [r.levelname for r in self.records]


def _attach_handler(handler: logging.Handler) -> logging.Logger:
    """Attach a handler to the repo_scanner logger, bypassing configure_logging."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


# ── _SyslogFormatter ──────────────────────────────────────────────────────────

class TestSyslogFormatter(unittest.TestCase):

    def _format(self, level: int, msg: str) -> str:
        fmt = _SyslogFormatter()
        record = logging.LogRecord(
            name=LOGGER_NAME, level=level, pathname='', lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        return fmt.format(record)

    def test_contains_level_name(self):
        line = self._format(logging.WARNING, 'file_skipped_size path=/tmp/x')
        self.assertIn('WARNING', line)

    def test_contains_app_name(self):
        line = self._format(logging.INFO, 'scan_started')
        self.assertIn('repo-scanner[', line)

    def test_timestamp_is_utc_iso8601(self):
        line = self._format(logging.INFO, 'test')
        # Expect something like 2026-02-21T10:14:32.401Z at the start
        self.assertRegex(line, r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z')

    def test_message_preserved(self):
        line = self._format(logging.INFO, 'scan_completed violations=3')
        self.assertIn('scan_completed violations=3', line)


# ── ScanAdapter ───────────────────────────────────────────────────────────────

class TestScanAdapter(unittest.TestCase):

    def setUp(self):
        self.handler = _CapturingHandler()
        self.logger  = _attach_handler(self.handler)

    def test_scan_id_appended_to_message(self):
        adapter = ScanAdapter(self.logger, scan_id=42)
        adapter.info('scan_started source=git')
        self.assertEqual(len(self.handler.records), 1)
        self.assertIn('scan_id=42', self.handler.messages()[0])

    def test_level_passed_through(self):
        adapter = ScanAdapter(self.logger, scan_id=7)
        adapter.warning('file_skipped_size path=/tmp/big.sql')
        self.assertEqual(self.handler.levels()[0], 'WARNING')

    def test_different_scan_ids_are_independent(self):
        adapter_a = ScanAdapter(self.logger, scan_id=1)
        adapter_b = ScanAdapter(self.logger, scan_id=2)
        adapter_a.info('event_a')
        adapter_b.info('event_b')
        msgs = self.handler.messages()
        self.assertIn('scan_id=1', msgs[0])
        self.assertIn('scan_id=2', msgs[1])


# ── configure_logging ─────────────────────────────────────────────────────────

class TestConfigureLogging(unittest.TestCase):

    def _reset_logger(self):
        logger = logging.getLogger(LOGGER_NAME)
        logger.handlers.clear()

    def test_stdout_target(self):
        self._reset_logger()
        env = {'LOG_TARGET': 'stdout', 'LOG_LEVEL': 'DEBUG'}
        with patch.dict(os.environ, env):
            logger = configure_logging()
        self.assertTrue(logger.handlers)
        self._reset_logger()

    def test_stderr_target(self):
        self._reset_logger()
        env = {'LOG_TARGET': 'stderr', 'LOG_LEVEL': 'INFO'}
        with patch.dict(os.environ, env):
            logger = configure_logging()
        self.assertTrue(logger.handlers)
        self._reset_logger()

    def test_file_target_creates_file(self):
        self._reset_logger()
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, 'sub', 'scanner.log')
            env = {'LOG_TARGET': 'file', 'LOG_FILE': log_path}
            with patch.dict(os.environ, env):
                configure_logging()
            self.assertTrue(os.path.exists(log_path))
        self._reset_logger()

    def test_configure_logging_idempotent(self):
        """Calling configure_logging() twice must not add duplicate handlers."""
        self._reset_logger()
        env = {'LOG_TARGET': 'stdout'}
        with patch.dict(os.environ, env):
            configure_logging()
            handler_count = len(logging.getLogger(LOGGER_NAME).handlers)
            configure_logging()
        self.assertEqual(
            len(logging.getLogger(LOGGER_NAME).handlers), handler_count
        )
        self._reset_logger()

    def test_log_level_respected(self):
        self._reset_logger()
        handler = _CapturingHandler()
        env = {'LOG_TARGET': 'stdout', 'LOG_LEVEL': 'WARNING'}
        with patch.dict(os.environ, env):
            logger = configure_logging()
        # Swap the real handler for our capturing one
        logger.handlers = [handler]
        logger.debug('should_not_appear')
        logger.warning('should_appear')
        msgs = handler.messages()
        # logging_initialized is emitted during configure_logging at INFO —
        # that was handled by the original handler; only our new messages matter
        self.assertFalse(any('should_not_appear' in m for m in msgs))
        self.assertTrue(any('should_appear' in m for m in msgs))
        self._reset_logger()


# ── Integration: scanner emits expected events ────────────────────────────────

class TestScannerLogging(unittest.TestCase):
    """Verify that ProhibitedWordScanner emits the right log events."""

    def setUp(self):
        import yaml
        self.handler = _CapturingHandler()
        self.logger  = _attach_handler(self.handler)

        self.tmp = tempfile.mkdtemp()

        words_path = os.path.join(self.tmp, 'words.txt')
        with open(words_path, 'w') as f:
            f.write('secret\n')

        config = {
            'prohibited_words_file': words_path,
            'case_sensitive': False,
            'max_file_size_mb': 1,
        }
        self.config_path = os.path.join(self.tmp, 'config.yaml')
        with open(self.config_path, 'w') as f:
            yaml.dump(config, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        logging.getLogger(LOGGER_NAME).handlers.clear()

    def _make_scanner(self):
        from src.scanner import ProhibitedWordScanner
        return ProhibitedWordScanner(self.config_path, logger=self.logger)

    def test_file_skipped_size_logged_as_warning(self):
        # Create a file that exceeds the 1 MB limit
        big = os.path.join(self.tmp, 'big.txt')
        with open(big, 'wb') as f:
            f.write(b'x' * (2 * 1024 * 1024))

        scanner = self._make_scanner()
        scanner.scan_directory(self.tmp)
        scanner.cleanup()

        warnings = [r for r in self.handler.records if r.levelname == 'WARNING']
        skipped  = [r for r in warnings if 'file_skipped_size' in r.getMessage()]
        self.assertTrue(skipped, 'Expected a file_skipped_size WARNING')

    def test_match_found_logged_at_debug(self):
        src = os.path.join(self.tmp, 'code.py')
        with open(src, 'w') as f:
            f.write('password = "secret"\n')

        scanner = self._make_scanner()
        scanner.scan_directory(self.tmp)
        scanner.cleanup()

        debug_msgs = [
            r.getMessage() for r in self.handler.records
            if r.levelname == 'DEBUG' and 'match_found' in r.getMessage()
        ]
        self.assertTrue(debug_msgs, 'Expected match_found DEBUG entries')

    def test_archive_extracting_logged_at_info(self):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('inner.txt', 'no secrets here')
        archive = os.path.join(self.tmp, 'pkg.zip')
        with open(archive, 'wb') as f:
            f.write(buf.getvalue())

        scanner = self._make_scanner()
        scanner.scan_directory(self.tmp)
        scanner.cleanup()

        info_msgs = [
            r.getMessage() for r in self.handler.records
            if r.levelname == 'INFO' and 'archive_extracting' in r.getMessage()
        ]
        self.assertTrue(info_msgs, 'Expected archive_extracting INFO entry')


if __name__ == '__main__':
    unittest.main()
