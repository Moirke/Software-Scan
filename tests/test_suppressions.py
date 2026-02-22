"""
Unit tests for src/suppressions.py
"""
import os
import tempfile
import unittest

from src.suppressions import (
    add_suppression,
    apply_suppressions,
    load_suppressions,
    make_fingerprint,
    remove_suppression,
    save_suppressions,
)


class TestMakeFingerprint(unittest.TestCase):

    def test_make_fingerprint_stable(self):
        """Same inputs always produce the same fingerprint."""
        fp1 = make_fingerprint("src/config.py", "password = 'ci'", "password")
        fp2 = make_fingerprint("src/config.py", "password = 'ci'", "password")
        self.assertEqual(fp1, fp2)

    def test_make_fingerprint_unique(self):
        """Different inputs produce different fingerprints."""
        fp1 = make_fingerprint("src/config.py", "password = 'ci'", "password")
        fp2 = make_fingerprint("src/other.py",  "password = 'ci'", "password")
        fp3 = make_fingerprint("src/config.py", "secret = 'x'",   "secret")
        self.assertNotEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp3)
        self.assertNotEqual(fp2, fp3)

    def test_fingerprint_length(self):
        """Fingerprint is exactly 16 hex characters."""
        fp = make_fingerprint("a", "b", "c")
        self.assertEqual(len(fp), 16)
        self.assertRegex(fp, r'^[0-9a-f]{16}$')

    def test_fingerprint_strips_line_content(self):
        """Leading/trailing whitespace in line_content is ignored."""
        fp1 = make_fingerprint("f", "  line  ", "word")
        fp2 = make_fingerprint("f", "line",     "word")
        self.assertEqual(fp1, fp2)


class TestLoadSuppressions(unittest.TestCase):

    def test_load_suppressions_no_file(self):
        """Missing file returns empty dict."""
        result = load_suppressions("/nonexistent/path/suppressions.yaml")
        self.assertEqual(result, {})

    def test_load_suppressions_empty(self):
        """Empty / whitespace-only YAML returns empty dict."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("   \n")
            path = f.name
        try:
            result = load_suppressions(path)
            self.assertEqual(result, {})
        finally:
            os.unlink(path)

    def test_load_suppressions_entries(self):
        """Parses entries correctly and keys by fingerprint."""
        fp = make_fingerprint("src/config.py", "password = 'ci'", "password")
        content = f"""suppressions:
  - id: "{fp}"
    file: "src/config.py"
    line_content: "password = 'ci'"
    prohibited_word: "password"
    reason: "CI test credential"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(content)
            path = f.name
        try:
            result = load_suppressions(path)
            self.assertIn(fp, result)
            self.assertEqual(result[fp]["file"], "src/config.py")
            self.assertEqual(result[fp]["prohibited_word"], "password")
            self.assertEqual(result[fp]["reason"], "CI test credential")
        finally:
            os.unlink(path)


class TestApplySuppressions(unittest.TestCase):

    def _make_result(self, filepath, line_content, word):
        return {
            "file": filepath,
            "line_number": 1,
            "line_content": line_content,
            "prohibited_word": word,
            "match_type": "exact",
        }

    def test_apply_suppressions_removes_match(self):
        """Matching finding is filtered out; suppressed_count == 1."""
        repo_root = "/repo"
        rel = "src/config.py"
        line = "password = 'ci'"
        word = "password"
        fp = make_fingerprint(rel, line, word)
        suppressions = {fp: {"id": fp, "file": rel, "line_content": line, "prohibited_word": word}}
        results = [self._make_result(os.path.join(repo_root, rel), line, word)]

        kept, count = apply_suppressions(results, repo_root, suppressions)
        self.assertEqual(kept, [])
        self.assertEqual(count, 1)

    def test_apply_suppressions_no_match(self):
        """No suppression matches → full list returned, count == 0."""
        repo_root = "/repo"
        results = [self._make_result("/repo/src/config.py", "secret = 'x'", "secret")]
        kept, count = apply_suppressions(results, repo_root, {})
        self.assertEqual(len(kept), 1)
        self.assertEqual(count, 0)

    def test_apply_suppressions_partial_match(self):
        """Only the matching entry is removed; unmatched findings are kept."""
        repo_root = "/repo"
        rel1 = "src/a.py"
        rel2 = "src/b.py"
        line = "password = 'ci'"
        word = "password"
        fp1 = make_fingerprint(rel1, line, word)
        suppressions = {fp1: {"id": fp1, "file": rel1, "line_content": line, "prohibited_word": word}}
        results = [
            self._make_result(os.path.join(repo_root, rel1), line, word),
            self._make_result(os.path.join(repo_root, rel2), "secret = 'x'", "secret"),
        ]
        kept, count = apply_suppressions(results, repo_root, suppressions)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["file"], os.path.join(repo_root, rel2))
        self.assertEqual(count, 1)


class TestAddRemoveSuppressions(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "suppressions.yaml")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_suppression_creates_file(self):
        """add_suppression creates the file if it does not exist."""
        self.assertFalse(os.path.exists(self.path))
        fp = add_suppression(self.path, "src/a.py", "password = 'x'", "password")
        self.assertTrue(os.path.exists(self.path))
        loaded = load_suppressions(self.path)
        self.assertIn(fp, loaded)

    def test_add_suppression_appends(self):
        """Subsequent adds accumulate without overwriting previous entries."""
        fp1 = add_suppression(self.path, "src/a.py", "password = 'x'", "password")
        fp2 = add_suppression(self.path, "src/b.py", "secret = 'y'",   "secret")
        loaded = load_suppressions(self.path)
        self.assertIn(fp1, loaded)
        self.assertIn(fp2, loaded)
        self.assertEqual(len(loaded), 2)

    def test_add_suppression_idempotent(self):
        """Adding the same entry twice does not create a duplicate."""
        fp1 = add_suppression(self.path, "src/a.py", "password = 'x'", "password")
        fp2 = add_suppression(self.path, "src/a.py", "password = 'x'", "password")
        self.assertEqual(fp1, fp2)
        loaded = load_suppressions(self.path)
        self.assertEqual(len(loaded), 1)

    def test_remove_suppression_found(self):
        """remove_suppression removes the entry and returns True."""
        fp = add_suppression(self.path, "src/a.py", "password = 'x'", "password")
        result = remove_suppression(self.path, fp)
        self.assertTrue(result)
        loaded = load_suppressions(self.path)
        self.assertNotIn(fp, loaded)

    def test_remove_suppression_not_found(self):
        """remove_suppression returns False when fingerprint is absent."""
        result = remove_suppression(self.path, "deadbeefdeadbeef")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
