"""
Logging configuration for Repository Scanner.

Reads environment variables to determine target and level, then wires up
a syslog-compatible formatter.  Call configure_logging() once at process
startup before importing any other application module.

Environment variables
---------------------
LOG_LEVEL      Minimum level to emit (DEBUG/INFO/WARNING/ERROR/CRITICAL).
               Default: INFO
LOG_TARGET     Output sink: file | stdout | stderr | syslog.
               Default: file
LOG_FILE       Path to log file when LOG_TARGET=file.
               Default: /var/log/repo-scanner/scanner.log
SYSLOG_ADDRESS Unix socket path or host:port when LOG_TARGET=syslog.
               Default: /dev/log
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import socket
import sys
from datetime import datetime, timezone


# ── Constants ─────────────────────────────────────────────────────────────────

LOGGER_NAME      = 'repo_scanner'
DEFAULT_LEVEL    = 'INFO'
DEFAULT_TARGET   = 'file'
DEFAULT_LOG_FILE = '/var/log/repo-scanner/scanner.log'
DEFAULT_SYSLOG   = '/dev/log'


# ── Formatter ─────────────────────────────────────────────────────────────────

class _SyslogFormatter(logging.Formatter):
    """
    Emits lines in the form:
        <ISO8601-UTC> <hostname> repo-scanner[<pid>]: <LEVEL> <message>
    """

    _hostname = socket.gethostname()

    def formatTime(self, record: logging.LogRecord, datefmt=None) -> str:  # noqa: N802
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z'

    def format(self, record: logging.LogRecord) -> str:
        ts      = self.formatTime(record)
        level   = record.levelname
        message = record.getMessage()
        if record.exc_info:
            message += '\n' + self.formatException(record.exc_info)
        return (
            f'{ts} {self._hostname} repo-scanner[{os.getpid()}]: '
            f'{level} {message}'
        )


# ── ScanAdapter ───────────────────────────────────────────────────────────────

class ScanAdapter(logging.LoggerAdapter):
    """
    Wraps a logger and prepends scan_id=<N> to every message so all lines
    for a single scan can be filtered from the stream with a simple grep.

    Usage
    -----
        scan_log = ScanAdapter(logging.getLogger(LOGGER_NAME), scan_id=17)
        scan_log.info('scan_started source=%s target=%s', source, target)
        # → INFO scan_started scan_id=17 source=git target=github.com/org/repo
    """

    def __init__(self, logger: logging.Logger, scan_id: int | str):
        super().__init__(logger, {})
        self._scan_id = scan_id

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        return f'{msg} scan_id={self._scan_id}', kwargs


# ── configure_logging ─────────────────────────────────────────────────────────

def configure_logging() -> logging.Logger:
    """
    Configure the root repo_scanner logger from environment variables.

    Returns the configured logger.  Safe to call more than once (subsequent
    calls are no-ops if handlers are already attached).
    """
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger  # already configured

    level_name = os.environ.get('LOG_LEVEL', DEFAULT_LEVEL).upper()
    level      = getattr(logging, level_name, logging.INFO)
    target     = os.environ.get('LOG_TARGET', DEFAULT_TARGET).lower()

    logger.setLevel(level)
    formatter = _SyslogFormatter()

    if target == 'syslog':
        address = os.environ.get('SYSLOG_ADDRESS', DEFAULT_SYSLOG)
        # address may be a path (/dev/log) or host:port string
        if ':' in address and not address.startswith('/'):
            host, port_str = address.rsplit(':', 1)
            address = (host, int(port_str))
        handler = logging.handlers.SysLogHandler(
            address=address,
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
        )

    elif target == 'stdout':
        handler = logging.StreamHandler(sys.stdout)

    elif target == 'stderr':
        handler = logging.StreamHandler(sys.stderr)

    else:
        # Default: file — fall back to stdout if the path is not writable
        log_path = os.environ.get('LOG_FILE', DEFAULT_LOG_FILE)
        log_dir  = os.path.dirname(log_path)
        handler  = None
        try:
            os.makedirs(log_dir, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,   # 10 MB per file
                backupCount=5,
                encoding='utf-8',
            )
        except OSError as exc:
            sys.stderr.write(
                f'WARNING log_file_unavailable path="{log_path}" error="{exc}" '
                f'-- falling back to stdout\n'
            )
            handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False

    logger.info(
        'logging_initialized level=%s target=%s', level_name, target
    )
    return logger
