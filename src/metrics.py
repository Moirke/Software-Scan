"""
In-process metrics for the Repository Scanner web service.

All counters live in memory and reset when the process restarts.  In a
multi-worker or multi-instance deployment each worker / instance maintains
its own counters — the /api/metrics endpoint reflects a single process.

If you need cluster-wide aggregation, scrape /api/metrics from each instance
and push to a time-series store (Prometheus, CloudWatch, Datadog, etc.).
"""
import threading
from collections import defaultdict
from datetime import date, datetime, timezone

_lock          = threading.Lock()
_server_start  = datetime.now(timezone.utc).isoformat()

# Simple integer counters
_page_views     = 0
_scans_started  = 0
_scans_completed = 0
_scans_failed   = 0
_total_violations = 0

# Bucketed counters
_source_counts: dict = defaultdict(int)   # {'git': n, 'zip': n, ...}
_scans_by_date: dict = defaultdict(int)   # {'2026-02-21': n, ...}

# Duration samples — bounded to the last 1 000 scans
_durations_ms: list = []


# ── Write helpers (called from web.py) ───────────────────────────────────────

def record_page_view() -> None:
    global _page_views
    with _lock:
        _page_views += 1


def record_scan_started(source_type: str) -> None:
    global _scans_started
    today = date.today().isoformat()
    with _lock:
        _scans_started += 1
        _source_counts[source_type] += 1
        _scans_by_date[today] += 1


def record_scan_completed(duration_ms: int, violations: int) -> None:
    global _scans_completed, _total_violations
    with _lock:
        _scans_completed += 1
        _total_violations += violations
        _durations_ms.append(duration_ms)
        if len(_durations_ms) > 1000:
            del _durations_ms[:-1000]


def record_scan_failed() -> None:
    global _scans_failed
    with _lock:
        _scans_failed += 1


# ── Read helper ───────────────────────────────────────────────────────────────

def get_snapshot() -> dict:
    """Return a point-in-time copy of all metrics — safe to serialise as JSON."""
    today = date.today().isoformat()
    with _lock:
        started   = _scans_started
        completed = _scans_completed
        failed    = _scans_failed

        success_rate = round(completed / started * 100, 1) if started else 0.0
        avg_ms       = round(sum(_durations_ms) / len(_durations_ms)) if _durations_ms else 0

        # Last 7 distinct dates that have scan data, in ascending order
        last_7 = {d: _scans_by_date[d] for d in sorted(_scans_by_date)[-7:]}

        return {
            'server_start':           _server_start,
            'page_views':             _page_views,
            'scans_today':            _scans_by_date.get(today, 0),
            'scans_started':          started,
            'scans_completed':        completed,
            'scans_failed':           failed,
            'success_rate_pct':       success_rate,
            'avg_scan_duration_ms':   avg_ms,
            'total_violations_found': _total_violations,
            'source_type_counts':     dict(_source_counts),
            'scans_last_7_days':      last_7,
        }
