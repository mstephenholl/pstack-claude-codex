"""sync-upstream.sh and its workflow: who the sync acts as, and what that changes about the pull request it opens."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

PORT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PORT))
SYNC_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "pstack-upstream-sync.yml")
TOKEN = r"\$\{\{ secrets\.PSTACK_SYNC_TOKEN \|\| github\.token \}\}"

FAKE_GH = """#!{python}
import json, os, sys
argv = sys.argv[1:]
body = None
if "--body-file" in argv:
    with open(argv[argv.index("--body-file") + 1]) as f:
        body = f.read()
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps([argv, body]) + "\\n")
"""


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)


class SyncWorkflowTest(unittest.TestCase):
    def setUp(self):
        with open(SYNC_WORKFLOW) as f:
            self.text = f.read()

    def test_it_pushes_and_opens_the_pull_request_with_the_sync_token_or_else_the_actions_token(self):
        self.assertRegex(self.text, re.compile(r"^          token: " + TOKEN + r"$", re.M))
        self.assertRegex(self.text, re.compile(
            r"^        env:\n          GH_TOKEN: " + TOKEN + r"\n"
            r"          AS_ACTIONS_BOT: \$\{\{ secrets\.PSTACK_SYNC_TOKEN == '' \}\}\n"
            r"        run: pstack/port/sync-upstream\.sh$", re.M))


class SyncScriptTest(unittest.TestCase):
    """Runs the real sync against throwaway repos: an upstream at a path the script takes for cursor/plugins, and a fork whose origin is a local bare repo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        upstream = os.path.join(self.tmp, "github.com", "cursor", "plugins")
        origin = os.path.join(self.tmp, "origin.git")
        self.fork = os.path.join(self.tmp, "fork")
        os.makedirs(upstream)
        git(upstream, "init", "-q", "-b", "main")
        write(upstream, "README.md", "# cursor/plugins\n")
        write(upstream, "pstack/skills/how/SKILL.md", "---\nname: how\ndescription: Explain code.\n---\n\n# How\n\nExplore the codebase.\n")
        git(upstream, "add", "-A")
        git(upstream, "commit", "-qm", "upstream base")

        git(self.tmp, "clone", "-q", upstream, self.fork)
        git(self.fork, "config", "user.name", "t")
        git(self.fork, "config", "user.email", "t@example.com")
        git(self.fork, "remote", "rename", "origin", "upstream")
        write(self.fork, "pstack/port/install.sh", "SKILLS=(\n  how\n)\n")
        for script in ("sync-upstream.sh", "merge-upstream.py"):
            shutil.copy2(os.path.join(PORT, script), os.path.join(self.fork, "pstack", "port", script))
        git(self.fork, "add", "-A")
        git(self.fork, "commit", "-qm", "port")
        git(self.tmp, "init", "-q", "--bare", origin)
        git(self.fork, "remote", "add", "origin", origin)
        git(self.fork, "push", "-q", "origin", "main")

        write(upstream, "pstack/skills/how/SKILL.md", "---\nname: how\ndescription: Explain code.\n---\n\n# How\n\nExplore the codebase first.\n")
        git(upstream, "commit", "-qam", "upstream change")

        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        gh = os.path.join(bin_dir, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH.format(python=sys.executable))
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "gh.log")
        self.env = {**os.environ, "PATH": bin_dir + os.pathsep + os.environ["PATH"], "FAKE_GH_LOG": self.log, "GITHUB_REPOSITORY": "o/r"}
        self.env.pop("AS_ACTIONS_BOT", None)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def sync(self, **env):
        proc = subprocess.run([os.path.join(self.fork, "pstack", "port", "sync-upstream.sh")], cwd=self.fork, env={**self.env, **env}, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(self.log) as f:
            calls = [json.loads(line) for line in f]
        created = next(body for argv, body in calls if argv[:2] == ["pr", "create"])
        dispatched = any(argv[:2] == ["workflow", "run"] for argv, _ in calls)
        return created, dispatched

    def test_as_the_actions_bot_it_starts_ci_itself_and_asks_for_approval(self):
        body, dispatched = self.sync(AS_ACTIONS_BOT="true")
        self.assertTrue(dispatched)
        self.assertIn("Approve workflows to run", body)

    def test_as_a_person_the_pull_request_runs_its_own_checks(self):
        for env in ({}, {"AS_ACTIONS_BOT": "false"}):
            with self.subTest(env=env):
                body, dispatched = self.sync(**env)
                self.assertFalse(dispatched)
                self.assertNotIn("Approve workflows to run", body)
                self.assertIn("Run `pstack/port/smoke/smoke.py` before merging if a ported skill changed.", body)
                os.remove(self.log)
                git(self.fork, "reset", "-q", "--hard", "origin/main")


if __name__ == "__main__":
    unittest.main()
