"""The pstack command, against a throwaway HOME with model detection faked."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

PORT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOT = os.path.dirname(os.path.dirname(PORT))
SCRIPT = os.path.join(PORT, "pstack")
BOOTSTRAP = os.path.join(PORT, "bootstrap.sh")
ALL = ["low", "medium", "high", "xhigh", "max"]
DETECTED = {
    "claude": {"cli": True, "models": {m: ALL for m in ("opus", "sonnet", "haiku", "fable")}},
    "codex": {"cli": True, "models": {"gpt-6-astra": ALL, "gpt-6.1-sol": ["low", "medium"]}},
}


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Env(unittest.TestCase):
    """A throwaway HOME with faked model detection, shared by the tests below."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        detect = os.path.join(self.tmp, "detect.json")
        with open(detect, "w") as f:
            json.dump(DETECTED, f)
        self.models = os.path.join(self.home, ".agents", "pstack-models.md")
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": self.home,
            "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
            "PSTACK_BIN": os.path.join(self.home, "bin"),
            "PSTACK_DETECT_JSON": detect,
        }

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pstack(self, *args, script=SCRIPT, code=0):
        proc = subprocess.run([script, *args], capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL)
        if code is not None:
            self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        return proc

    def models_file(self):
        with open(self.models) as f:
            return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in f if not line.startswith("#"))}

    def links(self, harness):
        d = os.path.join(self.home, f".{harness}", "skills")
        return sorted(n for n in os.listdir(d) if os.path.islink(os.path.join(d, n))) if os.path.isdir(d) else []

    def fake_installed(self, *harnesses):
        detected = {h: DETECTED[h] if h in harnesses else {"cli": False, "models": {}} for h in DETECTED}
        with open(self.env["PSTACK_DETECT_JSON"], "w") as f:
            json.dump(detected, f)

    def saved_harnesses(self):
        with open(os.path.join(self.home, ".config", "pstack", "state.json")) as f:
            return json.load(f)["harnesses"]

    def symlinks_under_home(self):
        return sorted(os.path.join(p, n) for p, dirs, files in os.walk(self.home) for n in dirs + files if os.path.islink(os.path.join(p, n)))


class CliTest(Env):
    def test_install_links_skills_and_the_command(self):
        self.pstack("install")
        self.assertIn("poteto-mode", self.links("claude"))
        self.assertEqual(self.links("claude"), self.links("codex"))
        self.assertEqual(os.path.realpath(os.path.join(self.home, "bin", "pstack")), SCRIPT)
        with open(os.path.join(self.home, ".config", "pstack", "state.json")) as f:
            self.assertEqual(json.load(f)["harnesses"], ["claude", "codex"])

    def test_install_for_codex_only(self):
        self.pstack("install", "--codex")
        self.assertEqual(self.links("claude"), [])
        self.assertIn("poteto-mode", self.links("codex"))
        self.pstack("configure", "--budget", "large", "--foreign-seats", "off", "--yes")
        self.assertEqual(self.models_file()["interrogate reviewers"], "codex:gpt-6-astra@xhigh, codex:gpt-6.1-sol@medium")

    def test_install_with_only_claude_installed_skips_codex(self):
        self.fake_installed("claude")
        self.assertIn("installed for claude.", self.pstack("install").stdout)
        self.assertIn("poteto-mode", self.links("claude"))
        self.assertEqual(self.links("codex"), [])
        self.assertEqual(self.saved_harnesses(), ["claude"])
        self.pstack("doctor", "--offline")

    def test_install_with_only_codex_installed_skips_claude(self):
        self.fake_installed("codex")
        self.assertIn("installed for codex.", self.pstack("install").stdout)
        self.assertIn("poteto-mode", self.links("codex"))
        self.assertEqual(self.links("claude"), [])
        self.assertEqual(self.saved_harnesses(), ["codex"])
        self.pstack("doctor", "--offline")

    def test_install_with_no_harness_cli_asks_for_one_and_links_nothing(self):
        self.fake_installed()
        proc = self.pstack("install", code=1)
        self.assertIn("--claude", proc.stderr)
        self.assertIn("--codex", proc.stderr)
        self.assertEqual(self.symlinks_under_home(), [])

    def test_install_with_a_flag_installs_without_the_cli(self):
        self.fake_installed()
        self.assertIn("installed for codex.", self.pstack("install", "--codex").stdout)
        self.assertEqual(self.links("claude"), [])
        self.assertIn("poteto-mode", self.links("codex"))

    def test_saved_choice_wins_over_the_installed_clis(self):
        self.pstack("install", "--claude")
        self.assertEqual(self.links("codex"), [])
        self.pstack("install")
        self.assertEqual(self.saved_harnesses(), ["claude"])
        self.assertEqual(self.links("codex"), [])

    def test_doctor_notes_an_installed_cli_pstack_is_not_set_up_for(self):
        self.fake_installed("claude")
        self.pstack("install")
        self.fake_installed("claude", "codex")
        lines = [line for line in self.pstack("doctor", "--offline").stdout.splitlines() if "pstack install --claude --codex" in line]
        self.assertEqual([line.split()[0] for line in lines], ["ok"])
        self.assertIn("codex", lines[0])

    def test_doctor_and_status_say_so_when_no_harness_is_selected(self):
        self.fake_installed()
        self.assertIn("neither claude nor codex is installed", self.pstack("doctor", "--offline", code=1).stdout)
        self.assertIn("harnesses: none selected", self.pstack("status").stdout)

    def test_configure_applies_budget_foreign_seats_and_effort_limits(self):
        self.pstack("install")
        self.pstack("configure", "--budget", "medium", "--foreign-seats", "off", "--yes")
        roles = self.models_file()
        self.assertEqual(roles["foreign seats"], "off")
        self.assertEqual(roles["interrogate reviewers"], "claude:opus@high, claude:sonnet@high, codex:gpt-6-astra@high, codex:gpt-6.1-sol@medium")
        self.assertEqual(roles["swarm workers"], "claude:sonnet@high, codex:gpt-6.1-sol@medium")
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".claude", "agents", "pstack-effort-high.md")))
        self.pstack("configure", "--budget", "large", "--foreign-seats", "on", "--yes")
        self.assertEqual(self.models_file()["interrogate reviewers"], "claude:opus@xhigh, codex:gpt-6-astra@xhigh")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "agents", "pstack-effort-high.md")))

    def test_configure_keeps_your_overrides_and_drops_retired_roles(self):
        self.pstack("install")
        self.pstack("configure", "--budget", "large", "--foreign-seats", "off", "--yes")
        with open(self.models, "a") as f:
            f.write("how critics: claude:opus@xhigh\n")
        text = open(self.models).read().replace("swarm workers: claude:sonnet@xhigh, codex:gpt-6.1-sol@medium", "swarm workers: claude:haiku@xhigh")
        with open(self.models, "w") as f:
            f.write(text)
        out = json.loads(self.pstack("configure", "--budget", "small", "--json", "--yes").stdout)
        self.assertEqual(out["kept_overrides"], ["swarm workers"])
        self.assertEqual(out["dropped"], ["how critics"])
        roles = self.models_file()
        self.assertEqual(roles["swarm workers"], "claude:haiku@medium")
        self.assertNotIn("how critics", roles)

    def test_configure_will_not_write_an_unavailable_model(self):
        self.pstack("install")
        proc = self.pstack("configure", "--set", "bug-fix=codex:gpt-9", "--yes", code=2)
        self.assertIn("needs a choice: bug-fix: codex:gpt-9 is not available", proc.stdout)
        self.assertFalse(os.path.exists(self.models))
        self.pstack("configure", "--set", "bug-fix=codex:gpt-9", "--yes", "--force")
        self.assertEqual(self.models_file()["bug-fix"], "codex:gpt-9@xhigh")
        self.pstack("configure", "--set", "not a role=claude:opus", "--yes", code=1)

    def test_doctor_finds_what_install_and_configure_fix(self):
        self.pstack("install")
        self.pstack("configure", "--budget", "large", "--foreign-seats", "off", "--yes")
        self.pstack("doctor", "--offline")
        os.remove(os.path.join(self.home, ".claude", "skills", "how"))
        self.assertIn("not linked", self.pstack("doctor", "--offline", code=1).stdout)
        self.pstack("install")
        os.remove(os.path.join(self.home, ".claude", "agents", "pstack-effort-xhigh.md"))
        self.assertIn("effort agents", self.pstack("doctor", "--offline", code=1).stdout)
        self.pstack("install")
        text = open(self.models).read().replace("bug-fix: claude:sonnet@xhigh", "bug-fix: claude:gpt-9@xhigh")
        with open(self.models, "w") as f:
            f.write(text)
        self.assertIn("bug-fix: claude:gpt-9@xhigh is not an available model", self.pstack("doctor", "--offline", code=1).stdout)

    def test_read_rules_merge_into_existing_settings(self):
        settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings))
        with open(settings, "w") as f:
            json.dump({"model": "opus", "permissions": {"allow": ["Bash(ls:*)", f"Read({ROOT}/**)"]}}, f)
        self.pstack("install", "--claude-read-rules")
        with open(settings) as f:
            data = json.load(f)
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["permissions"]["allow"], [
            "Bash(ls:*)",
            "Read(~/.agents/pstack/**)",
            "Read(~/.agents/pstack-models.md)",
            "Read(~/.claude/skills/**)",
            f"Read(/{ROOT}/**)",
        ])

    def test_doctor_flags_read_rules_that_never_matched(self):
        self.fake_installed("claude")
        self.pstack("install")
        with open(os.path.join(self.home, ".claude", "settings.json"), "w") as f:
            json.dump({"permissions": {"allow": ["Read(~/.agents/pstack/**)", "Read(~/.claude/skills/**)", f"Read({ROOT}/**)"]}}, f)
        self.assertIn("Claude Code will ask before reading pstack's files", self.pstack("doctor", "--offline").stdout)
        self.pstack("install", "--claude-read-rules")
        self.assertNotIn("Claude Code will ask before reading pstack's files", self.pstack("doctor", "--offline").stdout)

    def test_uninstall_removes_everything_it_made(self):
        self.pstack("install")
        self.pstack("configure", "--budget", "large", "--foreign-seats", "off", "--yes")
        self.pstack("uninstall")
        for d in (".claude", ".codex", ".agents", "bin"):
            found = [os.path.join(p, n) for p, dirs, files in os.walk(os.path.join(self.home, d)) for n in dirs + files if os.path.islink(os.path.join(p, n))]
            self.assertEqual(found, [], d)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "agents", "pstack-effort-xhigh.md")))
        self.assertTrue(os.path.exists(self.models))
        self.pstack("uninstall", "--purge", "--yes")
        self.assertFalse(os.path.exists(self.models))


class ReleaseTest(Env):
    """update and bootstrap.sh, against a small release repository with tags v0.1.0 and v0.2.0."""

    def setUp(self):
        super().setUp()
        src = os.path.join(self.tmp, "src")
        ignore = shutil.ignore_patterns("node_modules", "__pycache__", ".DS_Store")
        for name in ("pstack", "cursor-team-kit"):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(src, name), ignore=ignore)
        git(src, "init", "-q", "-b", "main")
        git(src, "add", "-A")
        git(src, "commit", "-qm", "first release")
        git(src, "tag", "v0.1.0")
        for message in ("second release", "unreleased work"):
            with open(os.path.join(src, "pstack", "port", "README.md"), "a") as f:
                f.write(f"\n{message}\n")
            git(src, "commit", "-qam", message)
            if message == "second release":
                git(src, "tag", "v0.2.0")
        self.remote = os.path.join(self.tmp, "remote.git")
        git(self.tmp, "clone", "-q", "--bare", src, self.remote)

    def test_bootstrap_installs_the_newest_release(self):
        managed = os.path.join(self.home, ".local", "share", "pstack")
        self.env.update(PSTACK_REPO=self.remote, PSTACK_HOME=managed)
        subprocess.run(["bash", BOOTSTRAP], env=self.env, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(git(managed, "describe", "--tags").strip(), "v0.2.0")
        self.assertEqual(os.path.realpath(os.path.join(self.home, "bin", "pstack")), os.path.join(managed, "pstack", "port", "pstack"))
        self.assertEqual(os.path.realpath(os.path.join(self.home, ".claude", "skills", "how")), os.path.join(managed, "pstack", "skills", "how"))

    def tag_stray_names_on_the_tip(self):
        for name in ("v0.9", "v1.0.0-rc.1"):
            git(self.remote, "tag", name, "main")

    def test_bootstrap_ignores_tags_that_are_not_plain_vX_Y_Z(self):
        self.tag_stray_names_on_the_tip()
        managed = os.path.join(self.home, ".local", "share", "pstack")
        self.env.update(PSTACK_REPO=self.remote, PSTACK_HOME=managed)
        subprocess.run(["bash", BOOTSTRAP], env=self.env, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(git(managed, "describe", "--tags").strip(), "v0.2.0")

    def test_bootstrap_without_any_plain_tag_follows_the_default_branch(self):
        for name in ("v0.1.0", "v0.2.0"):
            git(self.remote, "tag", "-d", name)
        self.tag_stray_names_on_the_tip()
        managed = os.path.join(self.home, ".local", "share", "pstack")
        self.env.update(PSTACK_REPO=self.remote, PSTACK_HOME=managed)
        subprocess.run(["bash", BOOTSTRAP], env=self.env, check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(git(managed, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")

    def test_bootstrap_refuses_a_home_inside_agents_skills(self):
        self.env.update(PSTACK_REPO=self.remote, PSTACK_HOME=os.path.join(self.home, ".agents", "skills", "pstack"))
        proc = subprocess.run(["bash", BOOTSTRAP], env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("inside ~/.agents/skills", proc.stderr)

    def test_update_moves_to_the_newest_release_then_the_tip(self):
        clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "clone", "-q", self.remote, clone)
        git(clone, "checkout", "-q", "v0.1.0")
        script = os.path.join(clone, "pstack", "port", "pstack")
        self.pstack("install", script=script)
        self.assertIn("v0.1.0 -> v0.2.0", self.pstack("update", script=script).stdout)
        self.pstack("update", "--head", script=script)
        self.assertEqual(git(clone, "log", "-1", "--format=%s").strip(), "unreleased work")

    def test_update_finishes_with_the_new_release_code(self):
        src = os.path.join(self.tmp, "src")
        script = os.path.join(src, "pstack", "port", "pstack")
        with open(script) as f:
            text = f.read()
        with open(script, "w") as f:
            f.write(text.replace("def cmd_doctor(args):\n", "def cmd_doctor(args):\n    print(\"doctor from v0.3.0\")\n", 1))
        git(src, "commit", "-qam", "third release")
        git(src, "tag", "v0.3.0")
        git(src, "push", "-q", self.remote, "main", "v0.3.0")
        clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "clone", "-q", self.remote, clone)
        git(clone, "checkout", "-q", "v0.2.0")
        script = os.path.join(clone, "pstack", "port", "pstack")
        self.pstack("install", script=script)
        out = self.pstack("update", script=script).stdout
        self.assertIn("v0.2.0 -> v0.3.0", out)
        self.assertIn("doctor from v0.3.0", out)

    def test_update_ignores_tags_that_are_not_plain_vX_Y_Z(self):
        self.tag_stray_names_on_the_tip()
        clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "clone", "-q", self.remote, clone)
        git(clone, "checkout", "-q", "v0.1.0")
        script = os.path.join(clone, "pstack", "port", "pstack")
        self.pstack("install", script=script)
        self.assertIn("v0.1.0 -> v0.2.0", self.pstack("update", script=script).stdout)

    def test_doctor_does_not_offer_a_tag_that_is_not_plain_vX_Y_Z(self):
        self.tag_stray_names_on_the_tip()
        clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "clone", "-q", self.remote, clone)
        git(clone, "checkout", "-q", "v0.2.0")
        script = os.path.join(clone, "pstack", "port", "pstack")
        self.pstack("install", script=script)
        self.assertNotIn("is available", self.pstack("doctor", script=script).stdout)

    def test_update_with_no_harness_cli_links_nothing(self):
        clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "clone", "-q", self.remote, clone)
        detected = {h: {"cli": False, "models": {}} for h in DETECTED}
        with open(self.env["PSTACK_DETECT_JSON"], "w") as f:
            json.dump(detected, f)
        proc = self.pstack("update", script=os.path.join(clone, "pstack", "port", "pstack"), code=1)
        self.assertIn("--claude", proc.stderr)
        links = [os.path.join(p, n) for p, dirs, files in os.walk(self.home) for n in dirs + files if os.path.islink(os.path.join(p, n))]
        self.assertEqual(links, [])

    def test_update_never_moves_a_branch_clone_back_to_an_older_release(self):
        clone = os.path.join(self.tmp, "clone")
        git(self.tmp, "clone", "-q", self.remote, clone)
        script = os.path.join(clone, "pstack", "port", "pstack")
        self.pstack("install", script=script)
        self.pstack("update", script=script)
        self.assertEqual(git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip(), "main")
        self.assertEqual(git(clone, "log", "-1", "--format=%s").strip(), "unreleased work")


if __name__ == "__main__":
    unittest.main()
