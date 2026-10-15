#!/usr/bin/env python3
"""Smoke-test the pstack port in Claude Code and Codex.

Each scenario runs one skill headless in both harnesses against a fresh
throwaway repo, then checks the session transcripts (the parent's, its
subagents', and any foreign seat's) for the plumbing the port adds: reading
pstack-harness, resolving models from ~/.agents/pstack-models.md, spawning the
right subagent type and model, and running the other harness's CLI for a
foreign seat. Runs that pass are deleted, sessions included. Runs that fail
are kept for inspection.

  smoke.py [--only how,interrogate] [--harness claude|codex] [--jobs 4] [--keep] [--out DIR]
"""

import argparse
import concurrent.futures
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

PSTACK = os.path.expanduser("~/.agents/pstack")
TRANSCRIPTS = os.path.join(PSTACK, "port", "transcripts.py")
CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")

CACHE_MAIN = '''\
# User cache module
# TODO: maybe add a TTL later
_cache = {}


def get_user(db, user_id):
    # Check the cache first
    if user_id in _cache:
        # Cache hit, return it
        return _cache[user_id]
    # Cache miss, fetch from the db
    user = db.fetch_user(user_id)
    # Store it in the cache
    _cache[user_id] = user
    return user
'''

CACHE_FEAT = CACHE_MAIN + '''

def update_user(db, user_id, fields):
    # Write the new fields to the db
    db.update_user(user_id, fields)
'''

TEST_CACHE = '''\
import unittest

import cache


class FakeDB:
    def __init__(self):
        self.users = {1: {"name": "ada"}}
        self.fetches = 0

    def fetch_user(self, user_id):
        self.fetches += 1
        return dict(self.users[user_id])

    def update_user(self, user_id, fields):
        self.users[user_id].update(fields)


class GetUserTest(unittest.TestCase):
    def setUp(self):
        cache._cache.clear()

    def test_second_read_hits_the_cache(self):
        db = FakeDB()
        cache.get_user(db, 1)
        cache.get_user(db, 1)
        self.assertEqual(db.fetches, 1)


if __name__ == "__main__":
    unittest.main()
'''

CLI = '''\
"""Look up and rename users stored in users.json."""
import json
import sys

from cache import get_user, update_user


class JsonDB:
    def __init__(self, path="users.json"):
        self.path = path

    def _load(self):
        with open(self.path) as f:
            return json.load(f)

    def fetch_user(self, user_id):
        return self._load()[str(user_id)]

    def update_user(self, user_id, fields):
        users = self._load()
        users[str(user_id)].update(fields)
        with open(self.path, "w") as f:
            json.dump(users, f, indent=2)


def main(argv):
    db = JsonDB()
    if argv[0] == "get":
        print(json.dumps(get_user(db, argv[1])))
    elif argv[0] == "rename":
        update_user(db, argv[1], {"name": argv[2]})
        print(json.dumps(get_user(db, argv[1])))


if __name__ == "__main__":
    main(sys.argv[1:])
'''

USERS = '{\n  "1": {"name": "ada"},\n  "2": {"name": "grace"}\n}\n'

# The bug the scenarios revolve around: update_user leaves a stale cache entry.
BEHAVIOR_CHECK = '''\
import cache
class DB:
    users = {1: {"name": "ada"}}
    def fetch_user(self, i): return dict(self.users[i])
    def update_user(self, i, f): self.users[i].update(f)
db = DB()
cache._cache.clear()
cache.get_user(db, 1)
cache.update_user(db, 1, {"name": "lovelace"})
assert cache.get_user(db, 1)["name"] == "lovelace", "stale cache after update_user"
'''


os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def sh(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def make_fixture(repo):
    os.makedirs(repo)
    files = {"cache.py": CACHE_MAIN, "test_cache.py": TEST_CACHE, "README.md": "# users\n\nA user cache and a CLI over users.json.\n"}
    for name, body in files.items():
        with open(os.path.join(repo, name), "w") as f:
            f.write(body)
    sh("git init -q -b main && git add -A && git -c user.name=smoke -c user.email=smoke@example.com commit -qm 'Add user cache'", repo)
    for name, body in {"cache.py": CACHE_FEAT, "cli.py": CLI, "users.json": USERS}.items():
        with open(os.path.join(repo, name), "w") as f:
            f.write(body)
    sh("git switch -qc feat && git add -A && git -c user.name=smoke -c user.email=smoke@example.com commit -qm 'Add update_user and a CLI'", repo)


# --- transcript access ------------------------------------------------------


def tool_calls(path):
    """(name, full input text) for every tool call in a Claude or Codex transcript."""
    calls = []
    with open(path, errors="replace") as f:
        for line in f:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") == "assistant":
                for part in (record.get("message") or {}).get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "tool_use":
                        calls.append((part.get("name"), json.dumps(part.get("input"), ensure_ascii=False)))
            elif record.get("type") == "response_item":
                item = record.get("payload") or {}
                if item.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
                    args = item.get("arguments") or item.get("input") or json.dumps(item.get("action"))
                    calls.append((item.get("name", "shell"), args if isinstance(args, str) else json.dumps(args)))
    return calls


def sessions_under(run_dir):
    out = sh([TRANSCRIPTS, "list", "--cwd", run_dir, "--days", "1", "--subagents"], run_dir).stdout
    return [dict(zip(("modified", "harness", "id", "path", "title"), row.split("\t"))) for row in out.splitlines() if row]


def claude_subagents(session_path):
    return glob.glob(os.path.join(session_path[:-6], "subagents", "*.jsonl"))


class Evidence:
    def __init__(self, harness, run_dir, answer):
        self.harness, self.run_dir, self.answer = harness, run_dir, answer or ""
        self.sessions = sessions_under(run_dir)
        paths = [s["path"] for s in self.sessions]
        for s in list(self.sessions):
            if s["harness"] == "claude":
                paths += claude_subagents(s["path"])
        self.calls = [c for p in dict.fromkeys(paths) for c in tool_calls(p)]
        self.harnesses = {s["harness"] for s in self.sessions}

    def called(self, pattern, name=None):
        rx = re.compile(pattern, re.S)
        return any((name is None or re.fullmatch(name, n or "")) and rx.search(text) for n, text in self.calls)


# --- checks -----------------------------------------------------------------
# Each check takes Evidence and returns (ok, detail).

FOREIGN = {"claude": "codex", "codex": "claude"}
MODELS = {  # role family -> model the native harness should spawn, per ~/.agents/pstack-models.md
    ("claude", "judgment"): "opus",
    ("claude", "code"): "sonnet",
    ("codex", "judgment"): "gpt-6-astra",
    ("codex", "code"): "gpt-6.1-sol",
}


def read_harness(ev):
    return ev.called(r"pstack-harness/SKILL\.md"), "read pstack-harness"


def read_models(ev):
    return ev.called(r"pstack-models\.md(?!c)"), "read ~/.agents/pstack-models.md"


def no_cursor_slugs(ev):
    spawns = r"Agent|Bash|(collaboration\.)?spawn_agent|exec|shell"
    used = ev.called(r"grok-4|gpt-5\.6-sol-max|claude-opus-5-5-(max|xhigh)|xhigh-fast", name=spawns)
    return not used, "used no Cursor model slug"


EFFORT = r"(?:low|medium|high|xhigh|max)"  # skills move a subagent's effort off its entry to fit the job


def spawned(family, types=rf"pstack-effort-{EFFORT}"):
    def check(ev):
        model = MODELS[(ev.harness, family)]
        if ev.harness == "claude":
            ok = ev.called(rf'"subagent_type":\s*"(?:{types})".*"model":\s*"{model}"|"model":\s*"{model}".*"subagent_type":\s*"(?:{types})"', name="Agent")
        else:
            ok = ev.called(rf'"model":\s*"{re.escape(model)}".*"reasoning_effort":\s*"{EFFORT}"|"reasoning_effort":\s*"{EFFORT}".*"model":\s*"{re.escape(model)}"', name=r"(collaboration\.)?spawn_agent")
        return ok, f"spawned a {family} subagent on {model}"
    return check


def spawned_type(subagent_type, codex_marker):
    def check(ev):
        if ev.harness == "claude":
            return ev.called(rf'"subagent_type":\s*"{subagent_type}"', name="Agent"), f"spawned subagent_type {subagent_type}"
        # Codex encrypts spawn_agent messages in its logs, so check that the parent read the agent file and then spawned.
        return ev.called(codex_marker) and ev.called(r".", name=r"(collaboration\.)?spawn_agent"), f"read {subagent_type}'s instructions and spawned an agent with them"
    return check


def foreign_seats_off():
    try:
        with open(os.path.expanduser("~/.agents/pstack-models.md")) as f:
            return bool(re.search(r"^foreign seats:\s*off\s*$", f.read(), re.M))
    except OSError:
        return False


def no_foreign_seat(ev):
    cli = r"codex\s+exec\b" if ev.harness == "claude" else r"claude\s+-p\b"
    crossed = ev.called(cli) or FOREIGN[ev.harness] in ev.harnesses
    return not crossed, f"kept every seat in {ev.harness} (foreign seats off)"


PANEL_MODELS = {"claude": ("opus", "sonnet"), "codex": ("gpt-6-astra", "gpt-6.1-sol")}


def panel_seats(ev):
    """With foreign seats off, a panel seats both native models from the models file."""
    if not foreign_seats_off():
        return True, "panel seating checked by the foreign-seat check"
    missing = []
    for model in PANEL_MODELS[ev.harness]:
        if ev.harness == "claude":
            ok = ev.called(rf'"model":\s*"{model}"', name="Agent")
        else:
            ok = ev.called(rf'"model":\s*"{re.escape(model)}"', name=r"(collaboration\.)?spawn_agent")
        if not ok:
            missing.append(model)
    return not missing, "seated both native panel models" + (f" (missing {', '.join(missing)})" if missing else "")


def foreign_seat(writes=False):
    def check(ev):
        if foreign_seats_off():
            return no_foreign_seat(ev)
        if ev.harness == "claude":
            needles = [r"codex\s+exec\b", r"gpt-6-astra", r"xhigh", r"workspace-write" if writes else r"read-only"]
        else:
            needles = [r"claude\s+-p\b", r"opus", r"xhigh", r"--permission-mode\s+auto" if writes else r"--disallowedTools"]
        seat_calls = [text for _, text in ev.calls if all(re.search(n, text) for n in needles)]
        cli = bool(seat_calls)
        # A seat run with --no-session-persistence or --ephemeral leaves no transcript, so its output file has to prove it ran.
        seat_ran = FOREIGN[ev.harness] in ev.harnesses or any(seat_output_ok(ev, t) for t in seat_calls)
        what = f"ran a {'writing' if writes else 'read-only'} {FOREIGN[ev.harness]} seat through its CLI"
        # Codex's approval reviewer may deny the escalation. Asking correctly and reporting the block is the right behavior.
        escalated = any("require_escalated" in t for t in seat_calls)
        if cli and not seat_ran and escalated and re.search(r"block|denied|reject", ev.answer, re.I):
            return True, what + " (escalation denied by the approval policy and reported)"
        return cli and seat_ran, what
    return check


def seat_output_ok(ev, call):
    if not re.search(r"--no-session-persistence|--ephemeral", call):
        return False
    for path in re.findall(r"(?:-o\s+|(?<![0-9&])>\s*)([^\s'\"\\;&|]+)", call):
        for base in ("", os.path.join(ev.run_dir, "repo"), ev.run_dir):
            full = os.path.join(base, path)
            if os.path.isfile(full) and os.path.getsize(full) > 200:
                with open(full, errors="replace") as f:
                    if "Not logged in" not in f.read():
                        return True
    return False


def used_transcripts(command):
    return lambda ev: (ev.called(rf"transcripts\.py.*\b{command}\b"), f"ran transcripts.py {command}")


def answer_matches(pattern, what):
    return lambda ev: (bool(re.search(pattern, ev.answer, re.I | re.S)), f"answer {what}")


def repo_check(cmd, what):
    return lambda ev: (sh(cmd, os.path.join(ev.run_dir, "repo")).returncode == 0, what)


def behaves(snippet, what):
    return lambda ev: (sh(["python3", "-c", snippet], os.path.join(ev.run_dir, "repo")).returncode == 0, what)


def comment_lines_dropped(ev):
    with open(os.path.join(ev.run_dir, "repo", "cache.py")) as f:
        remaining = sum(1 for line in f if line.strip().startswith("#"))
    return remaining <= 2, f"cache.py narration comments removed ({remaining} of 7 left)"


def verification_skill(ev):
    repo = os.path.join(ev.run_dir, "repo")
    skills = glob.glob(os.path.join(repo, ".agents", "skills", "verify-*", "SKILL.md"))
    links = glob.glob(os.path.join(repo, ".claude", "skills", "verify-*"))
    linked = any(os.path.islink(l) and os.path.realpath(l) == os.path.realpath(os.path.dirname(s)) for l in links for s in skills)
    features = any(os.path.exists(os.path.join(os.path.dirname(s), "features", "README.md")) for s in skills)
    return bool(skills) and linked and features, ".agents/skills/verify-*/ with features/README.md, linked from .claude/skills/"


# --- scenarios ----------------------------------------------------------------

SMOKE_NOTE = "This is a headless smoke test with no human available. The session ends when your turn ends, so wait for every subagent and background command to finish before your final reply. Put the task's deliverables in the repo, the current directory. Keep scratch files, worktrees, and logs inside {tmp}. Never open a PR or push; there is no remote."

SCENARIOS = {
    "how": dict(
        skill="how",
        task="How does the user cache in cache.py work, and how does cli.py use it?",
        checks=[read_harness, read_models, no_cursor_slugs, spawned("judgment"), answer_matches(r"cache", "explains the cache")],
    ),
    "why": dict(
        skill="why",
        task="Why does get_user cache results in a module-level dict? For this smoke test, run only the source control investigator and record every other evidence category as skipped for the smoke test. Do not query any MCP.",
        checks=[read_harness, read_models, spawned("code"), spawned("judgment"), answer_matches(r"source", "cites sources")],
    ),
    "interrogate": dict(
        skill="interrogate",
        task="Interrogate the feat branch diff against main. Intent: add update_user next to the cached get_user, and a CLI over it.",
        checks=[read_harness, read_models, spawned("judgment"), foreign_seat(), panel_seats, answer_matches(r"stale|invalidat|old (?:record|value|data)|clear\w* (?:the |its )?cache", "finds the stale-cache bug")],
    ),
    "arena": dict(
        skill="arena",
        task="Arena this: change update_user so get_user never returns stale data after an update. Each candidate works in its own git worktree under the scratch directory. Leave the main checkout untouched and report the base you picked and the grafts.",
        checks=[read_harness, read_models, spawned("judgment"), foreign_seat(writes=True), panel_seats, answer_matches(r"base|(?:selected|picked|chose).{0,20}candidate", "reports a base pick")],
    ),
    "swarm": dict(
        skill="swarm",
        task="Swarm this with two workers in the coverage shape: one checks cache.py for correctness bugs, the other checks cli.py for input-handling bugs. Workers are read-only. Report PASS, ISSUES, or BLOCKED per slice.",
        checks=[read_harness, read_models, spawned("code"), answer_matches(r"ISSUES|PASS|BLOCKED", "reports per-slice verdicts")],
    ),
    "no-comments": dict(
        skill="no-comments",
        task="Clean the comments out of cache.py on this branch and apply the changes.",
        checks=[read_harness, spawned_type("comment-sicko", r"agents/comment-sicko\.md"), comment_lines_dropped],
    ),
    "poteto-mode": dict(
        skill="poteto-mode",
        task="Bug: after update_user(db, id, fields), get_user(db, id) still returns the old user. Reproduce it first, then fix and verify. Commit locally on this branch.",
        checks=[
            read_harness,
            lambda ev: (ev.called(r"playbooks/bug-fix\.md"), "opened the bug-fix playbook"),
            spawned("code", types=rf"pstack-effort-{EFFORT}|poteto-agent"),
            behaves(BEHAVIOR_CHECK, "update_user no longer leaves a stale entry"),
            repo_check("python3 -m unittest -q", "tests pass"),
        ],
    ),
    "show-me-your-work": dict(
        skill="show-me-your-work",
        task="Add a docstring to update_user in cache.py, logging your decisions as you go, then run the end-of-run check on the log.",
        checks=[
            read_harness,
            lambda ev: (any(f.endswith(".tsv") for _, _, fs in os.walk(ev.run_dir) for f in fs), "wrote a decision log"),
            used_transcripts("current"),
        ],
    ),
    "reflect": dict(
        skill="reflect",
        warmup="Make `python3 cli.py get` with no user id print a usage line instead of crashing. Keep the change small.",
        task="Reflect on this session. It is headless: present the synthesizer's Accepted, Rejected, and Backlog output and stop without applying any edit.",
        checks=[
            read_harness,
            read_models,
            used_transcripts("current"),
            used_transcripts("dump"),
            spawned("judgment"),
            foreign_seat(),
            answer_matches(r"Accepted.*Rejected|Rejected.*Accepted", "presents the synthesizer lists"),
        ],
    ),
    "create-verification-skill": dict(
        skill="create-verification-skill",
        task="Create a verification skill for this repo's CLI (cli.py over users.json).",
        # Claude Code asks before any write under .claude/, and a headless run cannot answer.
        claude_mode="bypassPermissions",
        checks=[read_harness, verification_skill],
    ),
}


def prompt_for(harness, skill, task, tmp):
    note = SMOKE_NOTE.format(tmp=tmp)
    if harness == "claude":
        return f"/{skill} {task}\n\n{note}"
    return f"Use the {skill} skill: read ~/.agents/pstack/skills/{skill}/SKILL.md and follow it.\n\nTask: {task}\n\n{note}"


def run_claude(prompt, repo, log, resume=None, mode="acceptEdits"):
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", mode,
           "--allowedTools", "Bash", "--add-dir", "/tmp", "--add-dir", os.path.dirname(repo)]
    if resume:
        cmd += ["--resume", resume]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=45 * 60)
    log.write(proc.stdout + proc.stderr)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None, ""
    # With `verbose` on in the user's settings, json output is every message, ending with the result.
    if isinstance(out, list):
        out = next((m for m in reversed(out) if m.get("type") == "result"), {})
    return out.get("session_id"), out.get("result", "")


def run_codex(prompt, repo, log, resume=None, mode=None):
    last = os.path.join(os.path.dirname(repo), "codex-last.md")
    cmd = ["codex", "exec", "--json", "-s", "workspace-write", "--skip-git-repo-check", "-C", repo, "-o", last]
    cmd += ["resume", resume, prompt] if resume else [prompt]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=45 * 60)
    log.write(proc.stdout + proc.stderr)
    thread = None
    for line in proc.stdout.splitlines():
        if '"thread.started"' in line:
            thread = json.loads(line).get("thread_id")
            break
    answer = open(last).read() if os.path.exists(last) else ""
    return thread or resume, answer


def run_one(name, harness, out_dir):
    spec = SCENARIOS[name]
    run_dir = os.path.join(out_dir, f"{name}-{harness}")
    repo, tmp = os.path.join(run_dir, "repo"), os.path.join(run_dir, "tmp")
    make_fixture(repo)
    os.makedirs(tmp)
    run = run_claude if harness == "claude" else run_codex
    started = time.time()
    with open(os.path.join(run_dir, "run.log"), "w") as log:
        session = None
        if spec.get("warmup"):
            session, _ = run(spec["warmup"] + "\n\n" + SMOKE_NOTE.format(tmp=tmp), repo, log)
        try:
            extra = {"mode": spec["claude_mode"]} if harness == "claude" and spec.get("claude_mode") else {}
            session, answer = run(prompt_for(harness, spec["skill"], spec["task"], tmp), repo, log, resume=session, **extra)
        except subprocess.TimeoutExpired:
            answer = ""
    with open(os.path.join(run_dir, "answer.md"), "w") as f:
        f.write(answer)
    ev = Evidence(harness, run_dir, answer)
    results = []
    for check in spec["checks"]:
        try:
            results.append(check(ev))
        except Exception as e:  # a crashing check is a failed check, not a crashed suite
            results.append((False, f"check crashed: {e}"))
    return dict(name=name, harness=harness, run_dir=run_dir, minutes=(time.time() - started) / 60, results=results, sessions=ev.sessions)


def delete_sessions(run_dir):
    for s in sessions_under(run_dir):
        if s["harness"] == "codex":
            sh(["codex", "delete", "--force", s["id"]], run_dir)
        elif os.path.dirname(s["path"]).startswith(CLAUDE_PROJECTS):
            shutil.rmtree(s["path"][:-6], ignore_errors=True)
            if os.path.exists(s["path"]):
                os.remove(s["path"])
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(run_dir))
    for d in glob.glob(os.path.join(CLAUDE_PROJECTS, "*")):
        if os.path.basename(d).startswith(slug):
            shutil.rmtree(d, ignore_errors=True)


def recheck(out_dir):
    for run_dir in sorted(glob.glob(os.path.join(out_dir, "*-*"))):
        name, harness = os.path.basename(run_dir).rsplit("-", 1)
        answer_path = os.path.join(run_dir, "answer.md")
        answer = open(answer_path).read() if os.path.exists(answer_path) else ""
        ev = Evidence(harness, run_dir, answer)
        results = [check(ev) for check in SCENARIOS[name]["checks"]]
        failed = [detail for ok, detail in results if not ok]
        notes = [d for ok, d in results if ok and "denied by the approval policy" in d]
        print(f"{'FAIL' if failed else 'PASS'}  {name:<26} {harness:<6} " + ("; ".join(f"missing: {d}" for d in failed) if failed else "ok") + "".join(f"; note: {n}" for n in notes))


def pstack_state():
    return sh("git status --porcelain && git rev-parse HEAD", PSTACK).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated scenario names")
    parser.add_argument("--harness", choices=["claude", "codex"])
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--keep", action="store_true", help="keep passing runs and their sessions too")
    parser.add_argument("--out", default=os.path.join("/tmp", f"pstack-smoke-{time.strftime('%Y%m%d-%H%M%S')}"))
    parser.add_argument("--recheck", action="store_true", help="re-run the checks on the kept runs in --out without running anything")
    args = parser.parse_args()
    if args.recheck:
        return recheck(os.path.realpath(args.out))

    names = args.only.split(",") if args.only else list(SCENARIOS)
    unknown = set(names) - set(SCENARIOS)
    if unknown:
        sys.exit(f"unknown scenarios: {', '.join(sorted(unknown))}")
    harnesses = [args.harness] if args.harness else ["claude", "codex"]
    out_dir = os.path.realpath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    before = pstack_state()
    print(f"runs in {out_dir}", flush=True)

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_one, n, h, out_dir): (n, h) for n in names for h in harnesses}
        for future in concurrent.futures.as_completed(futures):
            n, h = futures[future]
            try:
                row = future.result()
            except Exception as e:
                row = dict(name=n, harness=h, run_dir=os.path.join(out_dir, f"{n}-{h}"), minutes=0, results=[(False, f"run crashed: {e}")], sessions=[])
            passed = all(ok for ok, _ in row["results"])
            print(f"{'PASS' if passed else 'FAIL'}  {n:<26} {h:<6} {row['minutes']:5.1f} min", flush=True)
            if passed and not args.keep:
                delete_sessions(row["run_dir"])
                shutil.rmtree(row["run_dir"], ignore_errors=True)
            rows.append(row)

    print()
    for row in sorted(rows, key=lambda r: (r["name"], r["harness"])):
        failed = [detail for ok, detail in row["results"] if not ok]
        status = "PASS" if not failed else "FAIL"
        notes = [d for ok, d in row["results"] if ok and "denied by the approval policy" in d]
        detail = "; ".join(f"missing: {d}" for d in failed) if failed else f"{len(row['results'])} checks"
        print(f"{status}  {row['name']:<26} {row['harness']:<6} " + detail + "".join(f"; note: {n}" for n in notes))
    if pstack_state() != before:
        print("\nWARNING: the pstack clone changed during the run. A skill edited pstack itself.")
    failures = sum(1 for r in rows if not all(ok for ok, _ in r["results"]))
    print(f"\n{len(rows) - failures}/{len(rows)} passed. Failed runs and their sessions are kept in {out_dir}.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
