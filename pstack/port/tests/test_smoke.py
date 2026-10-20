"""smoke.py: reading what a headless Claude Code run prints."""

import importlib.util
import io
import json
import os
import shutil
import tempfile
import types
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


class AnswerCheckTest(unittest.TestCase):
    def finds_stale_cache_bug(self, answer):
        return smoke.SCENARIOS["interrogate"]["checks"][-1](types.SimpleNamespace(answer=answer))[0]

    def test_interrogate_accepts_any_wording_of_the_stale_cache_bug(self):
        for answer in (
            "`update_user` never clears the cache entry. Later reads through the cache still return the old record.",
            "Reads after a write return stale data.",
            "The cache is never invalidated on write.",
        ):
            self.assertTrue(self.finds_stale_cache_bug(answer), answer)

    def test_interrogate_rejects_an_answer_that_misses_the_bug(self):
        self.assertFalse(self.finds_stale_cache_bug("A CLI typo exits 0, and a failed save empties users.json."))


class RunCodexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_lets_codexs_reviewer_answer_escalations(self):
        bin_dir, args = os.path.join(self.tmp, "bin"), os.path.join(self.tmp, "args")
        os.makedirs(bin_dir)
        codex = os.path.join(bin_dir, "codex")
        with open(codex, "w") as f:
            f.write(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {args}\n")
        os.chmod(codex, 0o755)
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
            smoke.run_codex("prompt", self.repo, io.StringIO())
        argv = open(args).read().splitlines()
        self.assertIn("--approve-for-me", argv)
        # codex exec refuses --approve-for-me alongside a sandbox flag.
        self.assertFalse({"-s", "--sandbox"} & set(argv), argv)


class ForgetTrustTest(unittest.TestCase):
    OUT = "/tmp/pstack-smoke-1"

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config = os.path.join(self.tmp, "config.toml")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_removes_only_this_runs_entries(self):
        with open(self.config, "w") as f:
            f.write(
                'model = "gpt-6-astra"\n\n'
                '[projects."/home/me/code"]\ntrust_level = "trusted"\n\n'
                f'[projects."{self.OUT}/how-codex/repo"]\ntrust_level = "trusted"\n\n'
                f'[projects."{self.OUT}-2/how-codex/repo"]\ntrust_level = "trusted"\n\n'
                f'[projects."{self.OUT}/why-codex/repo"]\ntrust_level = "trusted"\n\n'
                '[features]\nfast = true\n'
            )
        os.chmod(self.config, 0o600)
        smoke.forget_trust(self.OUT, self.config)
        self.assertEqual(open(self.config).read(), (
            'model = "gpt-6-astra"\n\n'
            '[projects."/home/me/code"]\ntrust_level = "trusted"\n\n'
            f'[projects."{self.OUT}-2/how-codex/repo"]\ntrust_level = "trusted"\n\n'
            '[features]\nfast = true\n'
        ))
        self.assertEqual(os.stat(self.config).st_mode & 0o777, 0o600)

    def test_leaves_a_missing_config_missing(self):
        smoke.forget_trust(self.OUT, self.config)
        self.assertFalse(os.path.exists(self.config))


if __name__ == "__main__":
    unittest.main()
