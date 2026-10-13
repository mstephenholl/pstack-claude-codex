"""smoke.py: reading what a headless Claude Code run prints."""

import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "smoke", "smoke.py")
spec = importlib.util.spec_from_file_location("smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

RESULT = {"type": "result", "subtype": "success", "session_id": "s1", "result": "the answer"}


class RunClaudeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_claude(self, output):
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        claude = os.path.join(bin_dir, "claude")
        with open(claude, "w") as f:
            f.write("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(output) + "\nEOF\n")
        os.chmod(claude, 0o755)
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            return smoke.run_claude("prompt", self.repo, io.StringIO())

    def test_reads_the_result_object(self):
        self.assertEqual(self.run_claude(RESULT), ("s1", "the answer"))

    def test_reads_the_result_when_verbose_prints_every_message(self):
        messages = [{"type": "system", "subtype": "init", "session_id": "s1"}, {"type": "assistant", "message": {"content": []}}, RESULT]
        self.assertEqual(self.run_claude(messages), ("s1", "the answer"))


if __name__ == "__main__":
    unittest.main()
