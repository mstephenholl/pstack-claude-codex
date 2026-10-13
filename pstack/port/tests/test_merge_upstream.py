"""merge-upstream.py: its path rules, pointer handling, and whole merges between throwaway repos."""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "merge-upstream.py")
spec = importlib.util.spec_from_file_location("merge_upstream", SCRIPT)
merge_upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge_upstream)

POINTER = "Outside Cursor, first read `~/.agents/pstack/skills/pstack-harness/SKILL.md` in full. It maps the Cursor tools, model slugs, and paths below to Claude Code and Codex."
HOW_UPSTREAM = "---\nname: how\ndescription: Explain code.\n---\n\n# How\n\nExplore the codebase.\n\nSpawn a `Task` subagent.\n"
HOW_PORTED = HOW_UPSTREAM.replace("# How\n", f"# How\n\n{POINTER}\n")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
README_UPSTREAM = "# cursor/plugins\n\nShared intro.\n\nShared middle.\n\nShared end.\n"
README_OURS = README_UPSTREAM.replace("# cursor/plugins", "# pstack for Claude Code and Codex")
DEPENDABOT_UPSTREAM = "version: 2\nupdates: []\n\n# Shared notes.\n\n# Shared end.\n"
DEPENDABOT_OURS = DEPENDABOT_UPSTREAM.replace("updates: []", "# ours")


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def write(root, path, text):
    full = os.path.join(root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(text)


class RulesTest(unittest.TestCase):
    def test_keep_and_drop(self):
        for path in ["README.md", "pstack/skills/how/SKILL.md", "pstack/port/install.sh", ".gitignore",
                     "cursor-team-kit/skills/deslop/SKILL.md", "cursor-team-kit/LICENSE", ".github/workflows/pstack-ci.yml",
                     ".github/dependabot.yml", ".github/SECURITY.md"]:
            self.assertTrue(merge_upstream.kept(path), path)
        for path in ["advisor/README.md", "pstack/automations/benny/README.md", "pstack/skills/make-bot-ui/SKILL.md",
                     "pstack/skills/poteto-help/SKILL.md", "pstack/skills/poteto-help/references/recipes.md",
                     "pstack/.cursor-plugin/plugin.json", "pstack/plugin.json", "cursor-team-kit/skills/fix-ci/SKILL.md", ".github/workflows/validate-plugins.yml",
                     ".github/CODEOWNERS", ".github/dependabot.yaml", ".github/SECURITY.md.orig", ".github/dependabot.yml/x"]:
            self.assertFalse(merge_upstream.kept(path), path)

    def test_a_file_that_only_starts_like_a_kept_one_is_dropped(self):
        for path in ["README.md.orig", ".gitignore.bak", "cursor-team-kit/LICENSE.bak", ".github/SECURITY.md.orig", ".github/dependabot.yml.orig",
                     "docs/README.md", "cursor-team-kit/LICENSE/x"]:
            self.assertFalse(merge_upstream.kept(path), path)

    def test_the_next_sync_deletes_nothing_this_repository_tracks(self):
        tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], check=True, capture_output=True, text=True).stdout.splitlines()
        self.assertTrue(tracked)
        self.assertEqual([path for path in tracked if not merge_upstream.kept(path)], [])

    def test_every_file_we_keep_over_upstream_is_one_the_sync_keeps(self):
        self.assertTrue(merge_upstream.OURS)
        self.assertEqual([path for path in sorted(merge_upstream.OURS) if not merge_upstream.kept(path)], [])

    def test_our_github_files_win_over_upstream_copies(self):
        self.assertTrue({".github/dependabot.yml", ".github/SECURITY.md"} <= merge_upstream.OURS)

    def test_pointer_round_trip(self):
        without, pointer = merge_upstream.strip_pointer(HOW_PORTED)
        self.assertEqual((without, pointer), (HOW_UPSTREAM, POINTER))
        self.assertEqual(merge_upstream.insert_pointer(HOW_UPSTREAM, POINTER), HOW_PORTED)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.upstream = os.path.join(self.tmp, "upstream")
        self.fork = os.path.join(self.tmp, "fork")
        os.makedirs(self.upstream)
        git(self.upstream, "init", "-q", "-b", "main")
        files = {
            "README.md": README_UPSTREAM,
            "pstack/port/install.sh": "SKILLS=(\n  how\n)\n",
            "pstack/skills/how/SKILL.md": HOW_UPSTREAM,
            "pstack/skills/make-bot-ui/SKILL.md": "---\nname: make-bot-ui\ndescription: Cursor only.\n---\n",
            "advisor/README.md": "advisor v1\n",
            "cursor-team-kit/skills/deslop/SKILL.md": "---\nname: deslop\ndescription: Deslop.\n---\n",
            "cursor-team-kit/skills/fix-ci/SKILL.md": "---\nname: fix-ci\ndescription: Fix CI.\n---\n",
            ".github/workflows/validate-plugins.yml": "name: validate\n",
            ".github/dependabot.yml": DEPENDABOT_UPSTREAM,
        }
        for path, text in files.items():
            write(self.upstream, path, text)
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-qm", "upstream base")

        git(self.tmp, "clone", "-q", self.upstream, self.fork)
        # merge-upstream.py runs git without -c overrides, and git merge needs an identity even with --no-commit.
        git(self.fork, "config", "user.name", "t")
        git(self.fork, "config", "user.email", "t@example.com")
        git(self.fork, "rm", "-rq", "pstack/skills/make-bot-ui", "advisor", "cursor-team-kit/skills/fix-ci", ".github/workflows/validate-plugins.yml")
        write(self.fork, "pstack/skills/how/SKILL.md", HOW_PORTED)
        write(self.fork, "README.md", README_OURS)
        write(self.fork, ".github/workflows/pstack-ci.yml", "name: pstack ci\n")
        write(self.fork, ".github/dependabot.yml", DEPENDABOT_OURS)
        write(self.fork, ".github/SECURITY.md", "# Security policy\n")
        git(self.fork, "add", "-A")
        git(self.fork, "commit", "-qm", "port")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def upstream_commit(self, changes):
        for path, text in changes.items():
            write(self.upstream, path, text)
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-qm", "upstream change")
        git(self.fork, "fetch", "-q", "origin")

    def merge(self):
        report = os.path.join(self.tmp, "report.json")
        proc = subprocess.run(["python3", SCRIPT, "--repo", self.fork, "--ref", "origin/main", "--no-fetch", "--report", report], capture_output=True, text=True)
        with open(report) as f:
            return proc.returncode, json.load(f), proc.stdout

    def test_a_routine_upstream_change_merges_without_a_person(self):
        self.upstream_commit({
            "pstack/skills/how/SKILL.md": HOW_UPSTREAM.replace("Explore the codebase.", "Explore the codebase first."),
            "advisor/README.md": "advisor v2\n",
            "greenhouse/README.md": "new plugin\n",
            "README.md": README_UPSTREAM.replace("# cursor/plugins", "# cursor/plugins, now with more plugins"),
            ".github/workflows/validate-plugins.yml": "name: validate v2\n",
            "pstack/skills/new-thing/SKILL.md": "---\nname: new-thing\ndescription: New.\n---\n\n# New thing\n\nUse `AskQuestion`.\n",
        })
        code, report, out = self.merge()
        self.assertEqual((code, report["status"]), (0, "merged"), out)
        self.assertEqual(report["resolved"], ["pstack/skills/how/SKILL.md"])
        self.assertEqual(report["manual"], [])
        self.assertEqual(len(report["to_port"]), 1)
        self.assertIn("new-thing", report["to_port"][0])
        self.assertIn("AskQuestion", report["to_port"][0])

        tracked = set(git(self.fork, "ls-files").split())
        self.assertNotIn("advisor/README.md", tracked)
        self.assertNotIn("greenhouse/README.md", tracked)
        self.assertNotIn(".github/workflows/validate-plugins.yml", tracked)
        self.assertIn(".github/workflows/pstack-ci.yml", tracked)
        with open(os.path.join(self.fork, "README.md")) as f:
            self.assertEqual(f.read(), README_OURS)
        with open(os.path.join(self.fork, "pstack/skills/how/SKILL.md")) as f:
            self.assertEqual(f.read(), HOW_PORTED.replace("Explore the codebase.", "Explore the codebase first."))

        git(self.fork, "commit", "-qm", "merge upstream")
        code, report, _ = self.merge()
        self.assertEqual((code, report["status"]), (0, "up_to_date"))

    def test_our_dependabot_and_security_files_survive_a_merge(self):
        self.upstream_commit({
            ".github/dependabot.yml": "version: 2\nupdates: [upstream]\n",
            ".github/SECURITY.md": "# Upstream security policy\n",
            "advisor/README.md": "advisor v2\n",
        })
        code, report, out = self.merge()
        self.assertEqual((code, report["status"], report["manual"]), (0, "up_to_date", []), out)
        for path, ours in {".github/dependabot.yml": DEPENDABOT_OURS, ".github/SECURITY.md": "# Security policy\n"}.items():
            with open(os.path.join(self.fork, path)) as f:
                self.assertEqual(f.read(), ours, path)

    def test_our_copy_survives_an_upstream_edit_that_merges_cleanly(self):
        self.upstream_commit({
            "README.md": README_UPSTREAM.replace("Shared end.", "Shared end, edited upstream."),
            ".github/dependabot.yml": DEPENDABOT_UPSTREAM.replace("# Shared end.", "# Shared end, edited upstream."),
        })
        code, report, out = self.merge()
        self.assertEqual((code, report["status"], report["manual"]), (0, "up_to_date", []), out)
        for path, ours in {"README.md": README_OURS, ".github/dependabot.yml": DEPENDABOT_OURS}.items():
            with open(os.path.join(self.fork, path)) as f:
                self.assertEqual(f.read(), ours, path)
        self.assertFalse(merge_upstream.merge_head(self.fork), "no empty merge left in progress")

    def test_an_upstream_change_outside_the_port_leaves_nothing_to_merge(self):
        self.upstream_commit({"advisor/README.md": "advisor v2\n", "greenhouse/README.md": "new plugin\n"})
        code, report, out = self.merge()
        self.assertEqual((code, report["status"], report["dropped"]), (0, "up_to_date", 2), out)
        self.assertFalse(merge_upstream.merge_head(self.fork), "no empty merge left in progress")
        self.assertEqual(git(self.fork, "status", "--porcelain"), "")

    def test_a_merge_git_refuses_fails_loudly(self):
        git(self.fork, "config", "--unset", "user.name")
        git(self.fork, "config", "--unset", "user.email")
        self.upstream_commit({"advisor/README.md": "advisor v2\n"})
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        report = os.path.join(self.tmp, "report.json")
        proc = subprocess.run(["python3", SCRIPT, "--repo", self.fork, "--ref", "origin/main", "--no-fetch", "--report", report], capture_output=True, text=True, env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("failed", proc.stderr)
        self.assertFalse(os.path.exists(report))

    def test_a_real_conflict_is_left_for_a_person(self):
        write(self.fork, "pstack/skills/how/SKILL.md", HOW_PORTED.replace("Spawn a `Task` subagent.", "Spawn a subagent."))
        git(self.fork, "commit", "-qam", "port edit")
        self.upstream_commit({"pstack/skills/how/SKILL.md": HOW_UPSTREAM.replace("Spawn a `Task` subagent.", "Spawn two `Task` subagents.")})
        code, report, out = self.merge()
        self.assertEqual((code, report["status"]), (1, "conflicts"), out)
        self.assertEqual(report["manual"], ["pstack/skills/how/SKILL.md"])


if __name__ == "__main__":
    unittest.main()
