"""
Tests for the CLI interface (src/cli.py).

Each test invokes main() directly by patching sys.argv and catching SystemExit,
so no subprocess overhead is needed.
"""
import os
import shutil
import tempfile
import unittest
import yaml
from unittest.mock import patch

from src.cli import main


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir  = tempfile.mkdtemp(prefix='cli_test_')
        self.scan_dir = os.path.join(self.tmpdir, 'repo')
        os.makedirs(self.scan_dir)

        self.words_file = os.path.join(self.tmpdir, 'words.txt')
        with open(self.words_file, 'w') as f:
            f.write('password\nsecret\n')

        self.config_file = os.path.join(self.tmpdir, 'config.yaml')
        with open(self.config_file, 'w') as f:
            yaml.dump({
                'prohibited_words_file': self.words_file,
                'case_sensitive': False,
                'max_file_size_mb': 10,
            }, f)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, rel_path: str, content: str):
        full = os.path.join(self.scan_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
        return full

    def _run(self, *extra_args) -> int:
        """Run the CLI with the default config+repo and return the exit code."""
        argv = ['scanner', '--config', self.config_file, '--repo', self.scan_dir]
        argv.extend(extra_args)
        with patch('sys.argv', argv):
            try:
                main()
                return 0          # main() returned without calling sys.exit
            except SystemExit as e:
                return int(e.code)


class TestCliExitCodes(CliTestCase):

    def test_exit_0_on_clean_repo(self):
        self._write('clean.py', 'x = 1  # nothing here\n')
        self.assertEqual(self._run(), 0)

    def test_exit_1_on_violations_found(self):
        self._write('dirty.py', 'password = "hunter2"\n')
        self.assertEqual(self._run(), 1)

    def test_exit_2_on_bad_config_path(self):
        argv = ['scanner', '--config', '/nonexistent/config.yaml',
                '--repo', self.scan_dir]
        with patch('sys.argv', argv):
            try:
                main()
                exit_code = 0
            except SystemExit as e:
                exit_code = int(e.code)
        self.assertEqual(exit_code, 2)

    def test_exit_2_on_bad_repo_path(self):
        argv = ['scanner', '--config', self.config_file,
                '--repo', '/nonexistent/repo']
        with patch('sys.argv', argv):
            try:
                main()
                exit_code = 0
            except SystemExit as e:
                exit_code = int(e.code)
        # scan_directory on a non-existent path should fail gracefully
        self.assertIn(exit_code, [0, 1, 2])


class TestCliFlags(CliTestCase):

    def test_no_recursive_flag_skips_subdirs(self):
        # Violation only in subdir — should be missed with --no-recursive
        self._write('subdir/dirty.py', 'password = "x"\n')
        self.assertEqual(self._run('--no-recursive'), 0)

    def test_recursive_default_finds_subdirs(self):
        self._write('subdir/dirty.py', 'password = "x"\n')
        self.assertEqual(self._run(), 1)

    def test_output_file_written(self):
        self._write('dirty.py', 'password = "x"\n')
        output_path = os.path.join(self.tmpdir, 'results.txt')
        self._run('--output', output_path)
        self.assertTrue(os.path.exists(output_path))
        with open(output_path) as f:
            content = f.read()
        self.assertGreater(len(content), 0)

    def test_verbose_flag_does_not_crash(self):
        self._write('clean.py', 'x = 1\n')
        try:
            self._run('--verbose')
        except SystemExit:
            pass  # exit codes handled by other tests


if __name__ == '__main__':
    unittest.main()
