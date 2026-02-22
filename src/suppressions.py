"""
Finding suppression list — load, save, apply, and manage scan suppressions.

Suppressions let operators mark a specific finding (file + line + word) as a
known false positive so it is silently excluded from future scan results.

File format: config/suppressions.yaml (auto-detected; no config key required).
"""
import hashlib
import os
import tempfile

import yaml


def make_fingerprint(relative_file: str, line_content: str, prohibited_word: str) -> str:
    """Return the first 16 hex chars of SHA-256(file\\x00line\\x00word)."""
    raw = f"{relative_file}\x00{line_content.strip()}\x00{prohibited_word}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_suppressions(path: str) -> dict:
    """
    Load suppressions from *path*.

    Returns a dict keyed by fingerprint.  Returns {} if the file does not
    exist, is empty, or contains no suppressions list.
    """
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or not isinstance(data, dict):
        return {}
    entries = data.get("suppressions") or []
    result = {}
    for entry in entries:
        fp = entry.get("id")
        if fp:
            result[fp] = entry
    return result


def save_suppressions(path: str, suppressions: dict) -> None:
    """Write *suppressions* (keyed by fingerprint) to *path* atomically."""
    entries = list(suppressions.values())
    data = {"suppressions": entries}
    dir_ = os.path.dirname(os.path.abspath(path))
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_suppressions(
    results: list, repo_root: str, suppressions: dict
) -> tuple:
    """
    Filter *results*, removing any finding whose fingerprint is in *suppressions*.

    ``relative_file`` is computed as the path of each result relative to
    *repo_root*.  Returns ``(filtered_results, suppressed_count)``.
    """
    if not suppressions:
        return results, 0

    kept = []
    suppressed = 0
    for r in results:
        try:
            rel = os.path.relpath(r["file"], repo_root)
        except ValueError:
            rel = r["file"]
        fp = make_fingerprint(rel, r.get("line_content", ""), r.get("prohibited_word", ""))
        if fp in suppressions:
            suppressed += 1
        else:
            kept.append(r)
    return kept, suppressed


def add_suppression(
    path: str,
    relative_file: str,
    line_content: str,
    prohibited_word: str,
    reason: str = "",
) -> str:
    """
    Add a suppression entry to *path* and return the fingerprint.

    Creates the file if it does not exist.  If the fingerprint is already
    present the existing entry is left unchanged.
    """
    from datetime import datetime, timezone

    fp = make_fingerprint(relative_file, line_content.strip(), prohibited_word)
    suppressions = load_suppressions(path)
    if fp not in suppressions:
        entry: dict = {
            "id": fp,
            "file": relative_file,
            "line_content": line_content.strip(),
            "prohibited_word": prohibited_word,
            "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if reason:
            entry["reason"] = reason
        suppressions[fp] = entry
        save_suppressions(path, suppressions)
    return fp


def remove_suppression(path: str, fingerprint: str) -> bool:
    """
    Remove the suppression with *fingerprint* from *path*.

    Returns True if the entry was found and removed, False otherwise.
    """
    suppressions = load_suppressions(path)
    if fingerprint not in suppressions:
        return False
    del suppressions[fingerprint]
    save_suppressions(path, suppressions)
    return True
