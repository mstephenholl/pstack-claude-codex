"""worktree-audit.sh finds the recent chat of a worktree, with the system's file tools and with GNU's, in a throwaway repo."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PORT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PORT))
SCRIPT = os.path.join(ROOT, "pstack", "skills", "poteto-mode", "scripts", "worktree-audit.sh")
REAL_DATE = shutil.which("date")

GH = 'print("[]")\n'

# Only the call the script makes: rg -l -0 -e PATTERN... FILE...
RG = """\
import sys
args = sys.argv[1:]
assert args[:2] == ["-l", "-0"], args
args = args[2:]
patterns = []
while args[:1] == ["-e"]:
    patterns.append(args[1])
    args = args[2:]
for path in args:
    with open(path) as f:
        if any(p in f.read() for p in patterns):
            sys.stdout.write(path + "\\0")
"""

# GNU stat answers -c, and -f describes a filesystem instead of a file.
GNU_STAT = """\
import os
import sys
args = sys.argv[1:]
if args[0] == "-f":
    sys.exit("stat: cannot read file system information")
assert args[0] == "-c", args
for path in args[2:]:
    print(int(os.stat(path).st_mtime), path)
"""

# GNU date reads a file's time with -r and an epoch with -d @N.
GNU_DATE = f"""\
import subprocess
import sys
import time
args = sys.argv[1:]
if args[0] == "-r":
    sys.exit("date: %s: No such file or directory" % args[1])
if args[0] == "-d":
    print(time.strftime(args[2][1:], time.localtime(int(args[1][1:]))))
else:
    sys.exit(subprocess.call([{REAL_DATE!r}, *args]))
"""


def git(cwd, env, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


class WorktreeAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.bin = os.path.join(self.tmp, "bin")
        home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(self.bin)
        os.makedirs(self.repo)
        self.env = {**os.environ, "HOME": home, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "PATH": self.bin + os.pathsep + os.environ["PATH"]}
        git(self.repo, self.env, "init")
        git(self.repo, self.env, "commit", "--allow-empty", "-m", "init")
        git(self.repo, self.env, "worktree", "add", "-b", "feature", self.wt)

        self.chat_time = int(time.time())
        chat = os.path.join(home, ".claude", "projects", "proj", "x.jsonl")
        os.makedirs(os.path.dirname(chat))
        with open(chat, "w") as f:
            f.write(json.dumps({"type": "assistant", "command": f"cat {self.wt}/README.md"}) + "\n")
        os.utime(chat, (self.chat_time, self.chat_time))

        self.fake("gh", GH)
        self.fake("rg", RG)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as f:
            f.write(f"#!{sys.executable}\n{body}")
        os.chmod(path, 0o755)

    def audit(self):
        proc = subprocess.run([SCRIPT, "-b", "HEAD"], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        base, *lines = proc.stdout.splitlines()
        self.assertEqual(base, "# base: HEAD")
        header, *rows = (line.split("\t") for line in lines)
        self.assertEqual(len(rows), 1, proc.stdout)
        return dict(zip(header, rows[0]))

    def assert_recent_chat_found(self):
        row = self.audit()
        self.assertEqual(
            (row["LAST_CHAT"], row["BUCKET"], row["WORKTREE"]),
            (time.strftime("%Y-%m-%d", time.localtime(self.chat_time)), "verify-recent-chat", self.wt),
        )

    def test_finds_the_recent_chat_with_the_system_tools(self):
        self.assert_recent_chat_found()

    def test_finds_the_recent_chat_with_gnu_tools(self):
        self.fake("stat", GNU_STAT)
        self.fake("date", GNU_DATE)
        self.assert_recent_chat_found()


if __name__ == "__main__":
    unittest.main()
