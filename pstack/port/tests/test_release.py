"""release.py: version planning from merged pull requests, release notes, and a release cut from a real git history."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PORT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PORT))
SCRIPT = os.path.join(PORT, "release.py")
CI = os.path.join(ROOT, ".github", "workflows", "pstack-ci.yml")
MACOS_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "pstack-macos.yml")
PR_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "pstack-pr.yml")
SYNC = os.path.join(PORT, "sync-upstream.sh")
spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

Version, Level, Commit, Change, Release = release.Version, release.Level, release.Commit, release.Change, release.Release
SHA = "a" * 40


def commit(subject, body="", sha=SHA):
    return Commit(sha, subject, body)


def change(sha=SHA, pr=None, type=None, scope=None, description="", breaking=False, release_as=()):
    return Change(sha, pr, type, scope, description, breaking, release_as)


class VersionTest(unittest.TestCase):
    def test_parse_accepts_plain_triples_with_or_without_v(self):
        self.assertEqual(Version.parse("0.4.2"), Version(0, 4, 2))
        self.assertEqual(Version.parse("v10.0.31"), Version(10, 0, 31))

    def test_parse_rejects_everything_else(self):
        for text in ["v1.0.0-rc.1", "1.0.0+build", "v2", "v1.2", "vfoo", "v1.2.3.4", "v01.2.3", " v1.2.3", "v1.2.3\n", ""]:
            with self.assertRaises(ValueError, msg=text):
                Version.parse(text)

    def test_tag(self):
        self.assertEqual(Version(1, 2, 3).tag, "v1.2.3")

    def test_bump_resets_what_is_below_the_level(self):
        v = Version(1, 2, 3)
        self.assertEqual(v.bump(Level.PATCH), Version(1, 2, 4))
        self.assertEqual(v.bump(Level.MINOR), Version(1, 3, 0))
        self.assertEqual(v.bump(Level.MAJOR), Version(2, 0, 0))

    def test_successors_are_the_next_patch_minor_and_major(self):
        self.assertEqual(Version(0, 4, 2).successors(), (Version(0, 4, 3), Version(0, 5, 0), Version(1, 0, 0)))

    def test_versions_sort_numerically(self):
        self.assertGreater(Version(0, 10, 0), Version(0, 9, 9))


class ParseChangeTest(unittest.TestCase):
    def test_a_legacy_merge_commit_takes_its_title_from_the_body(self):
        got = release.parse_change(commit(
            "Merge pull request #2 from mstephenholl/fix/single-harness-install",
            "fix(pstack): install for the harness CLIs on PATH by default\n",
        ))
        self.assertEqual(got, change(pr=2, type="fix", scope="pstack", description="install for the harness CLIs on PATH by default"))

    def test_a_legacy_merge_commit_keeps_the_rest_of_its_body_for_directives(self):
        got = release.parse_change(commit(
            "Merge pull request #9 from o/b",
            "\nfeat: add a thing\n\nReleases the thing.\n\nRelease-As: 1.0.0\n",
        ))
        self.assertEqual(got, change(pr=9, type="feat", description="add a thing", release_as=("1.0.0",)))

    def test_a_legacy_merge_commit_with_no_body_is_titled_by_its_subject(self):
        got = release.parse_change(commit("Merge pull request #4 from o/b", ""))
        self.assertEqual(got, change(pr=4, description="Merge pull request #4 from o/b"))

    def test_a_pr_title_commit_is_conventional_with_the_pr_number_stripped(self):
        got = release.parse_change(commit("feat(cli)!: drop the old flag (#7)"))
        self.assertEqual(got, change(pr=7, type="feat", scope="cli", description="drop the old flag", breaking=True))

    def test_the_type_is_lowercased(self):
        self.assertEqual(release.parse_change(commit("Fix(Docs): typo (#3)")), change(pr=3, type="fix", scope="Docs", description="typo"))

    def test_a_plain_title_has_no_type_and_no_pr(self):
        got = release.parse_change(commit("Keep pstack update from moving a clone backwards"))
        self.assertEqual(got, change(description="Keep pstack update from moving a clone backwards"))

    def test_a_sync_title_is_plain(self):
        got = release.parse_change(commit("Merge upstream cursor/plugins (#5)", "Upstream: cursor/plugins@0123456789ab\n"))
        self.assertEqual(got, change(pr=5, description="Merge upstream cursor/plugins"))

    def test_a_breaking_change_footer_marks_the_change(self):
        got = release.parse_change(commit("refactor(cli): rename the command (#8)", "Renames it.\n\nBREAKING CHANGE: pstack now starts as pst.\n"))
        self.assertEqual(got, change(pr=8, type="refactor", scope="cli", description="rename the command", breaking=True))

    def test_the_hyphenated_breaking_footer_counts_too(self):
        got = release.parse_change(commit("fix: x (#8)", "BREAKING-CHANGE: yes\n"))
        self.assertTrue(got.breaking)

    def test_directives_inside_fenced_code_are_ignored(self):
        body = "Documents the footers.\n\n```\nBREAKING CHANGE: example\nRelease-As: 9.9.9\n```\n\n~~~md\nRelease-As: 8.8.8\n~~~\n\nRelease-As: 1.0.0\n"
        got = release.parse_change(commit("docs: describe the footers (#6)", body))
        self.assertEqual(got, change(pr=6, type="docs", description="describe the footers", release_as=("1.0.0",)))

    def test_a_directive_after_an_unterminated_fence_is_ignored(self):
        got = release.parse_change(commit("docs: x (#6)", "```\nBREAKING CHANGE: example\n"))
        self.assertFalse(got.breaking)

    def test_a_directive_must_start_its_line(self):
        got = release.parse_change(commit("fix: x (#6)", "Do not write BREAKING CHANGE: in prose.\nSee Release-As: 2.0.0 in the docs.\n"))
        self.assertEqual((got.breaking, got.release_as), (False, ()))


class PlanReleaseTest(unittest.TestCase):
    def plan(self, previous, *titles):
        commits = [commit(title, sha=f"{i:040x}") for i, title in enumerate(titles, 1)]
        return release.plan_release(previous, commits[0].sha, commits)

    def test_a_plain_title_is_a_patch(self):
        got = self.plan(Version(0, 4, 2), "Keep pstack update from moving a clone backwards")
        self.assertEqual((got.version, got.previous), (Version(0, 4, 3), Version(0, 4, 2)))

    def test_a_sync_title_is_a_patch(self):
        self.assertEqual(self.plan(Version(0, 4, 2), "Merge upstream cursor/plugins (#5)").version, Version(0, 4, 3))

    def test_a_fix_is_a_patch_and_a_feat_is_a_minor(self):
        self.assertEqual(self.plan(Version(1, 4, 2), "fix(x): y (#1)").version, Version(1, 4, 3))
        self.assertEqual(self.plan(Version(1, 4, 2), "fix(x): y (#1)", "feat(x): z (#2)").version, Version(1, 5, 0))

    def test_breaking_is_a_minor_before_1_0_and_a_major_after(self):
        self.assertEqual(self.plan(Version(0, 4, 2), "feat!: x (#1)").version, Version(0, 5, 0))
        self.assertEqual(self.plan(Version(1, 4, 2), "feat!: x (#1)").version, Version(2, 0, 0))

    def test_the_release_covers_every_change_newest_first(self):
        got = self.plan(Version(0, 1, 0), "fix: newer (#4)", "feat: older (#3)")
        self.assertEqual([c.pr for c in got.changes], [4, 3])
        self.assertEqual(got.target, f"{1:040x}")

    def test_release_as_names_the_next_minor_or_major(self):
        body = "Release-As: 1.0.0\n"
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#1)", body)])
        self.assertEqual((got.version, got.warnings), (Version(1, 0, 0), ()))

    def test_a_pr_may_lower_its_own_level_with_release_as(self):
        for title in ("feat: x (#1)", "feat!: x (#1)"):
            with self.subTest(title):
                got = release.plan_release(Version(0, 4, 2), SHA, [commit(title, "Release-As: v0.4.3\n")])
                self.assertEqual((got.version, got.warnings), (Version(0, 4, 3), ()))

    def test_release_as_sets_only_its_own_prs_level(self):
        commits = [commit("feat: a (#1)", sha="1" * 40), commit("fix: b (#2)", "Release-As: 0.4.3\n", sha="2" * 40)]
        got = release.plan_release(Version(0, 4, 2), "1" * 40, commits)
        self.assertEqual((got.version, got.warnings), (Version(0, 5, 0), ()))

    def test_release_as_does_not_hide_a_breaking_change_in_another_pr(self):
        commits = [commit("feat!: drop flag (#3)", sha="1" * 40), commit("docs: typo (#4)", "Release-As: 1.4.3\n", sha="2" * 40)]
        self.assertEqual(release.plan_release(Version(1, 4, 2), "1" * 40, commits).version, Version(2, 0, 0))

    def test_an_explicit_major_is_not_clamped_before_1_0(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("docs: x (#1)", "Release-As: 1.0.0\n")])
        self.assertEqual((got.version, got.warnings), (Version(1, 0, 0), ()))

    def test_the_highest_release_as_within_one_pr_sets_its_level(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#1)", "Release-As: 0.5.0\nRelease-As: 0.4.3\n")])
        self.assertEqual(got.version, Version(0, 5, 0))

    def test_a_release_as_with_trailing_words_is_ignored_and_the_pr_keeps_its_kind_level(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("feat: x (#7)", "Release-As: 1.0.0 (first stable)\n")])
        self.assertEqual(got.version, Version(0, 5, 0))
        self.assertEqual(got.warnings, ("#7: ignored Release-As 1.0.0 (first stable), which is not one of v0.4.3, v0.5.0, v1.0.0",))

    def test_a_release_as_that_is_not_the_next_version_is_ignored_with_a_warning(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#7)", "Release-As: 10.0.0\n")])
        self.assertEqual(got.version, Version(0, 4, 3))
        self.assertEqual(got.warnings, ("#7: ignored Release-As 10.0.0, which is not one of v0.4.3, v0.5.0, v1.0.0",))

    def test_a_release_as_that_does_not_parse_is_ignored_with_a_warning(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("fix: x (#7)", "Release-As: soon\n")])
        self.assertEqual((got.version, len(got.warnings)), (Version(0, 4, 3), 1))

    def test_a_warning_without_a_pr_names_the_commit(self):
        got = release.plan_release(Version(0, 4, 2), SHA, [commit("Direct push", "Release-As: 3.0.0\n")])
        self.assertTrue(got.warnings[0].startswith("aaaaaaaaaaaa: ignored Release-As 3.0.0"), got.warnings)

    def test_the_highest_valid_release_as_wins(self):
        commits = [commit("fix: a (#2)", "Release-As: 0.5.0\n", sha="1" * 40), commit("fix: b (#1)", "Release-As: 1.0.0\n", sha="2" * 40)]
        self.assertEqual(release.plan_release(Version(0, 4, 2), "1" * 40, commits).version, Version(1, 0, 0))

    def test_an_empty_range_is_nothing_to_release(self):
        self.assertIsNone(release.plan_release(Version(0, 4, 2), SHA, []))


class NotesTest(unittest.TestCase):
    def test_sections_lines_and_footer(self):
        got = release.notes(Release(
            version=Version(0, 2, 0),
            previous=Version(0, 1, 0),
            target="d" * 40,
            changes=(
                change(pr=7, type="feat", scope="cli", description="drop the old flag", breaking=True),
                change(pr=6, type="feat", scope="setup", description="add a models line"),
                change(pr=2, type="fix", scope="pstack", description="install for the harness CLIs on PATH by default"),
                change(pr=5, description="Merge upstream cursor/plugins"),
                change(description="Keep pstack update from moving a clone backwards"),
                change(pr=9, type="docs", description="explain releases"),
            ),
            warnings=("#7: ignored Release-As 10.0.0",),
        ), "mstephenholl/pstack-claude-codex")
        self.assertEqual(got, (
            "## Breaking changes\n\n"
            "- **cli:** drop the old flag (#7)\n\n"
            "## Features\n\n"
            "- **setup:** add a models line (#6)\n\n"
            "## Fixes\n\n"
            "- **pstack:** install for the harness CLIs on PATH by default (#2)\n\n"
            "## Other changes\n\n"
            "- Merge upstream cursor/plugins (#5)\n"
            "- Keep pstack update from moving a clone backwards\n"
            "- explain releases (#9)\n\n"
            "Update with `pstack update`. Full changes: https://github.com/mstephenholl/pstack-claude-codex/compare/v0.1.0...v0.2.0"
        ))

    def test_empty_sections_are_omitted(self):
        got = release.notes(Release(Version(0, 1, 1), Version(0, 1, 0), SHA, (change(pr=4, type="fix", scope="pstack", description="y"),), ()), "o/r")
        self.assertEqual(got, "## Fixes\n\n- **pstack:** y (#4)\n\nUpdate with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.1.1")


    def test_sections_follow_the_table_whatever_the_order_of_the_changes(self):
        got = release.notes(Release(
            version=Version(0, 2, 0),
            previous=Version(0, 1, 0),
            target=SHA,
            changes=(
                change(pr=4, description="plain"),
                change(pr=3, type="fix", description="repair"),
                change(pr=2, type="feat", description="add"),
                change(pr=1, type="fix", description="drop", breaking=True),
            ),
            warnings=(),
        ), "o/r")
        self.assertEqual(got, (
            "## Breaking changes\n\n- drop (#1)\n\n"
            "## Features\n\n- add (#2)\n\n"
            "## Fixes\n\n- repair (#3)\n\n"
            "## Other changes\n\n- plain (#4)\n\n"
            "Update with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.2.0"
        ))


class GitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        self.bin = os.path.join(self.tmp, "bin")
        self.gh_dir = os.path.join(self.tmp, "gh")
        for d in (self.repo, self.bin, self.gh_dir):
            os.makedirs(d)
        patcher = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": self.bin + os.pathsep + os.environ["PATH"],
            "FAKE_GH_DIR": self.gh_dir,
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("GITHUB_REPOSITORY", "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS", "FAKE_GH_EXIT"):
            os.environ.pop(name, None)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)

        self.git("init", "-q", "-b", "main")
        self.commit("initial")
        self.git("tag", "-a", "v0.1.0", "-m", "v0.1.0")
        self.tagged = self.head()
        self.git("checkout", "-q", "-b", "upstream")
        self.commit("feat(x): upstream feature")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "Merge upstream cursor/plugins (#3)", "upstream")
        self.commit("fix(pstack): y (#4)")

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message, paths=("pstack/change.txt",)):
        for path in paths:
            full = os.path.join(self.repo, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "a") as f:
                f.write(message + "\n")
            self.git("add", path)
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def head(self):
        return self.git("rev-parse", "HEAD")

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = release.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def install_gh(self, script):
        path = os.path.join(self.bin, "gh")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def gh_calls(self):
        path = os.path.join(self.gh_dir, "argv")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            argv = f.read().splitlines()
        with open(os.path.join(self.gh_dir, "stdin")) as f:
            return argv, f.read()


class PreviewTest(GitCase):
    def test_a_preview_reads_first_parent_history_only(self):
        sha = self.head()
        code, out, err = self.run_main("--repo", "o/r")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, (
            f"would release v0.1.1 at {sha[:12]} after v0.1.0 (2 changes)\n\n"
            "## Fixes\n\n"
            "- **pstack:** y (#4)\n\n"
            "## Other changes\n\n"
            "- Merge upstream cursor/plugins (#3)\n\n"
            "Update with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.1.1\n"
        ))

    def test_a_preview_needs_no_repository(self):
        code, out, err = self.run_main()
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("would release v0.1.1 at "), out)
        self.assertIn("https://github.com/OWNER/REPO/compare/v0.1.0...v0.1.1", out)

    def test_a_preview_never_calls_gh(self):
        self.install_gh('echo called > "$FAKE_GH_DIR/argv"; cat > "$FAKE_GH_DIR/stdin"\n')
        code, out, _ = self.run_main("--repo", "o/r")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("would release"), out)
        self.assertIsNone(self.gh_calls())

    def test_the_repository_defaults_to_github_repository(self):
        os.environ["GITHUB_REPOSITORY"] = "env/repo"
        _, out, _ = self.run_main()
        self.assertIn("https://github.com/env/repo/compare/v0.1.0...v0.1.1", out)

    def test_a_breaking_pr_takes_the_version_to_0_2_0(self):
        self.commit("feat(cli)!: drop the old flag (#5)")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertTrue(out.startswith("would release v0.2.0 "), out)
        self.assertIn("## Breaking changes\n\n- **cli:** drop the old flag (#5)\n", out)

    def test_target_selects_an_older_commit(self):
        merge = self.git("rev-parse", "HEAD~1")
        _, out, _ = self.run_main("--repo", "o/r", "--target", merge)
        self.assertTrue(out.startswith(f"would release v0.1.1 at {merge[:12]} after v0.1.0 (1 change)\n"), out)

    def test_an_ignored_release_as_is_reported_but_stays_out_of_the_notes(self):
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        lines = out.splitlines()
        self.assertEqual(lines[1], "warning: #6: ignored Release-As 10.0.0, which is not one of v0.1.1, v0.2.0, v1.0.0")
        self.assertNotIn("Release-As", "\n".join(lines[2:]))

    def test_warnings_become_annotations_on_github_actions(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertIn("::warning title=release.py::#6: ignored Release-As 10.0.0, which is not one of v0.1.1, v0.2.0, v1.0.0\n", out)

    def test_a_warning_annotation_escapes_a_percent_sign_in_a_tag_name(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        self.git("tag", "v2%0A")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertIn("::warning title=release.py::skipped tag v2%250A, which is not plain vX.Y.Z. pstack update and the bootstrap ignore it too.\n", out)

    def test_no_annotation_is_printed_elsewhere(self):
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertNotIn("::warning", out)

    def test_the_decision_and_notes_go_to_the_step_summary_without_annotations(self):
        summary = os.path.join(self.tmp, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = summary
        os.environ["GITHUB_ACTIONS"] = "true"
        self.commit("fix: z (#6)\n\nRelease-As: 10.0.0")
        _, out, _ = self.run_main("--repo", "o/r")
        with open(summary) as f:
            written = f.read()
        self.assertEqual(written, "".join(line for line in out.splitlines(keepends=True) if not line.startswith("::warning")))
        self.assertIn("warning: #6: ignored Release-As 10.0.0", written)

    def test_at_the_tagged_commit_there_is_nothing_to_release(self):
        code, out, err = self.run_main("--repo", "o/r", "--target", self.tagged)
        self.assertEqual((code, out, err), (0, f"nothing to release: {self.tagged[:12]} is covered by v0.1.0\n", ""))

    def test_a_target_below_the_tag_is_covered_too(self):
        self.commit("later")
        self.git("tag", "-a", "v0.1.1", "-m", "v0.1.1")
        code, out, _ = self.run_main("--target", "HEAD~1")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^nothing to release: [0-9a-f]{12} is covered by v0\.1\.1\n$")

    def test_the_newest_tag_decides_the_base(self):
        self.git("tag", "-a", "v0.9.0", "-m", "v0.9.0", "HEAD~1")
        _, out, _ = self.run_main("--repo", "o/r")
        self.assertRegex(out, r"^would release v0\.9\.1 at [0-9a-f]{12} after v0\.9\.0 \(1 change\)\n")

    def test_a_tag_that_is_not_plain_is_skipped_with_a_warning(self):
        for name in ("v2", "v0.9", "v1.0.0-rc.1"):
            with self.subTest(name):
                self.git("tag", name)
                code, out, err = self.run_main("--repo", "o/r")
                self.git("tag", "-d", name)
                self.assertEqual((code, err), (0, ""))
                lines = out.splitlines()
                self.assertRegex(lines[0], r"^would release v0\.1\.1 at [0-9a-f]{12} after v0\.1\.0 \(2 changes\)$")
                self.assertEqual(lines[1], f"warning: skipped tag {name}, which is not plain vX.Y.Z. pstack update and the bootstrap ignore it too.")

    def test_a_skipped_tag_is_reported_when_there_is_nothing_to_release_too(self):
        self.git("tag", "v2")
        code, out, _ = self.run_main("--target", self.tagged)
        self.assertEqual(code, 0)
        self.assertEqual(out, (
            f"nothing to release: {self.tagged[:12]} is covered by v0.1.0\n"
            "warning: skipped tag v2, which is not plain vX.Y.Z. pstack update and the bootstrap ignore it too.\n"
        ))

    def test_with_no_plain_tag_there_is_nothing_to_release_after(self):
        self.git("tag", "-d", "v0.1.0")
        self.git("tag", "v2")
        code, out, err = self.run_main()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no plain vX.Y.Z tag", err)

    def test_no_tag_fails(self):
        self.git("tag", "-d", "v0.1.0")
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("no plain vX.Y.Z tag", err)

    def test_a_newest_tag_off_the_targets_history_fails_with_the_recovery_pointer(self):
        self.git("checkout", "-q", "--orphan", "elsewhere")
        self.commit("elsewhere")
        self.git("tag", "-a", "v0.3.0", "-m", "v0.3.0")
        self.git("checkout", "-q", "main")
        code, out, err = self.run_main()
        self.assertEqual((code, out), (1, ""))
        self.assertIn("v0.3.0", err)
        self.assertIn('"Repository settings" in pstack/port/README.md', err)

    def test_an_unknown_target_fails(self):
        code, _, err = self.run_main("--target", "no-such-ref")
        self.assertEqual(code, 1)
        self.assertIn("no-such-ref", err)


class UnshippedTest(GitCase):
    def setUp(self):
        super().setUp()
        self.git("tag", "-a", "v0.2.0", "-m", "v0.2.0")
        self.install_gh('printf \'%s\\n\' "$@" > "$FAKE_GH_DIR/argv"\ncat > "$FAKE_GH_DIR/stdin"\n')

    def publish(self):
        return self.run_main("--publish", "--repo", "o/r")

    def test_a_commit_that_changes_only_github_releases_nothing(self):
        self.commit("chore(deps): bump actions/checkout (#7)", [".github/workflows/x.yml"])
        code, out, err = self.publish()
        self.assertEqual((code, out, err), (0, "nothing to release: only .github/ changed since v0.2.0 (1 change held for the next release)\n", ""))
        self.assertIsNone(self.gh_calls())

    def test_every_commit_held_back_is_counted(self):
        self.commit("chore(deps): bump actions/checkout (#7)", [".github/workflows/x.yml"])
        self.commit("chore(deps): bump actions/setup-python (#8)", [".github/workflows/y.yml"])
        _, out, _ = self.publish()
        self.assertEqual(out, "nothing to release: only .github/ changed since v0.2.0 (2 changes held for the next release)\n")

    def test_the_held_commits_join_the_next_release_notes_and_level(self):
        self.commit("feat(ci): add a workflow (#7)", [".github/workflows/x.yml"])
        self.commit("fix(pstack): y2 (#8)")
        sha = self.head()
        code, out, err = self.publish()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, (
            f"released v0.3.0 at {sha[:12]} after v0.2.0 (2 changes)\n\n"
            "## Features\n\n"
            "- **ci:** add a workflow (#7)\n\n"
            "## Fixes\n\n"
            "- **pstack:** y2 (#8)\n\n"
            "Update with `pstack update`. Full changes: https://github.com/o/r/compare/v0.2.0...v0.3.0\n"
        ))
        self.assertEqual(json.loads(self.gh_calls()[1])["tag_name"], "v0.3.0")

    def test_a_commit_that_changes_a_shipped_file_beside_github_releases(self):
        self.commit("fix: z (#7)", [".github/workflows/x.yml", "pstack/change.txt"])
        code, out, _ = self.publish()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("released v0.2.1 "), out)

    def test_a_file_moved_into_github_has_still_left_the_clone(self):
        os.makedirs(os.path.join(self.repo, ".github"))
        self.git("mv", "pstack/change.txt", ".github/change.txt")
        self.git("commit", "-q", "-m", "chore: move the file (#7)")
        code, out, _ = self.publish()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("released v0.2.1 "), out)

    def test_a_merge_that_changes_no_files_releases_nothing(self):
        self.git("checkout", "-q", "-b", "sync")
        self.commit("feat(x): upstream change the port drops", ["outside-the-port.txt"])
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-s", "ours", "-m", "chore(upstream): merge cursor/plugins (#7)", "sync")
        code, out, err = self.publish()
        self.assertEqual((code, out, err), (0, "nothing to release: no files changed since v0.2.0 (1 change held for the next release)\n", ""))
        self.assertIsNone(self.gh_calls())

    def test_the_preview_says_the_same(self):
        self.commit("chore(deps): bump actions/checkout (#7)", [".github/workflows/x.yml"])
        code, out, err = self.run_main()
        self.assertEqual((code, out, err), (0, "nothing to release: only .github/ changed since v0.2.0 (1 change held for the next release)\n", ""))

    def test_a_skipped_tag_is_still_reported(self):
        self.git("tag", "v2")
        self.commit("chore(deps): bump actions/checkout (#7)", [".github/workflows/x.yml"])
        _, out, _ = self.publish()
        self.assertEqual(out, (
            "nothing to release: only .github/ changed since v0.2.0 (1 change held for the next release)\n"
            "warning: skipped tag v2, which is not plain vX.Y.Z. pstack update and the bootstrap ignore it too.\n"
        ))


class PublishTest(GitCase):
    def test_publish_creates_the_tag_and_release_in_one_call(self):
        self.install_gh('printf \'%s\\n\' "$@" > "$FAKE_GH_DIR/argv"\ncat > "$FAKE_GH_DIR/stdin"\n')
        sha = self.head()
        code, out, err = self.run_main("--publish", "--repo", "o/r")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith(f"released v0.1.1 at {sha[:12]} after v0.1.0 (2 changes)\n"), out)
        argv, stdin = self.gh_calls()
        self.assertEqual(argv, ["api", "-X", "POST", "repos/o/r/releases", "--input", "-"])
        payload = json.loads(stdin)
        self.assertEqual(payload, {
            "tag_name": "v0.1.1",
            "target_commitish": sha,
            "name": "pstack v0.1.1",
            "body": (
                "## Fixes\n\n- **pstack:** y (#4)\n\n"
                "## Other changes\n\n- Merge upstream cursor/plugins (#3)\n\n"
                "Update with `pstack update`. Full changes: https://github.com/o/r/compare/v0.1.0...v0.1.1"
            ),
            "make_latest": "true",
        })

    def test_publish_needs_a_repository(self):
        self.install_gh('echo called > "$FAKE_GH_DIR/argv"; cat > "$FAKE_GH_DIR/stdin"\n')
        code, out, err = self.run_main("--publish")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("--repo", err)
        self.assertIsNone(self.gh_calls())

    def test_publish_takes_the_repository_from_github_repository(self):
        self.install_gh('printf \'%s\\n\' "$@" > "$FAKE_GH_DIR/argv"\ncat > "$FAKE_GH_DIR/stdin"\n')
        os.environ["GITHUB_REPOSITORY"] = "env/repo"
        code, _, _ = self.run_main("--publish")
        self.assertEqual(code, 0)
        self.assertIn("repos/env/repo/releases", self.gh_calls()[0])

    def test_nothing_to_release_never_calls_gh(self):
        self.install_gh('echo called > "$FAKE_GH_DIR/argv"; cat > "$FAKE_GH_DIR/stdin"\n')
        code, out, _ = self.run_main("--publish", "--repo", "o/r", "--target", self.tagged)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("nothing to release"), out)
        self.assertIsNone(self.gh_calls())

    def test_a_failed_publish_exits_1_and_claims_no_release(self):
        self.install_gh('cat > /dev/null\necho "HTTP 422: tag exists" >&2\nexit 1\n')
        code, out, err = self.run_main("--publish", "--repo", "o/r")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("HTTP 422: tag exists", err)

    def test_a_missing_gh_exits_1(self):
        only_git = os.path.join(self.tmp, "only-git")
        os.makedirs(only_git)
        os.symlink(shutil.which("git"), os.path.join(only_git, "git"))
        with mock.patch.dict(os.environ, {"PATH": only_git}):
            code, _, err = self.run_main("--publish", "--repo", "o/r")
        self.assertEqual(code, 1)
        self.assertIn("gh", err)


class CheckPrTest(GitCase):
    TYPES = "feat, fix, docs, refactor, test, chore, perf, ci, build, style, revert"

    def setUp(self):
        super().setUp()
        self.git("tag", "-a", "v0.2.0", "-m", "v0.2.0")
        for name in ("PR_TITLE", "PR_BODY", "PR_NUMBER"):
            os.environ.pop(name, None)

    @contextlib.contextmanager
    def merge_ref(self, paths=("pstack/change.txt",)):
        base = self.head()
        self.git("checkout", "-q", "--detach")
        self.commit("Merge the pull request", paths)
        try:
            yield base
        finally:
            self.git("checkout", "-q", "main")

    def check(self, title, body="", number=4, *args, paths=("pstack/change.txt",)):
        os.environ.update({"PR_TITLE": title, "PR_BODY": body, "PR_NUMBER": str(number)})
        with self.merge_ref(paths) as base:
            return self.run_main("--check-pr", "--target", base, *args)

    def title_error(self, title):
        return f'error: title "{title}" is not Conventional Commits. Expected <type>[(<scope>)]: <description>, where type is one of {self.TYPES}.\n'

    def plan(self, level, version="v0.2.1", previous="v0.2.0", changes="1 change"):
        return f"this pull request asks for a {level} release\nmerged now, it would release {version} after {previous} ({changes})\n"

    def test_a_feat_asks_for_a_minor_release_against_the_newest_tag(self):
        code, out, err = self.check("feat(cli): add a flag")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.plan("minor", "v0.3.0") + "notes section: Features\nnotes line: - **cli:** add a flag (#4)\n")

    def test_every_listed_type_passes(self):
        for kind in self.TYPES.split(", "):
            with self.subTest(kind):
                code, out, _ = self.check(f"{kind}: do the thing")
                self.assertEqual(code, 0, out)

    def test_a_fix_and_a_plain_conventional_title_ask_for_a_patch(self):
        for title, section, line in (("fix(x): y", "Fixes", "- **x:** y (#9)"), ("docs: explain releases", "Other changes", "- explain releases (#9)")):
            with self.subTest(title):
                code, out, _ = self.check(title, number=9)
                self.assertEqual((code, out), (0, self.plan("patch") + f"notes section: {section}\nnotes line: {line}\n"))

    def test_a_title_that_is_not_conventional_fails_with_the_expected_form(self):
        for title in ("Update the README", "wip: stuff", "feat:no space", "feat: ", "feat(): x", "Merge upstream cursor/plugins"):
            with self.subTest(title):
                code, out, err = self.check(title)
                self.assertEqual((code, out, err), (1, self.title_error(title), ""))

    def test_a_dependabot_title_passes_as_a_patch_under_other_changes(self):
        code, out, _ = self.check("chore(deps): bump the actions group with 3 updates", number=11)
        self.assertEqual((code, out), (0, self.plan("patch") + "notes section: Other changes\nnotes line: - **deps:** bump the actions group with 3 updates (#11)\n"))

    def test_the_sync_title_passes_and_reads_as_an_upstream_note(self):
        with open(SYNC) as f:
            title = re.search(r'gh pr create .*--title "([^"]+)"', f.read()).group(1)
        code, out, _ = self.check(title, number=5)
        self.assertEqual((code, out), (0, self.plan("patch") + "notes section: Other changes\nnotes line: - **upstream:** merge cursor/plugins (#5)\n"))

    def test_the_sync_keeps_its_title_when_it_updates_an_open_pull_request(self):
        with open(SYNC) as f:
            script = f.read()
        created = re.search(r'gh pr create .*--title "([^"]+)"', script).group(1)
        edited = re.search(r'gh pr edit "\$open_pr" .*--title "([^"]+)"', script).group(1)
        self.assertEqual((created, edited), ("chore(upstream): merge cursor/plugins",) * 2)

    def test_the_merge_is_planned_on_top_of_what_the_base_has_not_released(self):
        self.commit("feat(cli): add a flag (#6)")
        code, out, err = self.check("fix: x")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, self.plan("patch", "v0.3.0", changes="2 changes") + "notes section: Fixes\nnotes line: - x (#4)\n")

    def test_target_is_the_base_commit_and_a_later_commit_is_not_planned(self):
        base = self.head()
        self.commit("feat(cli): add a flag (#6)")
        code, out, _ = self.check("fix: x", "", 4, "--target", base)
        self.assertEqual((code, out), (0, self.plan("patch") + "notes section: Fixes\nnotes line: - x (#4)\n"))

    def test_a_base_the_newest_tag_already_covers_adds_nothing_to_the_plan(self):
        code, out, _ = self.check("feat: x", "", 4, "--target", self.git("rev-parse", "HEAD~1"))
        self.assertEqual((code, out), (0, self.plan("minor", "v0.3.0") + "notes section: Features\nnotes line: - x (#4)\n"))

    def test_a_valid_release_as_sets_the_level_it_names(self):
        code, out, _ = self.check("docs: ready for 1.0", "Ready.\n\nRelease-As: 1.0.0\n")
        self.assertEqual((code, out.splitlines()[:2]), (0, self.plan("major", "v1.0.0").splitlines()))

    def test_a_pr_may_lower_its_own_level_with_release_as(self):
        code, out, _ = self.check("feat: small", "Release-As: 0.2.1\n")
        self.assertEqual((code, out.splitlines()[:2]), (0, self.plan("patch").splitlines()))

    def test_a_release_as_that_is_not_a_successor_fails_without_the_word_ignored(self):
        code, out, _ = self.check("fix: x", "Release-As: 9.9.9\n")
        self.assertEqual((code, out), (1, "error: Release-As 9.9.9 is not one of v0.2.1, v0.3.0, v1.0.0\n"))

    def test_a_fenced_release_as_is_ignored(self):
        body = "Documents the footer.\n\n```\nRelease-As: 9.9.9\n```\n"
        code, out, _ = self.check("docs: describe Release-As", body)
        self.assertEqual((code, out.splitlines()[:2]), (0, self.plan("patch").splitlines()))

    def test_a_breaking_marker_in_the_body_is_reported(self):
        code, out, _ = self.check("refactor(cli): rename the command", "Renames it.\n\nBREAKING CHANGE: pstack now starts as pst.\n")
        self.assertEqual((code, out), (0, self.plan("minor", "v0.3.0") + (
            "notes section: Breaking changes\n"
            "notes line: - **cli:** rename the command (#4)\n"
            "breaking: yes\n"
        )))

    def test_every_skip_marker_fails_because_no_release_would_run(self):
        for marker in ("[skip ci]", "[ci skip]", "[no ci]", "[skip actions]", "[actions skip]", "skip-checks: true"):
            for text in (marker, marker.upper(), marker.title()):
                with self.subTest(text):
                    code, out, err = self.check("fix: x", f"Fixes x.\n\n{text}\n")
                    self.assertEqual((code, err), (1, ""))
                    self.assertEqual(out, f'error: "{text.lower()}" in the title or body would make the merge commit skip CI, so no release would run\n')

    def test_a_skip_marker_in_the_title_fails_too(self):
        code, out, _ = self.check("fix: x [skip ci]")
        self.assertEqual((code, out), (1, 'error: "[skip ci]" in the title or body would make the merge commit skip CI, so no release would run\n'))

    def test_a_skip_marker_inside_a_fence_still_fails(self):
        code, out, _ = self.check("docs: x", "```\n[skip ci]\n```\n")
        self.assertEqual(code, 1, out)

    def test_skip_checks_must_be_a_whole_line_set_to_true(self):
        for body in ("skip-checks: false\n", "mention skip-checks: true in prose\n", "skip-checks: truely\n"):
            with self.subTest(body):
                code, out, _ = self.check("fix: x", body)
                self.assertEqual(code, 0, out)

    def test_a_body_written_with_windows_line_endings_is_read_the_same(self):
        code, out, _ = self.check("fix: x", "Fixes x.\r\n\r\nskip-checks: true\r\nRelease-As: 9.9.9\r\n")
        self.assertEqual((code, out), (1, (
            "error: Release-As 9.9.9 is not one of v0.2.1, v0.3.0, v1.0.0\n"
            'error: "skip-checks: true" in the title or body would make the merge commit skip CI, so no release would run\n'
        )))

    def test_every_error_is_listed_in_order(self):
        code, out, _ = self.check("Update the README", "Release-As: 3.0.0\n[no ci]\nskip-checks: true\n")
        self.assertEqual((code, out), (1, self.title_error("Update the README") + (
            "error: Release-As 3.0.0 is not one of v0.2.1, v0.3.0, v1.0.0\n"
            'error: "[no ci]" in the title or body would make the merge commit skip CI, so no release would run\n'
            'error: "skip-checks: true" in the title or body would make the merge commit skip CI, so no release would run\n'
        )))

    def test_the_levels_are_judged_against_the_newest_plain_tag(self):
        self.git("tag", "-a", "v0.9.0", "-m", "v0.9.0", "HEAD~1")
        self.git("tag", "v2")
        _, out, _ = self.check("feat: x")
        self.assertEqual(out.splitlines()[:2], self.plan("minor", "v0.10.0", "v0.9.0", "2 changes").splitlines())

    def test_shell_metacharacters_in_every_value_pass_through_the_environment_untouched(self):
        title = "fix(cli): handle `id` and $(touch pwned) in \"quotes\" & 'ticks'; echo $HOME | tee x > y"
        body = "Release-As: 1.0.0 $(touch pwned)\n```\n$(touch pwned2)\n```\n"
        os.environ.update({"PR_TITLE": title, "PR_BODY": body, "PR_NUMBER": "4"})
        with self.merge_ref() as base:
            proc = subprocess.run([sys.executable, SCRIPT, "--check-pr", "--target", base], capture_output=True, text=True, cwd=self.repo)
            self.assertEqual((proc.returncode, proc.stderr), (1, ""))
            self.assertEqual(proc.stdout, "error: Release-As 1.0.0 $(touch pwned) is not one of v0.2.1, v0.3.0, v1.0.0\n")
            os.environ["PR_BODY"] = "```\n$(touch pwned2)\n```\n"
            proc = subprocess.run([sys.executable, SCRIPT, "--check-pr", "--target", base], capture_output=True, text=True, cwd=self.repo)
            self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(proc.stdout, self.plan("patch") + (
            "notes section: Fixes\n"
            "notes line: - **cli:** handle `id` and $(touch pwned) in \"quotes\" & 'ticks'; echo $HOME | tee x > y (#4)\n"
        ))
        self.assertEqual(sorted(os.listdir(self.repo)), [".git", "pstack"])

    def test_the_text_comes_from_the_environment_and_never_from_argv(self):
        proc = subprocess.run([sys.executable, SCRIPT, "--check-pr", "fix: x"], capture_output=True, text=True, cwd=self.repo)
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertIn("unrecognized arguments: fix: x", proc.stderr)

    def test_a_check_needs_the_title_and_number_but_not_a_body(self):
        os.environ.update({"PR_NUMBER": "4"})
        code, out, err = self.run_main("--check-pr")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("PR_TITLE", err)
        os.environ.update({"PR_TITLE": "fix: x", "PR_NUMBER": "four"})
        code, out, err = self.run_main("--check-pr")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("PR_NUMBER", err)
        os.environ.update({"PR_NUMBER": "4"})
        with self.merge_ref() as base:
            code, out, _ = self.run_main("--check-pr", "--target", base)
        self.assertEqual((code, out.splitlines()[:2]), (0, self.plan("patch").splitlines()))

    def test_a_pull_request_that_changes_only_github_would_not_release(self):
        for paths, reason in (([".github/workflows/ci.yml"], "only .github/ changed"), ([], "no files changed")):
            with self.subTest(reason):
                code, out, err = self.check("chore(deps): bump actions", "", 7, paths=paths)
                self.assertEqual((code, out, err), (0, (
                    "this pull request asks for a patch release\n"
                    f"merged now, it would not release: {reason} since v0.2.0\n"
                    "notes section: Other changes\n"
                    "notes line: - **deps:** bump actions (#7)\n"
                ), ""))

    def test_a_check_cannot_publish(self):
        self.install_gh('echo called > "$FAKE_GH_DIR/argv"; cat > "$FAKE_GH_DIR/stdin"\n')
        os.environ.update({"PR_TITLE": "fix: x", "PR_NUMBER": "4"})
        proc = subprocess.run([sys.executable, SCRIPT, "--check-pr", "--publish", "--repo", "o/r"], capture_output=True, text=True, cwd=self.repo)
        self.assertEqual(proc.returncode, 2)
        self.assertIsNone(self.gh_calls())

    def test_a_check_needs_a_plain_tag(self):
        self.git("tag", "-d", "v0.2.0")
        self.git("tag", "-d", "v0.1.0")
        code, out, err = self.check("fix: x")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no plain vX.Y.Z tag", err)

    def test_an_annotation_escapes_a_percent_sign_in_a_title(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        code, out, _ = self.check("Update 50%0A done")
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[-1], (
            f"::error title=release.py::title \"Update 50%250A done\" is not Conventional Commits. Expected <type>[(<scope>)]: <description>, where type is one of {self.TYPES}."
        ))

    def test_errors_become_annotations_on_github_actions_and_go_to_the_step_summary(self):
        summary = os.path.join(self.tmp, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = summary
        os.environ["GITHUB_ACTIONS"] = "true"
        code, out, _ = self.check("Update the README", "[skip ci]\n")
        self.assertEqual(code, 1)
        reports = self.title_error("Update the README") + 'error: "[skip ci]" in the title or body would make the merge commit skip CI, so no release would run\n'
        self.assertEqual(out, reports + "".join(f"::error title=release.py::{line[len('error: '):]}\n" for line in reports.splitlines()))
        with open(summary) as f:
            self.assertEqual(f.read(), reports)


class AnnotationTest(unittest.TestCase):
    def test_a_percent_sign_and_both_line_breaks_are_escaped(self):
        self.assertEqual(release.annotation("warning", "a%b\r\nc\n%0A"), "::warning title=release.py::a%25b%0D%0Ac%0A%250A")

    def test_a_plain_message_is_left_alone(self):
        self.assertEqual(release.annotation("error", "#6: no change"), "::error title=release.py::#6: no change")


class PrWorkflowTest(unittest.TestCase):
    def setUp(self):
        with open(PR_WORKFLOW) as f:
            self.text = f.read()

    def test_it_runs_on_every_change_to_a_pull_request_with_read_only_access(self):
        self.assertRegex(self.text, re.compile(r"^name: pstack PR$", re.M))
        self.assertRegex(self.text, re.compile(r"^on:\n  pull_request:\n    types: \[opened, edited, synchronize, reopened\]$", re.M))
        self.assertRegex(self.text, re.compile(r"^permissions:\n  contents: read$", re.M))
        self.assertEqual(self.text.count("permissions:"), 1)

    def test_it_is_one_bounded_job_that_checks_out_the_tags_without_credentials(self):
        self.assertEqual(re.findall(r"^  ([A-Za-z0-9_-]+):[ \t]*$", self.text.split("\njobs:\n", 1)[1], re.M), ["title"])
        self.assertRegex(self.text, re.compile(r"^    name: Pull request title$", re.M))
        self.assertRegex(self.text, re.compile(r"^    timeout-minutes: 5$", re.M))
        self.assertRegex(self.text, re.compile(r"^          fetch-depth: 0$", re.M))
        self.assertRegex(self.text, re.compile(r"^          persist-credentials: false$", re.M))

    def test_the_title_body_and_base_reach_the_script_only_through_env(self):
        self.assertRegex(self.text, re.compile(
            r"^        env:\n"
            r"          PR_TITLE: \$\{\{ github\.event\.pull_request\.title \}\}\n"
            r"          PR_BODY: \$\{\{ github\.event\.pull_request\.body \}\}\n"
            r"          PR_NUMBER: \$\{\{ github\.event\.pull_request\.number \}\}\n"
            r"          BASE_SHA: \$\{\{ github\.event\.pull_request\.base\.sha \}\}\n"
            r'        run: python3 pstack/port/release\.py --check-pr --target "\$BASE_SHA"$', re.M))
        for line in self.text.splitlines():
            if line.lstrip().startswith("run:"):
                self.assertNotIn("${{", line)


def ci_jobs(workflow=CI):
    with open(workflow) as f:
        body = f.read().split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([A-Za-z0-9_-]+):[ \t]*$", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def steps(job):
    return job.split("\n    steps:\n", 1)[1].rstrip()


class CiWiringTest(unittest.TestCase):
    def test_the_gate_needs_every_other_job(self):
        jobs = ci_jobs()
        needs = re.search(r"^    needs: \[([^\]]*)\]", jobs["gate"], re.M).group(1)
        self.assertEqual(set(re.split(r"\s*,\s*", needs)), set(jobs) - {"gate", "release"})

    def test_the_gate_runs_even_when_a_job_it_needs_fails(self):
        self.assertRegex(ci_jobs()["gate"], re.compile(r"^    if: always\(\)$", re.M))

    def test_the_release_waits_for_the_gate_and_runs_only_on_main(self):
        job = ci_jobs()["release"]
        self.assertRegex(job, re.compile(r"^    needs: gate$", re.M))
        self.assertRegex(job, re.compile(r"^    if: github\.ref == 'refs/heads/main'$", re.M))

    def test_the_release_job_alone_can_write_contents(self):
        jobs = ci_jobs()
        self.assertRegex(jobs["release"], re.compile(r"^    permissions:\n      contents: write$", re.M))
        self.assertEqual([name for name, job in jobs.items() if "contents: write" in job], ["release"])

    def test_overlapping_releases_queue_instead_of_cancelling(self):
        self.assertRegex(
            ci_jobs()["release"],
            re.compile(r"^    concurrency:\n      group: pstack-release\n      queue: max$", re.M),
            "the release job's concurrency needs `queue: max`, so every merge releases in turn. "
            "The local actionlint rejects the key, but GitHub documents it, so do not delete it to quiet the linter",
        )

    def test_no_job_the_gate_needs_runs_on_macos(self):
        self.assertEqual([name for name, job in ci_jobs().items() if "macos" in job], [])

    def test_the_release_checks_out_the_full_history_without_keeping_credentials(self):
        job = ci_jobs()["release"]
        self.assertRegex(job, re.compile(r"^          fetch-depth: 0$", re.M))
        self.assertRegex(job, re.compile(r"^          persist-credentials: false$", re.M))

    def test_the_release_publishes_and_cannot_hang(self):
        job = ci_jobs()["release"]
        self.assertRegex(job, re.compile(r"^        run: .*\brelease\.py --publish\b", re.M))
        self.assertRegex(job, re.compile(r"^    timeout-minutes: [1-9][0-9]*$", re.M))


class MacosWorkflowTest(unittest.TestCase):
    def setUp(self):
        with open(MACOS_WORKFLOW) as f:
            self.text = f.read()

    def test_it_runs_on_main_daily_and_on_demand_but_never_on_a_pull_request(self):
        triggers = self.text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", triggers, re.M), ["push", "schedule", "workflow_dispatch"])
        self.assertRegex(triggers, re.compile(r"^    branches: \[main\]$", re.M))

    def test_it_reads_the_repository_and_nothing_more(self):
        self.assertRegex(self.text, re.compile(r"^permissions:\n  contents: read$", re.M))
        self.assertEqual(self.text.count("permissions:"), 1)

    def test_a_newer_commit_cancels_a_run_still_waiting_for_a_runner(self):
        self.assertRegex(self.text, re.compile(r"^concurrency:\n  group: pstack-macos-\$\{\{ github\.ref \}\}\n  cancel-in-progress: true$", re.M))

    def test_it_runs_the_gated_install_steps_with_the_stock_bash(self):
        jobs = ci_jobs(MACOS_WORKFLOW)
        self.assertEqual(list(jobs), ["install"])
        self.assertRegex(jobs["install"], re.compile(r"^    runs-on: macos-latest$", re.M))
        linux = steps(ci_jobs()["install"]).replace("install_test.sh\n", "install_test.sh /bin/bash\n")
        self.assertEqual(steps(jobs["install"]), linux)


if __name__ == "__main__":
    unittest.main()
