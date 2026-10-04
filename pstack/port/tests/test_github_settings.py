"""github.json and github-settings.py: the repository settings the release process depends on, the script that applies them, and the check that finds drift."""

import fnmatch
import glob
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
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
CI = os.path.join(WORKFLOWS, "pstack-ci.yml")
SETTINGS_WORKFLOW = os.path.join(WORKFLOWS, "pstack-settings.yml")
GITHUB_JSON = os.path.join(PORT, "github.json")
GITHUB_SETTINGS = os.path.join(PORT, "github-settings.py")
SYNC = os.path.join(PORT, "sync-upstream.sh")
ACTIONS_APP_ID = 15368
FIRST_PARTY_OWNERS = ("actions", "github")


def github_config():
    with open(GITHUB_JSON) as f:
        return json.load(f)


def ruleset(name):
    return next(r for r in github_config()["rulesets"] if r["name"] == name)


def endpoint(path):
    return next(e for e in github_config()["endpoints"] if e["path"] == path)


def rule_types(rs):
    return [rule["type"] for rule in rs["rules"]]


USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)[ \t]*(?:#[ \t]*(.*?))?[ \t]*$", re.M)
PINNED = re.compile(r"[^@\s]+@[0-9a-f]{40}")
VERSION_COMMENT = re.compile(r"v\d+\.\d+\.\d+")


def unpinned_uses(text):
    return [m.group(0).strip() for m in USES.finditer(text) if not (PINNED.fullmatch(m.group(1)) and VERSION_COMMENT.fullmatch(m.group(2) or ""))]


def unallowed_actions(text, patterns):
    used = [m.group(1) for m in USES.finditer(text)]
    third_party = [u for u in used if u.split("/", 1)[0] not in FIRST_PARTY_OWNERS]
    return [u for u in third_party if not any(fnmatch.fnmatchcase(u, p) for p in patterns)]


def workflow_texts():
    texts = {}
    for path in glob.glob(os.path.join(WORKFLOWS, "pstack-*.yml")):
        with open(path) as f:
            texts[os.path.basename(path)] = f.read()
    return texts


def job_name(workflow, job):
    with open(os.path.join(WORKFLOWS, workflow)) as f:
        return re.search(rf"^  {job}:\n(?:    .*\n)*?    name: (.+)$", f.read(), re.M).group(1)


class RepositoryWiringTest(unittest.TestCase):
    def test_every_action_is_pinned_to_a_commit_with_a_version_comment(self):
        texts = workflow_texts()
        self.assertTrue(texts)
        for name, text in texts.items():
            self.assertEqual(unpinned_uses(text), [], name)

    def test_a_tag_a_branch_or_a_missing_version_comment_is_unpinned(self):
        sha = "11d5960a326750d5838078e36cf38b85af677262"
        text = (
            f"      - uses: actions/checkout@{sha} # v4.4.0\n"
            "      - uses: actions/setup-python@v5\n"
            "      - uses: oven-sh/setup-bun@main # v2.2.0\n"
            f"        uses: actions/cache@{sha}\n"
            f"      - uses: actions/cache@{sha[:39]} # v4.0.0\n"
            "      - uses: ./local-action\n"
        )
        self.assertEqual(unpinned_uses(text), [
            "- uses: actions/setup-python@v5",
            "- uses: oven-sh/setup-bun@main # v2.2.0",
            f"uses: actions/cache@{sha}",
            f"- uses: actions/cache@{sha[:39]} # v4.0.0",
            "- uses: ./local-action",
        ])

    def test_every_third_party_action_is_allowed_and_pinning_is_required(self):
        permissions = endpoint("actions/permissions")["body"]
        allowed = endpoint("actions/permissions/selected-actions")["body"]
        self.assertEqual((permissions["allowed_actions"], permissions["sha_pinning_required"]), ("selected", True))
        self.assertIs(allowed["github_owned_allowed"], True)
        for name, text in workflow_texts().items():
            self.assertEqual(unallowed_actions(text, allowed["patterns_allowed"]), [], name)

    def test_a_third_party_action_missing_from_the_allowed_patterns_is_found(self):
        sha = "0c5077e51419868618aeaa5fe8019c62421857d6"
        text = f"      - uses: oven-sh/setup-bun@{sha} # v2.2.0\n      - uses: actions/checkout@{sha} # v4.4.0\n      - uses: github/codeql-action/init@{sha} # v3.0.0\n"
        self.assertEqual(unallowed_actions(text, []), [f"oven-sh/setup-bun@{sha}"])
        self.assertEqual(unallowed_actions(text, ["oven-sh/setup-bun@*"]), [])
        self.assertEqual(unallowed_actions(text, ["oven-sh/other@*"]), [f"oven-sh/setup-bun@{sha}"])

    def test_main_requires_the_ci_gate_and_the_title_check_from_github_actions(self):
        required = next(r for r in ruleset("main")["rules"] if r["type"] == "required_status_checks")
        self.assertEqual(
            required["parameters"]["required_status_checks"],
            [{"context": name, "integration_id": ACTIONS_APP_ID} for name in (job_name("pstack-ci.yml", "gate"), job_name("pstack-pr.yml", "title"))],
        )

    def test_only_merge_commits_are_allowed(self):
        settings = github_config()["settings"]
        self.assertEqual([settings["allow_merge_commit"], settings["allow_squash_merge"], settings["allow_rebase_merge"]], [True, False, False])
        pull_request = next(r for r in ruleset("main")["rules"] if r["type"] == "pull_request")
        self.assertEqual(pull_request["parameters"]["allowed_merge_methods"], ["merge"])
        for rs in github_config()["rulesets"]:
            self.assertNotIn("required_linear_history", rule_types(rs))

    def test_the_release_job_can_still_create_tags(self):
        rs = ruleset("release tags")
        self.assertEqual((rs["target"], rs["conditions"]["ref_name"]["include"]), ("tag", ["refs/tags/v*"]))
        self.assertNotIn("creation", rule_types(rs))
        self.assertTrue({"update", "deletion"} <= set(rule_types(rs)))

    def test_no_ruleset_has_a_bypass_actor(self):
        rulesets = github_config()["rulesets"]
        self.assertTrue(rulesets)
        self.assertTrue(all(not rs["bypass_actors"] for rs in rulesets))

    def test_issues_are_on_for_the_upstream_sync_to_open_one(self):
        with open(SYNC) as f:
            self.assertIn("gh issue create", f.read())
        self.assertIs(github_config()["settings"]["has_issues"], True)

    def test_private_vulnerability_reporting_is_on_for_the_security_policy_to_point_at(self):
        self.assertIs(github_config()["features"]["private-vulnerability-reporting"], True)
        with open(os.path.join(ROOT, ".github", "SECURITY.md")) as f:
            self.assertIn("Report a vulnerability", f.read())


class SettingsWorkflowTest(unittest.TestCase):
    def setUp(self):
        with open(SETTINGS_WORKFLOW) as f:
            self.text = f.read()

    def test_it_runs_daily_on_demand_and_smoke_tests_the_checker_on_a_pull_request(self):
        self.assertRegex(self.text, re.compile(r"^name: pstack settings$", re.M))
        self.assertRegex(self.text, re.compile(r'^  schedule:\n    - cron: "41 9 \* \* \*"$', re.M))
        self.assertRegex(self.text, re.compile(r"^  workflow_dispatch:$", re.M))
        paths = re.search(r"^  pull_request:\n    paths:\n((?:      - .+\n)+)", self.text, re.M).group(1)
        watched = re.findall(r"- (\S+)", paths)
        self.assertEqual(sorted(watched), sorted(["pstack/port/github-settings.py", ".github/workflows/pstack-settings.yml"]))
        for path in watched:
            self.assertTrue(os.path.exists(os.path.join(ROOT, path)), path)

    def test_it_reads_the_repository_and_nothing_more(self):
        self.assertRegex(self.text, re.compile(r"^permissions:\n  contents: read$", re.M))
        self.assertEqual(self.text.count("permissions:"), 1)
        self.assertNotIn("security-events", self.text)

    def test_it_is_one_bounded_job_that_checks_with_the_settings_token_or_else_the_actions_token(self):
        self.assertEqual(re.findall(r"^  ([A-Za-z0-9_-]+):[ \t]*$", self.text.split("\njobs:\n", 1)[1], re.M), ["settings"])
        self.assertRegex(self.text, re.compile(r"^    name: Settings match github\.json$", re.M))
        self.assertRegex(self.text, re.compile(r"^    timeout-minutes: 5$", re.M))
        self.assertRegex(self.text, re.compile(r"^          persist-credentials: false$", re.M))
        self.assertRegex(self.text, re.compile(
            r"^        env:\n          GH_TOKEN: \$\{\{ secrets\.PSTACK_SETTINGS_TOKEN \|\| github\.token \}\}\n"
            r"        run: python3 pstack/port/github-settings\.py --check$", re.M))


FAKE_GH = """#!{python}
import json, os, sys
argv = sys.argv[1:]
assert argv[:3] == ["api", "-i", "-X"], argv
method, path = argv[3], argv[4]
body = json.loads(sys.stdin.read()) if "--input" in argv else None
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps([method, path, body]) + "\\n")
with open(os.environ["FAKE_GH_REPLIES"]) as f:
    replies = json.load(f)
status, reply = replies.get(method + " " + path, [404 if method == "GET" else 200, {{"message": "Not Found"}}])
head = "HTTP/2.0 " + str(status) + " Whatever\\r\\nContent-Type: application/json\\r\\n\\r\\n"
sys.stdout.write(head + ("" if reply is None else reply if isinstance(reply, str) else json.dumps(reply)))
sys.exit(0 if status < 400 else 1)
"""

EXTRA_RULE_PARAMETERS = {
    "pull_request": {"required_reviewers": [], "require_extra_approval_for_unattributed_changes": True},
    "update": {"update_allows_fetch_and_merge": False},
}


def live_ruleset(want, ruleset_id):
    rules = [{**rule, "parameters": {**rule.get("parameters", {}), **EXTRA_RULE_PARAMETERS.get(rule["type"], {})}} if rule["type"] in EXTRA_RULE_PARAMETERS else dict(rule) for rule in want["rules"]]
    return {**want, "id": ruleset_id, "rules": rules, "source_type": "Repository"}


def live_state(config, repo="o/r", admin=True):
    replies = {f"GET repos/{repo}": [200, {**config["settings"], "id": 1, "permissions": {"admin": admin}}]}
    for e in config["endpoints"]:
        replies[f"GET repos/{repo}/{e['path']}"] = [200, {**e["body"], "updated_at": "2026-10-01T00:00:00Z"}]
    for name, on in config["features"].items():
        if name == "vulnerability-alerts":
            replies[f"GET repos/{repo}/{name}"] = [204, None] if on else [404, {"message": "Not Found"}]
        else:
            replies[f"GET repos/{repo}/{name}"] = [200, {"enabled": on}]
    replies[f"GET repos/{repo}/rulesets?per_page=100"] = [200, [{"id": 10 + i, "name": rs["name"]} for i, rs in enumerate(config["rulesets"])]]
    for i, rs in enumerate(config["rulesets"]):
        replies[f"GET repos/{repo}/rulesets/{10 + i}"] = [200, live_ruleset(rs, 10 + i)]
    return replies


SMALL = {
    "settings": {"has_wiki": False},
    "endpoints": [
        {"path": "topics", "method": "PUT", "body": {"names": ["a"]}},
        {"path": "code-scanning/default-setup", "method": "PATCH", "body": {"state": "configured"}},
    ],
    "features": {"vulnerability-alerts": True, "automated-security-fixes": False},
    "rulesets": [
        {"name": "main", "target": "branch", "enforcement": "active", "conditions": {}, "bypass_actors": [], "rules": [{"type": "deletion"}]},
        {"name": "tags", "target": "tag", "enforcement": "active", "conditions": {}, "bypass_actors": [], "rules": [{"type": "update"}]},
    ],
}


TYPOS = {
    "settings": {"has_wikis": False},
    "endpoints": [{"path": "actions/permissions/fork-pr-contributer-approval", "method": "PUT", "body": {"approval_policy": "all_external_contributors"}}],
    "features": {},
    "rulesets": [],
}
PIN = "a" * 40
PINNED_WORKFLOW = f"jobs:\n  x:\n    steps:\n      - uses: actions/checkout@{PIN} # v4.4.0\n      - uses: ./local\n      - uses: docker://alpine:3\n"
REQUIRES_PINNING = {**SMALL, "endpoints": [{"path": "actions/permissions", "method": "PUT", "body": {"sha_pinning_required": True}}]}


def workflow_replies(files, repo="o/r", branch="main"):
    base = f"GET repos/{repo}/contents/.github/workflows"
    replies = {
        f"GET repos/{repo}": [200, {"default_branch": branch}],
        f"{base}?ref={branch}": [200, [{"name": name, "path": f".github/workflows/{name}", "type": "file"} for name in files]],
    }
    replies.update({f"{base}/{name}?ref={branch}": [200, text] for name, text in files.items() if name.endswith((".yml", ".yaml"))})
    return replies


class ScriptCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        gh = os.path.join(bin_dir, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH.format(python=sys.executable))
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.tmp, "log")
        self.replies_path = os.path.join(self.tmp, "replies.json")
        self.env = {**os.environ, "PATH": bin_dir + os.pathsep + os.environ["PATH"], "FAKE_GH_LOG": self.log, "FAKE_GH_REPLIES": self.replies_path}
        for name in ("GITHUB_REPOSITORY", "GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY", "GH_REPO"):
            self.env.pop(name, None)

    def run_script(self, *args, replies=None, **env):
        with open(self.replies_path, "w") as f:
            json.dump(replies or {}, f)
        proc = subprocess.run([sys.executable, GITHUB_SETTINGS, *args], capture_output=True, text=True, env={**self.env, **env})
        calls = []
        if os.path.exists(self.log):
            with open(self.log) as f:
                calls = [tuple(json.loads(line)) for line in f.read().splitlines()]
        return proc, calls

    def scratch_config(self, config):
        path = os.path.join(self.tmp, "scratch.json")
        with open(path, "w") as f:
            json.dump(config, f)
        return path

    def small_config(self):
        return self.scratch_config(SMALL)


class ApplyTest(ScriptCase):
    def test_it_applies_settings_endpoints_features_then_rulesets_in_that_order(self):
        proc, calls = self.run_script("--repo", "o/r", "--config", self.small_config(), replies={
            "GET repos/o/r/rulesets?per_page=100": [200, [{"id": 7, "name": "main"}]],
        })
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(calls, [
            ("PATCH", "repos/o/r", {"has_wiki": False}),
            ("PUT", "repos/o/r/topics", {"names": ["a"]}),
            ("PATCH", "repos/o/r/code-scanning/default-setup", {"state": "configured"}),
            ("PUT", "repos/o/r/vulnerability-alerts", None),
            ("DELETE", "repos/o/r/automated-security-fixes", None),
            ("GET", "repos/o/r/rulesets?per_page=100", None),
            ("PUT", "repos/o/r/rulesets/7", SMALL["rulesets"][0]),
            ("POST", "repos/o/r/rulesets", SMALL["rulesets"][1]),
        ])
        self.assertEqual(proc.stdout.splitlines(), [
            "PATCH repos/o/r",
            "PUT repos/o/r/topics",
            "PATCH repos/o/r/code-scanning/default-setup",
            "PUT repos/o/r/vulnerability-alerts",
            "DELETE repos/o/r/automated-security-fixes",
            "PUT repos/o/r/rulesets/7",
            "POST repos/o/r/rulesets",
        ])

    def test_running_it_again_updates_both_rulesets_and_creates_nothing(self):
        _, calls = self.run_script("--repo", "o/r", "--config", self.small_config(), replies={
            "GET repos/o/r/rulesets?per_page=100": [200, [{"id": 7, "name": "main"}, {"id": 8, "name": "tags"}]],
        })
        self.assertEqual([c[:2] for c in calls[-2:]], [("PUT", "repos/o/r/rulesets/7"), ("PUT", "repos/o/r/rulesets/8")])
        self.assertNotIn("POST", [c[0] for c in calls])

    def test_it_sends_every_part_of_github_json(self):
        config = github_config()
        proc, calls = self.run_script("--repo", "o/r", replies={**workflow_replies({"ci.yml": PINNED_WORKFLOW}), "GET repos/o/r/rulesets?per_page=100": [200, []]})
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        expected = [("GET", "repos/o/r", None), ("GET", "repos/o/r/contents/.github/workflows?ref=main", None), ("GET", "repos/o/r/contents/.github/workflows/ci.yml?ref=main", None)]
        expected += [("PATCH", "repos/o/r", config["settings"])]
        expected += [(e["method"], f"repos/o/r/{e['path']}", e["body"]) for e in config["endpoints"]]
        expected += [("PUT" if on else "DELETE", f"repos/o/r/{name}", None) for name, on in config["features"].items()]
        expected += [("GET", "repos/o/r/rulesets?per_page=100", None)]
        expected += [("POST", "repos/o/r/rulesets", rs) for rs in config["rulesets"]]
        self.assertEqual(calls, expected)

    def test_a_dry_run_reads_but_writes_nothing(self):
        proc, calls = self.run_script("--repo", "o/r", "--dry-run", "--config", self.small_config(), replies={
            "GET repos/o/r/rulesets?per_page=100": [200, [{"id": 7, "name": "main"}]],
        })
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(calls, [("GET", "repos/o/r/rulesets?per_page=100", None)])
        self.assertEqual(proc.stdout.splitlines(), [
            'dry run: PATCH repos/o/r {"has_wiki": false}',
            'dry run: PUT repos/o/r/topics {"names": ["a"]}',
            'dry run: PATCH repos/o/r/code-scanning/default-setup {"state": "configured"}',
            "dry run: PUT repos/o/r/vulnerability-alerts",
            "dry run: DELETE repos/o/r/automated-security-fixes",
            "dry run: PUT repos/o/r/rulesets/7 " + json.dumps(SMALL["rulesets"][0]),
            "dry run: POST repos/o/r/rulesets " + json.dumps(SMALL["rulesets"][1]),
        ])

    def test_a_failed_write_stops_the_run_and_names_the_request(self):
        proc, calls = self.run_script("--repo", "o/r", "--config", self.small_config(), replies={
            "PUT repos/o/r/topics": [422, {"message": "Validation Failed"}],
        })
        self.assertEqual((proc.returncode, proc.stdout), (2, "PATCH repos/o/r\n"))
        self.assertEqual(proc.stderr, "github-settings.py: PUT repos/o/r/topics failed with HTTP 422, Validation Failed\n")
        self.assertEqual(len(calls), 2)

    def test_the_repository_is_required_and_never_guessed(self):
        proc, calls = self.run_script("--config", self.small_config())
        self.assertEqual((proc.returncode, proc.stdout, calls), (2, "", []))
        self.assertIn("--repo", proc.stderr)

    def test_the_repository_defaults_to_github_repository(self):
        proc, calls = self.run_script("--dry-run", "--config", self.small_config(), GITHUB_REPOSITORY="env/repo", replies={
            "GET repos/env/repo/rulesets?per_page=100": [200, []],
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines()[0], 'dry run: PATCH repos/env/repo {"has_wiki": false}')

    def test_a_repository_that_is_not_owner_slash_name_is_refused(self):
        for repo in ("repo", "o/r/extra", "o/r;rm", "../r"):
            with self.subTest(repo):
                proc, calls = self.run_script("--repo", repo, "--config", self.small_config())
                self.assertEqual((proc.returncode, calls), (2, []))
                self.assertIn("OWNER/REPO", proc.stderr)

    def test_a_check_cannot_also_be_a_dry_run(self):
        proc, calls = self.run_script("--repo", "o/r", "--check", "--dry-run")
        self.assertEqual((proc.returncode, calls), (2, []))
        self.assertIn("not allowed with argument", proc.stderr)

    def test_a_missing_gh_is_reported(self):
        proc = subprocess.run([sys.executable, GITHUB_SETTINGS, "--repo", "o/r", "--config", self.small_config()], capture_output=True, text=True, env={**self.env, "PATH": ""})
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertIn("gh", proc.stderr)


class PinningGuardTest(ScriptCase):
    def apply(self, files, *args, branch="main", **replies):
        return self.run_script("--repo", "o/r", "--config", self.scratch_config(REQUIRES_PINNING), *args, replies={
            **workflow_replies(files, branch=branch),
            "GET repos/o/r/rulesets?per_page=100": [200, []],
            **replies,
        })

    def test_it_refuses_while_the_default_branch_has_an_unpinned_use_and_writes_nothing(self):
        proc, calls = self.apply({"x.yml": "name: x\njobs:\n  y:\n    steps:\n      - uses: actions/checkout@v4\n"})
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertEqual(proc.stderr, "github-settings.py: refusing to require pinned actions: main still has unpinned uses in .github/workflows/x.yml:5; merge the pins first\n")
        self.assertEqual(calls, [
            ("GET", "repos/o/r", None),
            ("GET", "repos/o/r/contents/.github/workflows?ref=main", None),
            ("GET", "repos/o/r/contents/.github/workflows/x.yml?ref=main", None),
        ])

    def test_it_names_every_file_and_line(self):
        files = {
            "a.yml": f"      - uses: actions/checkout@{PIN}\n      - uses: actions/setup-python@v5\n",
            "b.yaml": f"- uses: 'oven-sh/setup-bun@main' # v2\n    uses: actions/cache@{PIN[:39]}\n",
            "notes.md": "uses: actions/checkout@v4\n",
        }
        proc, calls = self.apply(files)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, (
            "github-settings.py: refusing to require pinned actions: main still has unpinned uses in "
            ".github/workflows/a.yml:2, .github/workflows/b.yaml:1, .github/workflows/b.yaml:2; merge the pins first\n"
        ))
        self.assertNotIn("repos/o/r/contents/.github/workflows/notes.md?ref=main", [c[1] for c in calls])

    def test_a_dry_run_runs_the_same_guard_and_prints_the_refusal(self):
        proc, calls = self.apply({"x.yml": "      - uses: actions/checkout@v4\n"}, "--dry-run")
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertIn("refusing to require pinned actions: main still has unpinned uses in .github/workflows/x.yml:1", proc.stderr)
        self.assertEqual({c[0] for c in calls}, {"GET"})

    def test_it_applies_once_every_use_is_a_commit_or_a_local_or_docker_reference(self):
        proc, calls = self.apply({"x.yml": PINNED_WORKFLOW})
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual([c[:2] for c in calls[:4]], [
            ("GET", "repos/o/r"),
            ("GET", "repos/o/r/contents/.github/workflows?ref=main"),
            ("GET", "repos/o/r/contents/.github/workflows/x.yml?ref=main"),
            ("PATCH", "repos/o/r"),
        ])

    def test_it_reads_the_default_branch_that_the_repository_names(self):
        proc, calls = self.apply({"x.yml": PINNED_WORKFLOW}, branch="trunk")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(calls[1][1], "repos/o/r/contents/.github/workflows?ref=trunk")

    def test_a_repository_with_no_workflows_has_nothing_to_refuse(self):
        proc, calls = self.apply({}, **{"GET repos/o/r/contents/.github/workflows?ref=main": [404, {"message": "Not Found"}]})
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(calls[2][:2], ("PATCH", "repos/o/r"))

    def test_a_workflow_it_cannot_read_stops_the_run_before_any_write(self):
        proc, calls = self.apply({"x.yml": PINNED_WORKFLOW}, **{"GET repos/o/r/contents/.github/workflows/x.yml?ref=main": [500, {"message": "Server Error"}]})
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertEqual(proc.stderr, "github-settings.py: GET repos/o/r/contents/.github/workflows/x.yml?ref=main failed with HTTP 500, Server Error\n")
        self.assertEqual({c[0] for c in calls}, {"GET"})

    def test_a_config_that_does_not_require_pinning_never_reads_the_workflows(self):
        _, calls = self.run_script("--repo", "o/r", "--config", self.small_config(), replies={"GET repos/o/r/rulesets?per_page=100": [200, []]})
        self.assertFalse([c for c in calls if "contents" in c[1]])
        self.assertNotIn(("GET", "repos/o/r", None), calls)


class CheckTest(ScriptCase):
    def check(self, replies, *args, **env):
        return self.run_script("--repo", "o/r", "--check", *args, replies=replies, **env)

    def test_it_is_clean_when_the_live_state_matches_and_reads_without_writing(self):
        config = github_config()
        proc, calls = self.check(live_state(config))
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "checked o/r: 0 drift, 0 unreadable\n", ""))
        self.assertEqual({c[0] for c in calls}, {"GET"})
        self.assertEqual(calls[0][1], "repos/o/r")
        self.assertEqual(len(calls), 1 + len(config["endpoints"]) + len(config["features"]) + 1 + len(config["rulesets"]))

    def test_a_changed_value_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/actions/permissions"] = [200, {"enabled": True, "allowed_actions": "all", "sha_pinning_required": False}]
        replies["GET repos/o/r"][1]["has_wiki"] = True
        proc, _ = self.check(replies)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, (
            "drift: settings has_wiki: want false, have true\n"
            'drift: actions/permissions allowed_actions: want "selected", have "all"\n'
            "drift: actions/permissions sha_pinning_required: want true, have false\n"
            "checked o/r: 3 drift, 0 unreadable\n"
        ))

    def test_lists_of_scalars_compare_as_sets(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/topics"] = [200, {"names": list(reversed(endpoint("topics")["body"]["names"]))}]
        replies["GET repos/o/r/code-scanning/default-setup"][1]["languages"] = ["python", "actions"]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 0 unreadable\n"))

    def test_a_missing_topic_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/topics"] = [200, {"names": ["pstack"]}]
        proc, _ = self.check(replies)
        names = json.dumps(endpoint("topics")["body"]["names"])
        self.assertEqual(proc.stdout, f'drift: topics names: want {names}, have ["pstack"]\nchecked o/r: 1 drift, 0 unreadable\n')

    def test_a_feature_that_is_off_when_it_should_be_on_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/private-vulnerability-reporting"] = [200, {"enabled": False}]
        replies["GET repos/o/r/automated-security-fixes"] = [200, {"enabled": True, "paused": False}]
        proc, _ = self.check(replies)
        self.assertEqual(proc.stdout, (
            "drift: automated-security-fixes enabled: want false, have true\n"
            "drift: private-vulnerability-reporting enabled: want true, have false\n"
            "checked o/r: 2 drift, 0 unreadable\n"
        ))

    def test_a_live_ruleset_that_the_file_does_not_name_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/rulesets?per_page=100"][1].append({"id": 12, "name": "legacy"})
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, "drift: ruleset legacy exists: want false, have true\nchecked o/r: 1 drift, 0 unreadable\n"))

    def test_a_ruleset_listing_or_ruleset_the_token_cannot_read_follows_the_endpoint_rule(self):
        for admin in (True, False):
            with self.subTest(admin=admin):
                replies = live_state(github_config(), admin=admin)
                replies["GET repos/o/r/rulesets/10"] = [404, {"message": "Not Found"}]
                proc, _ = self.check(replies)
                want = (1, "drift: ruleset main: HTTP 404, Not Found\nchecked o/r: 1 drift, 0 unreadable\n") if admin else (0, "checked o/r: 0 drift, 1 unreadable: ruleset main\n")
                self.assertEqual((proc.returncode, proc.stdout), want)
                replies = live_state(github_config(), admin=admin)
                replies["GET repos/o/r/rulesets?per_page=100"] = [403, {"message": "Forbidden"}]
                proc, _ = self.check(replies)
                want = (1, "drift: rulesets: HTTP 403, Forbidden\nchecked o/r: 1 drift, 0 unreadable\n") if admin else (0, "checked o/r: 0 drift, 1 unreadable: rulesets\n")
                self.assertEqual((proc.returncode, proc.stdout), want)

    def test_a_ruleset_that_is_missing_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/rulesets?per_page=100"] = [200, [{"id": 10, "name": "main"}]]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, "drift: ruleset release tags exists: want true, have false\nchecked o/r: 1 drift, 0 unreadable\n"))

    def test_a_changed_enforcement_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/rulesets/10"][1]["enforcement"] = "evaluate"
        proc, _ = self.check(replies)
        self.assertEqual(proc.stdout, 'drift: ruleset main enforcement: want "active", have "evaluate"\nchecked o/r: 1 drift, 0 unreadable\n')

    def test_a_rule_added_in_the_web_ui_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/rulesets/10"][1]["rules"].append({"type": "required_linear_history"})
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, (
            'drift: ruleset main rules: want ["deletion", "non_fast_forward", "pull_request", "required_status_checks"], '
            'have ["deletion", "non_fast_forward", "pull_request", "required_linear_history", "required_status_checks"]\n'
            "checked o/r: 1 drift, 0 unreadable\n"
        )))

    def test_a_rule_removed_in_the_web_ui_is_drift(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/rulesets/11"][1]["rules"] = [{"type": "deletion"}]
        proc, _ = self.check(replies)
        self.assertEqual(proc.stdout, 'drift: ruleset release tags rules: want ["deletion", "update"], have ["deletion"]\nchecked o/r: 1 drift, 0 unreadable\n')

    def test_a_bypass_actor_added_live_is_drift(self):
        replies = live_state(github_config())
        actor = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        replies["GET repos/o/r/rulesets/10"][1]["bypass_actors"] = [actor]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, f"drift: ruleset main bypass_actors: want [], have {json.dumps([actor])}\nchecked o/r: 1 drift, 0 unreadable\n"))

    def test_a_changed_rule_parameter_is_drift_but_an_added_default_is_not(self):
        replies = live_state(github_config())
        pull_request = next(r for r in replies["GET repos/o/r/rulesets/10"][1]["rules"] if r["type"] == "pull_request")
        pull_request["parameters"]["required_review_thread_resolution"] = False
        proc, _ = self.check(replies)
        self.assertEqual(proc.stdout, "drift: ruleset main pull_request required_review_thread_resolution: want true, have false\nchecked o/r: 1 drift, 0 unreadable\n")

    def test_a_missing_rule_parameter_is_drift(self):
        replies = live_state(github_config())
        pull_request = next(r for r in replies["GET repos/o/r/rulesets/10"][1]["rules"] if r["type"] == "pull_request")
        del pull_request["parameters"]["allowed_merge_methods"]
        proc, _ = self.check(replies)
        self.assertEqual(proc.stdout, 'drift: ruleset main pull_request allowed_merge_methods: want ["merge"], have missing\nchecked o/r: 1 drift, 0 unreadable\n')

    def test_a_key_the_response_leaves_out_is_unreadable_for_a_token_that_is_not_admin(self):
        replies = live_state(github_config(), admin=False)
        del replies["GET repos/o/r"][1]["allow_squash_merge"]
        del replies["GET repos/o/r/rulesets/10"][1]["bypass_actors"]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 2 unreadable: settings allow_squash_merge, ruleset main bypass_actors\n"))

    def test_a_key_the_response_leaves_out_is_drift_for_an_admin(self):
        replies = live_state(github_config())
        del replies["GET repos/o/r"][1]["allow_squash_merge"]
        del replies["GET repos/o/r/rulesets/10"][1]["bypass_actors"]
        del replies["GET repos/o/r/topics"][1]["names"]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, (
            "drift: settings allow_squash_merge: want false, have missing\n"
            f"drift: topics names: want {json.dumps(endpoint('topics')['body']['names'])}, have missing\n"
            "drift: ruleset main bypass_actors: want [], have missing\n"
            "checked o/r: 3 drift, 0 unreadable\n"
        )))

    def test_a_ruleset_response_without_its_rules_is_drift_for_an_admin_and_unreadable_otherwise(self):
        for admin in (True, False):
            with self.subTest(admin=admin):
                replies = live_state(github_config(), admin=admin)
                del replies["GET repos/o/r/rulesets/11"][1]["rules"]
                proc, _ = self.check(replies)
                if admin:
                    self.assertEqual((proc.returncode, proc.stdout), (1, 'drift: ruleset release tags rules: want ["deletion", "update"], have missing\nchecked o/r: 1 drift, 0 unreadable\n'))
                else:
                    self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 1 unreadable: ruleset release tags rules\n"))

    def test_a_misspelled_setting_or_path_is_drift_for_an_admin(self):
        replies = {"GET repos/o/r": [200, {"has_wiki": False, "permissions": {"admin": True}}], "GET repos/o/r/rulesets?per_page=100": [200, []]}
        proc, _ = self.check(replies, "--config", self.scratch_config(TYPOS))
        self.assertEqual((proc.returncode, proc.stdout), (1, (
            "drift: settings has_wikis: want false, have missing\n"
            "drift: actions/permissions/fork-pr-contributer-approval: HTTP 404, Not Found\n"
            "checked o/r: 2 drift, 0 unreadable\n"
        )))

    def test_a_misspelled_setting_or_path_is_unreadable_for_a_token_that_is_not_admin(self):
        replies = {"GET repos/o/r": [200, {"has_wiki": False, "permissions": {"admin": False}}], "GET repos/o/r/rulesets?per_page=100": [200, []]}
        proc, _ = self.check(replies, "--config", self.scratch_config(TYPOS))
        self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 2 unreadable: settings has_wikis, actions/permissions/fork-pr-contributer-approval\n"))

    def test_a_forbidden_or_missing_endpoint_is_drift_with_its_reason_for_an_admin(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/actions/permissions"] = [403, {"message": "Must have admin rights to Repository."}]
        replies["GET repos/o/r/code-scanning/default-setup"] = [404, {"message": "Not Found"}]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, (
            "drift: actions/permissions: HTTP 403, Must have admin rights to Repository.\n"
            "drift: code-scanning/default-setup: HTTP 404, Not Found\n"
            "checked o/r: 2 drift, 0 unreadable\n"
        )))

    def test_a_forbidden_or_missing_endpoint_is_unreadable_not_drift_for_a_token_that_is_not_admin(self):
        replies = live_state(github_config(), admin=False)
        replies["GET repos/o/r/actions/permissions"] = [403, {"message": "Resource not accessible by integration"}]
        replies["GET repos/o/r/code-scanning/default-setup"] = [404, {"message": "Not Found"}]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 2 unreadable: actions/permissions, code-scanning/default-setup\n"))

    def test_every_feature_that_answers_404_is_unreadable_for_a_token_that_is_not_admin(self):
        for feature in github_config()["features"]:
            with self.subTest(feature):
                replies = live_state(github_config(), admin=False)
                replies[f"GET repos/o/r/{feature}"] = [404, {"message": "Not Found"}]
                proc, _ = self.check(replies)
                self.assertEqual((proc.returncode, proc.stdout), (0, f"checked o/r: 0 drift, 1 unreadable: {feature}\n"))

    def test_every_feature_that_answers_404_is_off_for_an_admin(self):
        for feature, want in github_config()["features"].items():
            with self.subTest(feature):
                replies = live_state(github_config())
                replies[f"GET repos/o/r/{feature}"] = [404, {"message": "Not Found"}]
                proc, _ = self.check(replies)
                if want:
                    self.assertEqual((proc.returncode, proc.stdout), (1, f"drift: {feature} enabled: want true, have false\nchecked o/r: 1 drift, 0 unreadable\n"))
                else:
                    self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 0 unreadable\n"))

    def test_a_feature_wanted_false_that_answers_404_is_clean_for_an_admin(self):
        config = {**SMALL, "features": {name: False for name in github_config()["features"]}}
        replies = live_state(config)
        for name in config["features"]:
            replies[f"GET repos/o/r/{name}"] = [404, {"message": "Not Found"}]
        proc, _ = self.check(replies, "--config", self.scratch_config(config))
        self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 0 unreadable\n"))

    def test_a_forbidden_feature_is_unreadable_for_a_token_that_is_not_admin_and_drift_for_an_admin(self):
        for feature in github_config()["features"]:
            with self.subTest(feature, admin=False):
                replies = live_state(github_config(), admin=False)
                replies[f"GET repos/o/r/{feature}"] = [403, {"message": "nope"}]
                proc, _ = self.check(replies)
                self.assertEqual((proc.returncode, proc.stdout), (0, f"checked o/r: 0 drift, 1 unreadable: {feature}\n"))
            with self.subTest(feature, admin=True):
                replies = live_state(github_config())
                replies[f"GET repos/o/r/{feature}"] = [403, {"message": "nope"}]
                proc, _ = self.check(replies)
                self.assertEqual((proc.returncode, proc.stdout), (1, f"drift: {feature}: HTTP 403, nope\nchecked o/r: 1 drift, 0 unreadable\n"))

    def test_a_204_means_on_for_every_feature(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/private-vulnerability-reporting"] = [204, None]
        replies["GET repos/o/r/automated-security-fixes"] = [204, None]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, "drift: automated-security-fixes enabled: want false, have true\nchecked o/r: 1 drift, 0 unreadable\n"))

    def test_a_feature_response_without_enabled_follows_the_missing_key_rule(self):
        for admin in (True, False):
            with self.subTest(admin=admin):
                replies = live_state(github_config(), admin=admin)
                replies["GET repos/o/r/immutable-releases"] = [200, {"enforced_by_owner": False}]
                proc, _ = self.check(replies)
                if admin:
                    self.assertEqual((proc.returncode, proc.stdout), (1, "drift: immutable-releases enabled: want true, have missing\nchecked o/r: 1 drift, 0 unreadable\n"))
                else:
                    self.assertEqual((proc.returncode, proc.stdout), (0, "checked o/r: 0 drift, 1 unreadable: immutable-releases enabled\n"))

    def test_any_other_failure_is_drift_with_its_reason(self):
        replies = live_state(github_config())
        replies["GET repos/o/r/actions/permissions/selected-actions"] = [409, {"message": "Conflict", "errors": "All actions and workflows are allowed on this repository"}]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (1, (
            "drift: actions/permissions/selected-actions: HTTP 409, All actions and workflows are allowed on this repository\n"
            "checked o/r: 1 drift, 0 unreadable\n"
        )))

    def test_a_transient_failure_stops_the_check_with_no_verdict(self):
        for status, message in ((429, "Too Many Requests"), (500, "Server Error"), (502, "Bad Gateway"), (503, "Unavailable")):
            for path in ("topics", "private-vulnerability-reporting", "rulesets?per_page=100", "rulesets/10"):
                with self.subTest(status=status, path=path):
                    replies = live_state(github_config(), admin=status % 2 == 0)
                    replies["GET repos/o/r"][1]["has_wiki"] = True
                    replies[f"GET repos/o/r/{path}"] = [status, {"message": message}]
                    proc, _ = self.check(replies, GITHUB_ACTIONS="true")
                    self.assertEqual((proc.returncode, proc.stdout), (2, ""))
                    self.assertEqual(proc.stderr, f"github-settings.py: GET repos/o/r/{path} failed with HTTP {status}, {message}\n")

    def test_a_repository_it_cannot_read_stops_the_check(self):
        replies = live_state(github_config())
        replies["GET repos/o/r"] = [404, {"message": "Not Found"}]
        proc, _ = self.check(replies)
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertEqual(proc.stderr, "github-settings.py: GET repos/o/r failed with HTTP 404, Not Found\n")

    def test_the_run_annotates_each_drift_and_the_unreadable_items_on_github_actions(self):
        replies = live_state(github_config(), admin=False)
        replies["GET repos/o/r"][1]["has_wiki"] = True
        replies["GET repos/o/r/actions/permissions"] = [403, {"message": "Resource not accessible by integration"}]
        proc, _ = self.check(replies, GITHUB_ACTIONS="true")
        self.assertEqual((proc.returncode, proc.stdout), (1, (
            "drift: settings has_wiki: want false, have true\n"
            "checked o/r: 1 drift, 1 unreadable: actions/permissions\n"
            "::error::drift: settings has_wiki: want false, have true\n"
            "::notice::unreadable with this token, so not compared: actions/permissions\n"
        )))

    def test_an_annotation_escapes_a_percent_sign_in_a_live_value(self):
        replies = live_state(SMALL)
        replies["GET repos/o/r"][1]["has_wiki"] = "50%0A done"
        proc, _ = self.check(replies, "--config", self.small_config(), GITHUB_ACTIONS="true")
        self.assertEqual(proc.stdout.splitlines()[:3], [
            'drift: settings has_wiki: want false, have "50%0A done"',
            "checked o/r: 1 drift, 0 unreadable",
            '::error::drift: settings has_wiki: want false, have "50%250A done"',
        ])

    def test_the_report_also_goes_to_the_step_summary(self):
        summary = os.path.join(self.tmp, "summary.md")
        replies = live_state(github_config(), admin=False)
        replies["GET repos/o/r"][1]["has_wiki"] = True
        replies["GET repos/o/r/actions/permissions"] = [403, {"message": "Resource not accessible by integration"}]
        proc, _ = self.check(replies, GITHUB_STEP_SUMMARY=summary)
        self.assertEqual(proc.stdout, "drift: settings has_wiki: want false, have true\nchecked o/r: 1 drift, 1 unreadable: actions/permissions\n")
        with open(summary) as f:
            self.assertEqual(f.read(), proc.stdout)

    def test_a_clean_report_goes_to_the_step_summary_too(self):
        summary = os.path.join(self.tmp, "summary.md")
        proc, _ = self.check(live_state(github_config()), GITHUB_STEP_SUMMARY=summary)
        with open(summary) as f:
            self.assertEqual(f.read(), "checked o/r: 0 drift, 0 unreadable\n")

    def test_no_annotation_is_printed_elsewhere(self):
        replies = live_state(github_config())
        replies["GET repos/o/r"][1]["has_wiki"] = True
        proc, _ = self.check(replies)
        self.assertNotIn("::", proc.stdout)


if __name__ == "__main__":
    unittest.main()
