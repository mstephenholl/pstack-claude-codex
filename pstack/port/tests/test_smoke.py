"""smoke.py: reading what a headless Claude Code run prints."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
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
            "A successful rename leaves cached reads returning the old name in the same process.",
        ):
            self.assertTrue(self.finds_stale_cache_bug(answer), answer)

    def test_interrogate_rejects_an_answer_that_misses_the_bug(self):
        self.assertFalse(self.finds_stale_cache_bug("A CLI typo exits 0, and a failed save empties users.json."))


class BraceReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def evidence(self, command):
        path = os.path.join(self.tmp, "rollout.jsonl")
        call = {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": command})}}
        with open(path, "w") as f:
            f.write(json.dumps(call) + "\n")
        session = {"modified": "", "harness": "codex", "id": "s1", "path": path, "title": ""}
        with mock.patch.object(smoke, "sessions_under", return_value=[session]):
            return smoke.Evidence("codex", self.tmp, "")

    def test_read_harness_sees_a_brace_expanded_read(self):
        ev = self.evidence("cat ~/.agents/pstack/skills/{pstack-harness,arena}/SKILL.md")
        self.assertTrue(smoke.read_harness(ev)[0])

    def test_read_harness_fails_when_no_brace_alternative_is_the_harness(self):
        ev = self.evidence("cat ~/.agents/pstack/skills/{arena,interrogate}/SKILL.md")
        self.assertFalse(smoke.read_harness(ev)[0])

    def test_every_called_check_sees_a_brace_expanded_path(self):
        ev = self.evidence("cat ~/.agents/pstack/skills/poteto-mode/playbooks/{feature,bug-fix}.md")
        self.assertTrue(ev.called(r"playbooks/bug-fix\.md"))

    def test_shell_variables_and_json_objects_are_left_alone(self):
        ev = self.evidence("cat ${HOME}/x")
        self.assertTrue(ev.called(r"\$\{HOME\}/x"))
        ev = self.evidence('echo {"model":"gpt-6-astra","reasoning_effort":"high"}')
        self.assertTrue(ev.called(r'\{\\"model\\":\\"gpt-6-astra\\",\\"reasoning_effort\\":\\"high\\"\}'))


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


def write(path, text="", mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)
    return path


def make_tree(root, skills):
    for skill, files in skills.items():
        for rel, text in files.items():
            write(os.path.join(root, "pstack", "skills", skill, rel), text)
    return root


def read(path):
    with open(path) as f:
        return f.read()


USER_ONLY = "policy:\n  allow_implicit_invocation: false\n"
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, args):
    proc = smoke.sh(f"git -c user.name=t -c user.email=t@example.com {args}", cwd)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.patch_env(GIT_ENV)

    def patch_env(self, env):
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value


class ArmHomeTest(TmpCase):
    def setUp(self):
        super().setUp()
        self.real = os.path.join(self.tmp, "real-home")
        write(os.path.join(self.real, ".gitconfig"), "[user]\n\tname = fake\n")
        write(os.path.join(self.real, ".agents", "pstack-models.md"), "judgment: opus\n")
        write(os.path.join(self.real, ".config", "tool", "settings.toml"), "x = 1\n")
        self.installed = make_tree(os.path.join(self.tmp, "installed"), {"demo": {"SKILL.md": "installed demo\n"}})
        os.symlink(os.path.join(self.installed, "pstack"), os.path.join(self.real, ".agents", "pstack"))
        self.variant = make_tree(os.path.join(self.tmp, "variant"), {"demo": {"SKILL.md": "variant demo\n"}})
        self.codex_home = os.path.join(self.tmp, "codex-home")
        os.makedirs(self.codex_home)

    def arm(self, name, tree):
        home = os.path.join(self.tmp, name, "home")
        smoke.make_arm_home(home, tree, self.real)
        return home, smoke.arm_env(home, self.codex_home)

    def shell(self, env, command):
        return subprocess.run(["sh", "-c", command], env=env, capture_output=True, text=True).stdout

    def test_tilde_paths_resolve_into_the_arms_tree(self):
        _, variant_env = self.arm("variant", self.variant)
        _, base_env = self.arm("base", self.installed)
        read = "cat ~/.agents/pstack/skills/demo/SKILL.md"
        self.assertEqual(self.shell(variant_env, read), "variant demo\n")
        self.assertEqual(self.shell(base_env, read), "installed demo\n")

    def test_the_rest_of_the_real_home_is_still_reachable(self):
        _, env = self.arm("variant", self.variant)
        self.assertIn("name = fake", self.shell(env, "cat ~/.gitconfig"))
        self.assertEqual(self.shell(env, "cat ~/.agents/pstack-models.md"), "judgment: opus\n")
        self.assertEqual(self.shell(env, "cat ~/.config/tool/settings.toml"), "x = 1\n")

    def test_the_arm_env_points_home_at_the_arm_and_codex_at_the_real_codex_home(self):
        home, env = self.arm("variant", self.variant)
        self.assertEqual((env["HOME"], env["CODEX_HOME"]), (home, self.codex_home))
        self.assertEqual(env["PATH"], os.environ["PATH"])

    def test_a_write_under_the_arms_agents_dir_does_not_reach_the_real_home(self):
        _, env = self.arm("variant", self.variant)
        self.shell(env, "echo x > ~/.agents/scratch")
        self.assertFalse(os.path.exists(os.path.join(self.real, ".agents", "scratch")))

    def test_removing_an_arm_home_leaves_the_real_home_intact(self):
        home, _ = self.arm("variant", self.variant)
        smoke.remove_arm_home(home)
        self.assertFalse(os.path.exists(home))
        for rel in (".gitconfig", ".agents/pstack-models.md", ".config/tool/settings.toml", ".agents/pstack/skills/demo/SKILL.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.real, rel)), rel)
        self.assertTrue(os.path.isfile(os.path.join(self.variant, "pstack", "skills", "demo", "SKILL.md")))


class ScheduleTest(unittest.TestCase):
    def test_every_scenario_runs_in_both_arms_each_rep(self):
        plan = smoke.schedule(["a", "b", "c"], 4)
        expected = {(rep, name, arm) for rep in range(1, 5) for name in "abc" for arm in ("base", "variant")}
        self.assertEqual(len(plan), len(expected))
        self.assertEqual(set(plan), expected)

    def test_the_arm_that_goes_first_alternates_by_rep(self):
        plan = smoke.schedule(["a", "b"], 4)
        firsts = [next(arm for rep, _, arm in plan if rep == r) for r in range(1, 5)]
        self.assertEqual(firsts, ["base", "variant", "base", "variant"])

    def test_reps_are_submitted_in_order(self):
        reps = [rep for rep, _, _ in smoke.schedule(["a", "b"], 3)]
        self.assertEqual(reps, sorted(reps))


class MetricsTest(TmpCase):
    def test_lines_count_modified_and_untracked_files_against_the_start_commit(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git(repo, "init -q -b main")
        write(os.path.join(repo, "a.txt"), "one\ntwo\nthree\n")
        git(repo, "add -A")
        git(repo, "commit -qm start")
        start = git(repo, "rev-parse HEAD")
        write(os.path.join(repo, "a.txt"), "one\nTWO\nthree\n")
        git(repo, "commit -qam edit")
        write(os.path.join(repo, "new.txt"), "x\ny\n")
        self.assertEqual(smoke.line_metrics(repo, start), {"lines_added": 3, "lines_removed": 1})

    def test_lines_are_none_when_the_repo_is_missing(self):
        self.assertEqual(smoke.line_metrics(os.path.join(self.tmp, "nope"), "HEAD"), {"lines_added": None, "lines_removed": None})

    def test_output_tokens_sum_the_turn_completed_events_and_skip_noise(self):
        log = os.path.join(self.tmp, "run.log")
        write(log, "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":120}}',
            "warning: something on stderr",
            '{"type":"item.completed","item":{"type":"agent_message","text":"turn.completed output_tokens 999"}}',
            '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":30}}',
            "",
        ]))
        self.assertEqual(smoke.output_tokens(log), 150)

    def test_output_tokens_are_none_without_a_turn_completed_event(self):
        log = os.path.join(self.tmp, "run.log")
        write(log, '{"type":"thread.started","thread_id":"t1"}\nboom\n')
        self.assertIsNone(smoke.output_tokens(log))


def rec(arm, rep, checks, **metrics):
    details = ["read pstack-harness", "wrote a test", f"comments removed ({rep} of 7 left)"]
    return dict(arm=arm, rep=rep, scenario="how", harness="codex", passed=all(checks),
                checks=[{"ok": ok, "detail": d} for ok, d in zip(checks, details)], metrics=metrics)


ROOTS = {"base": "/tmp/base (abc1234)", "variant": "/tmp/variant (def5678+dirty)"}


def line_with(lines, text):
    found = [l for l in lines if text in l]
    assert len(found) == 1, (text, found)
    return found[0]


class ReportTest(unittest.TestCase):
    def setUp(self):
        base = [
            rec("base", 1, [True, True, True], minutes=1.0, output_tokens=100, lines_added=0, lines_removed=0),
            rec("base", 2, [True, True, True], minutes=2.0, output_tokens=100, lines_added=0, lines_removed=0),
            rec("base", 3, [True, True, True], minutes=3.0, output_tokens=100, lines_added=0, lines_removed=0),
        ]
        variant = [
            rec("variant", 1, [True, False, True], minutes=10.0, output_tokens=90, lines_added=4, lines_removed=0),
            rec("variant", 2, [True, True, True], minutes=11.0, output_tokens=100, lines_added=5, lines_removed=0),
            rec("variant", 3, [True, True, False], minutes=12.0, output_tokens=110, lines_added=6, lines_removed=0),
        ]
        self.lines = smoke.ab_report(base + variant, ROOTS, 3)
        self.text = "\n".join(self.lines)

    def test_the_header_names_the_runs_per_arm_and_each_arms_tree(self):
        self.assertRegex(self.text, r"3 runs per arm")
        self.assertIn(ROOTS["base"], self.text)
        self.assertIn(ROOTS["variant"], self.text)

    def test_it_shows_runs_passed_per_arm_as_k_over_n(self):
        self.assertRegex(line_with(self.lines, "runs passed"), r"base 3/3\s+variant 1/3")

    def test_it_counts_each_check_by_position_and_labels_it_from_the_first_row(self):
        self.assertRegex(line_with(self.lines, "read pstack-harness"), r"base 3/3\s+variant 3/3")
        self.assertRegex(line_with(self.lines, "wrote a test"), r"base 3/3\s+variant 2/3")
        self.assertRegex(line_with(self.lines, "comments removed (1 of 7 left)"), r"base 3/3\s+variant 2/3")
        self.assertNotIn("(2 of 7 left)", self.text)

    def test_it_shows_each_metrics_median_range_and_ratio_of_medians(self):
        line = line_with(self.lines, "minutes")
        self.assertRegex(line, r"base 2 \[1, 3\]\s+variant 11 \[10, 12\]\s+variant/base 5\.50x")
        self.assertNotIn("run-to-run spread", line)

    def test_it_says_so_only_when_the_two_ranges_overlap(self):
        line = line_with(self.lines, "output_tokens")
        self.assertRegex(line, r"base 100 \[100, 100\]\s+variant 100 \[90, 110\]\s+variant/base 1\.00x")
        self.assertIn("within run-to-run spread", line)
        self.assertNotIn("run-to-run spread", line_with(self.lines, "lines_added"))

    def test_the_ratio_is_n_a_when_the_base_median_is_zero_or_missing(self):
        self.assertRegex(line_with(self.lines, "lines_added"), r"variant 5 \[4, 6\]\s+variant/base n/a")
        rows = [rec("base", 1, [True], output_tokens=None, minutes=1.0), rec("variant", 1, [True], output_tokens=50, minutes=1.0)]
        line = line_with(smoke.ab_report(rows, ROOTS, 1), "output_tokens")
        self.assertRegex(line, r"base n/a\s+variant 50 \[50, 50\]\s+variant/base n/a")
        self.assertNotIn("run-to-run spread", line)

    def test_a_crashed_run_counts_as_failed_without_shifting_the_check_counts(self):
        crashed = dict(rec("variant", 2, []), passed=False, checks=[], error="run crashed: boom")
        rows = [rec("base", 1, [True, True, True], minutes=1.0), rec("variant", 1, [True, True, True], minutes=1.0), crashed]
        lines = smoke.ab_report(rows, ROOTS, 2)
        self.assertRegex(line_with(lines, "runs passed"), r"base 1/1\s+variant 1/2")
        self.assertRegex(line_with(lines, "read pstack-harness"), r"base 1/1\s+variant 1/1")

    def test_the_report_is_plain_ascii(self):
        self.assertTrue(self.text.isascii(), self.text)


class ConfoundWarningTest(TmpCase):
    def trees(self, variant_edits):
        files = {
            "pstack-harness": {"SKILL.md": "harness v1\n"},
            "poteto-mode": {"SKILL.md": "poteto v1\n", "agents/openai.yaml": USER_ONLY},
        }
        base = make_tree(os.path.join(self.tmp, "base"), files)
        variant = make_tree(os.path.join(self.tmp, "variant"), files)
        for skill, rel in variant_edits:
            write(os.path.join(variant, "pstack", "skills", skill, rel), "edited\n")
        return base, variant

    def test_an_edited_implicit_skill_is_named_with_the_reason(self):
        base, variant = self.trees([("pstack-harness", "SKILL.md")])
        text = "\n".join(smoke.confound_warnings(base, variant))
        self.assertIn("pstack-harness", text)
        self.assertIn("installed copy", text)
        self.assertNotIn("poteto-mode", text)

    def test_a_new_file_in_an_implicit_skill_counts_as_an_edit(self):
        base, variant = self.trees([("pstack-harness", "notes/extra.md")])
        self.assertIn("pstack-harness", "\n".join(smoke.confound_warnings(base, variant)))

    def test_an_edited_user_only_skill_is_silent(self):
        base, variant = self.trees([("poteto-mode", "SKILL.md")])
        self.assertEqual(smoke.confound_warnings(base, variant), [])

    def test_identical_trees_are_silent(self):
        base, variant = self.trees([])
        self.assertEqual(smoke.confound_warnings(base, variant), [])


class DescribeRootTest(TmpCase):
    def test_it_names_the_short_commit_and_flags_a_dirty_tree(self):
        root = make_tree(os.path.join(self.tmp, "tree"), {"demo": {"SKILL.md": "x\n"}})
        git(root, "init -q -b main")
        git(root, "add -A")
        git(root, "commit -qm start")
        short = git(root, "rev-parse --short HEAD")
        self.assertEqual(smoke.describe_root(root), f"{root} ({short})")
        write(os.path.join(root, "pstack", "skills", "demo", "SKILL.md"), "edited\n")
        self.assertEqual(smoke.describe_root(root), f"{root} ({short}+dirty)")

    def test_a_tree_outside_git_says_so(self):
        root = make_tree(os.path.join(self.tmp, "tree"), {"demo": {"SKILL.md": "x\n"}})
        self.assertIn("not a git checkout", smoke.describe_root(root))


FAKE_CODEX = """#!/bin/sh
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then last="$2"; fi
  shift
done
cat ~/.agents/pstack/skills/demo/SKILL.md > "$last"
echo added > added.txt
[ -n "$FAKE_CODEX_TOUCH" ] && echo x > ~/.agents/pstack/skills/demo/touched
echo '{"type":"thread.started","thread_id":"t1"}'
echo 'warning: noise that is not json'
echo '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":7}}'
exit 0
"""

TRIPWIRE_HARNESS = '#!/bin/sh\ntouch "$(dirname "$0")/started-$(basename "$0")"\n'


class RunCodexEnvTest(TmpCase):
    def fake_codex(self):
        write(os.path.join(self.tmp, "bin", "codex"), '#!/bin/sh\nprintf "%s %s\\n" "$HOME" "$CODEX_HOME" > "$SEEN"\n', 0o755)
        self.patch_env({"PATH": os.path.join(self.tmp, "bin") + os.pathsep + os.environ["PATH"], "SEEN": os.path.join(self.tmp, "seen")})
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        return repo

    def seen(self):
        return read(os.path.join(self.tmp, "seen"))

    def test_it_runs_codex_under_the_env_it_is_given(self):
        repo = self.fake_codex()
        smoke.run_codex("prompt", repo, io.StringIO(), env=dict(os.environ, HOME="/arm/home", CODEX_HOME="/real/codex"))
        self.assertEqual(self.seen(), "/arm/home /real/codex\n")

    def test_without_an_env_it_inherits_the_callers(self):
        repo = self.fake_codex()
        self.patch_env({"HOME": "/caller/home", "CODEX_HOME": "/caller/codex"})
        smoke.run_codex("prompt", repo, io.StringIO())
        self.assertEqual(self.seen(), "/caller/home /caller/codex\n")


class MainTest(TmpCase):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        files = lambda demo, harness: {
            "demo": {"SKILL.md": demo, "agents/openai.yaml": USER_ONLY},
            "pstack-harness": {"SKILL.md": harness},
        }
        self.base = make_tree(os.path.join(self.tmp, "installed"), files("base demo\n", "harness v1\n"))
        self.variant = make_tree(os.path.join(self.tmp, "variant"), files("variant demo\n", "harness v2\n"))
        for root in (self.base, self.variant):
            git(root, "init -q -b main")
            git(root, "add -A")
            git(root, "commit -qm start")
        write(os.path.join(self.variant, "notes.txt"), "uncommitted\n")
        write(os.path.join(self.home, ".gitconfig"), "[user]\n\tname = fake\n")
        write(os.path.join(self.home, ".agents", "pstack-models.md"), "judgment: opus\n")
        os.symlink(os.path.join(self.base, "pstack"), os.path.join(self.home, ".agents", "pstack"))
        self.codex_home = os.path.join(self.tmp, "codex-home")
        os.makedirs(self.codex_home)
        bin_dir = os.path.join(self.tmp, "bin")
        write(os.path.join(bin_dir, "codex"), FAKE_CODEX, 0o755)
        write(os.path.join(bin_dir, "claude"), TRIPWIRE_HARNESS, 0o755)
        self.patch_env({"PATH": bin_dir + os.pathsep + os.environ["PATH"], "HOME": self.home, "CODEX_HOME": self.codex_home})
        self.patch(smoke, "REAL_HOME", self.home)
        self.patch(smoke, "CODEX_HOME", self.codex_home)
        self.patch(smoke, "PSTACK", os.path.join(self.home, ".agents", "pstack"))
        self.patch(smoke, "sessions_under", lambda run_dir: [])
        self.deleted = self.patch(smoke, "delete_sessions", mock.Mock())
        self.forgot = self.patch(smoke, "forget_trust", mock.Mock())
        scenarios = {"demo": dict(skill="demo", task="read it", checks=[smoke.answer_matches(r"variant", "answers from the variant tree")])}
        patcher = mock.patch.dict(smoke.SCENARIOS, scenarios, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = os.path.join(self.tmp, "out")

    def main(self, *argv):
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["smoke.py", "--only", "demo", "--jobs", "1", "--out", self.out, *argv]):
            with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as exited:
                smoke.main()
        return exited.exception.code, stdout.getvalue()

    def results(self):
        rows = [json.loads(line) for line in read(os.path.join(self.out, "results.jsonl")).splitlines()]
        return sorted(rows, key=lambda r: (r["rep"] or 0, r["arm"] or ""))

    def test_ab_runs_are_submitted_interleaved_with_the_first_arm_alternating(self):
        order = []
        real_run_one = smoke.run_one

        def spy(name, harness, run_dir, env=None):
            order.append(os.path.relpath(run_dir, self.out))
            return real_run_one(name, harness, run_dir, env)

        with mock.patch.object(smoke, "run_one", spy):
            self.main("--variant", self.variant, "--runs", "2")
        self.assertEqual(order, ["base/r1/demo-codex", "variant/r1/demo-codex", "variant/r2/demo-codex", "base/r2/demo-codex"])

    def test_an_ab_run_records_every_run_and_prints_the_comparison(self):
        code, out = self.main("--variant", self.variant, "--runs", "2")
        self.assertEqual(code, 0, "failed checks are data, not an error")
        rows = self.results()
        self.assertEqual([(r["rep"], r["arm"], r["passed"]) for r in rows], [(1, "base", False), (1, "variant", True), (2, "base", False), (2, "variant", True)])
        for row in rows:
            self.assertEqual((row["scenario"], row["harness"]), ("demo", "codex"))
            self.assertEqual(row["checks"], [{"ok": row["arm"] == "variant", "detail": "answer answers from the variant tree"}])
            metrics = row["metrics"]
            self.assertEqual((metrics["output_tokens"], metrics["lines_added"], metrics["lines_removed"]), (7, 1, 0))
            self.assertIsInstance(metrics["minutes"], float)
        self.assertRegex(out, r"2 runs per arm")
        self.assertRegex(out, rf"base:\s+{re.escape(self.base)} \([0-9a-f]+\)\n")
        self.assertRegex(out, rf"variant:\s+{re.escape(self.variant)} \([0-9a-f]+\+dirty\)\n")
        self.assertRegex(out, r"runs passed\s+base 0/2\s+variant 2/2")

    def test_each_arm_runs_under_its_own_home_and_the_homes_are_removed_after(self):
        self.main("--variant", self.variant, "--runs", "1")
        self.assertEqual(read(os.path.join(self.out, "base", "r1", "demo-codex", "answer.md")), "base demo\n")
        for arm in ("base", "variant"):
            self.assertFalse(os.path.exists(os.path.join(self.out, arm, "home")), arm)
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".gitconfig")))
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".agents", "pstack", "skills", "demo", "SKILL.md")))

    def test_passing_runs_are_deleted_and_failing_runs_kept(self):
        self.main("--variant", self.variant, "--runs", "1")
        self.assertFalse(os.path.exists(os.path.join(self.out, "variant", "r1", "demo-codex")))
        self.assertTrue(os.path.isdir(os.path.join(self.out, "base", "r1", "demo-codex", "repo")))
        self.assertEqual([c.args[0] for c in self.deleted.call_args_list], [os.path.join(self.out, "variant", "r1", "demo-codex")])
        self.forgot.assert_called_once_with(self.out)

    def test_keep_keeps_passing_runs_too(self):
        self.main("--variant", self.variant, "--runs", "1", "--keep")
        self.assertTrue(os.path.isdir(os.path.join(self.out, "variant", "r1", "demo-codex", "repo")))
        self.deleted.assert_not_called()

    def test_it_warns_before_running_when_the_variant_edits_an_implicit_skill(self):
        _, out = self.main("--variant", self.variant, "--runs", "1")
        warning = next(l for l in out.splitlines() if "pstack-harness" in l)
        self.assertIn("installed copy", warning)
        self.assertLess(out.index(warning), out.index("PASS  demo"))
        self.assertEqual(out.count("installed copy"), 1)

    def test_it_warns_when_a_run_changes_either_tree(self):
        self.patch_env({"FAKE_CODEX_TOUCH": "1"})
        _, out = self.main("--variant", self.variant, "--runs", "1")
        for root in (self.base, self.variant):
            self.assertRegex(out, rf"WARNING: .*{re.escape(root)}.* changed during the run")

    def test_it_stays_quiet_when_no_run_changes_the_trees(self):
        _, out = self.main("--variant", self.variant, "--runs", "1")
        self.assertNotIn("changed during the run", out)

    def test_a_run_that_crashes_is_recorded_and_makes_the_exit_nonzero(self):
        with mock.patch.object(smoke, "make_fixture", side_effect=RuntimeError("boom")):
            code, out = self.main("--variant", self.variant, "--runs", "1")
        self.assertEqual(code, 1)
        rows = self.results()
        self.assertEqual([r["passed"] for r in rows], [False, False])
        self.assertTrue(all("boom" in r["error"] and r["checks"] == [] for r in rows))
        self.assertRegex(out, r"runs passed\s+base 0/1\s+variant 0/1")

    def test_plain_mode_keeps_its_layout_and_output_and_adds_results_jsonl(self):
        code, out = self.main("--harness", "codex")
        lines = out.splitlines()
        self.assertEqual(lines[0], f"runs in {self.out}")
        self.assertRegex(lines[1], r"^FAIL  demo {23}codex  +\d+\.\d min$")
        self.assertEqual(lines[3], "FAIL  demo" + " " * 23 + "codex  missing: answer answers from the variant tree")
        self.assertEqual(lines[-1], f"0/1 passed. Failed runs and their sessions are kept in {self.out}.")
        self.assertEqual(code, 1)
        self.assertEqual(sorted(os.listdir(self.out)), ["demo-codex", "results.jsonl"])
        self.assertEqual(read(os.path.join(self.out, "demo-codex", "answer.md")), "base demo\n", "plain runs keep the caller's HOME")
        [row] = self.results()
        self.assertEqual((row["arm"], row["rep"], row["scenario"], row["harness"], row["passed"]), (None, None, "demo", "codex", False))
        self.assertEqual(row["metrics"]["output_tokens"], 7)

    def test_recheck_reads_both_layouts(self):
        for rel, answer in (("demo-codex", "variant demo"), ("base/r1/demo-codex", "base demo")):
            write(os.path.join(self.out, rel, "answer.md"), answer)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            smoke.recheck(self.out)
        self.assertRegex(stdout.getvalue(), r"(?m)^PASS  demo +codex +ok$")
        self.assertRegex(stdout.getvalue(), r"(?m)^FAIL  demo +codex +base +r1 +missing: answer answers from the variant tree$")


class CliTest(TmpCase):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        for harness in ("claude", "codex"):
            write(os.path.join(self.bin, harness), TRIPWIRE_HARNESS, 0o755)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".codex"))
        self.tree = make_tree(os.path.join(self.tmp, "tree"), {"demo": {"SKILL.md": "x\n"}})
        self.bare = os.path.join(self.tmp, "bare")
        os.makedirs(self.bare)

    def smoke(self, *args):
        env = dict(os.environ, HOME=self.home, CODEX_HOME=os.path.join(self.home, ".codex"), PATH=self.bin + os.pathsep + os.environ["PATH"])
        proc = subprocess.run([sys.executable, SCRIPT, "--out", os.path.join(self.tmp, "out"), *args], env=env, capture_output=True, text=True)
        self.assertEqual([f for f in os.listdir(self.bin) if f.startswith("started-")], [], "a harness was started")
        return proc

    def test_claude_with_a_variant_exits_nonzero_and_says_why(self):
        proc = self.smoke("--variant", self.tree, "--base", self.tree, "--harness", "claude")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Claude Code loads pstack from upstream's plugin", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))

    def test_runs_without_a_variant_is_a_usage_error(self):
        proc = self.smoke("--runs", "3")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--runs", proc.stderr)

    def test_a_tree_without_pstack_skills_is_refused_by_name(self):
        for variant, base in ((self.bare, self.tree), (self.tree, self.bare)):
            proc = self.smoke("--variant", variant, "--base", base)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(os.path.join(self.bare, "pstack", "skills"), proc.stderr)

    def test_the_default_base_is_the_parent_of_the_installed_pstack_and_must_hold_skills_too(self):
        proc = self.smoke("--variant", self.tree)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(os.path.join(self.home, ".agents", "pstack", "skills"), proc.stderr)


if __name__ == "__main__":
    unittest.main()
