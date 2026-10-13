#!/usr/bin/env python3
"""Merge upstream cursor/plugins into the Claude Code and Codex port and triage the result.

  merge-upstream.py [--ref REF] [--repo DIR] [--no-fetch] [--report FILE]

1. Fetches the cursor/plugins remote, whatever it is named, and merges its
   main (or REF), or picks up a merge already in progress.
2. Drops everything outside the port (the other plugins, and pstack's
   Cursor-only pieces), which upstream changes would otherwise bring back,
   and puts back our own copy of the two READMEs, the Dependabot config, and
   the security policy, whatever upstream changed in them.
3. Resolves each conflict where our only change to the file is the
   pstack-harness pointer line: it takes upstream's version and puts the
   pointer back under the title. Every other conflict is listed for a person.
4. Reports what needs porting: new upstream skills (with their Cursor
   terms), ported skills that disappeared, and Cursor terms that upstream
   newly added to skills already ported.

--report writes the outcome as JSON for CI: status (up_to_date, merged,
or conflicts), the incoming commit, and what was dropped, resolved, left
for a person, and needs porting.

It never commits. Finish the merge yourself, then run port/install.sh and
port/smoke/smoke.py.
"""

import argparse
import json
import os
import re
import subprocess
import sys

POINTER = re.compile(r"^Outside Cursor, first read `~/\.agents/pstack/skills/pstack-harness/SKILL\.md`.*$", re.M)
# What the port keeps. Anything else upstream adds or changes is dropped on merge.
KEEP_FILES = {"README.md", ".gitignore", "cursor-team-kit/LICENSE", ".github/dependabot.yml", ".github/SECURITY.md"}
KEEP_DIRS = ("pstack/", "cursor-team-kit/skills/deslop/", "cursor-team-kit/skills/control-ui/", "cursor-team-kit/skills/control-cli/")
KEEP_WORKFLOWS = re.compile(r"\.github/workflows/pstack-[^/]+\.yml")
DROP = re.compile(r"^pstack/((\.cursor-plugin|assets|automations|docs|skills/make-bot-ui|skills/poteto-help)/|plugin\.json$)")
OURS = {"README.md", "pstack/README.md", ".github/dependabot.yml", ".github/SECURITY.md"}

CURSOR_TERMS = re.compile(
    r"\bTask\b|AskQuestion|\.cursor/|pstack-models\.mdc|grok-|gpt-5\.6|claude-opus-5-5-max|agent-transcripts"
    r"|cursor-team-kit|create-skill|environment: \"cloud\"|cloud_base_branch|Cursor's built-in"
)


def git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{proc.stderr}")
    return proc.stdout


def upstream_remote(repo):
    for line in git(repo, "remote", "-v").splitlines():
        name, url = line.split()[:2]
        if re.search(r"github\.com[:/]cursor/plugins(\.git)?$", url):
            return name
    sys.exit("no remote points at cursor/plugins. Add one once:\n  git remote add upstream https://github.com/cursor/plugins.git")


def show(repo, spec):
    proc = subprocess.run(["git", "-C", repo, "show", spec], capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def kept(path):
    return (path in KEEP_FILES or path.startswith(KEEP_DIRS) or bool(KEEP_WORKFLOWS.fullmatch(path))) and not DROP.match(path)


def prune_and_keep_ours(repo):
    """Drop paths outside the port and restore our copy of every OURS path. Returns how many paths were dropped."""
    dropped = 0
    for path in sorted(set(git(repo, "ls-files").splitlines())):
        if path in OURS:
            git(repo, "checkout", "HEAD", "--", path, check=False)
            git(repo, "add", "--", path)
        elif not kept(path):
            git(repo, "rm", "-q", "-f", "--sparse", "--", path)
            dropped += 1
    return dropped


def strip_pointer(text):
    """The text with our pointer paragraph removed, plus that paragraph."""
    match = POINTER.search(text)
    if not match:
        return text, None
    start, end = match.start(), match.end()
    if text[end:end + 1] == "\n":
        end += 1
    if text[start - 2:start] == "\n\n":
        start -= 1
    return text[:start] + text[end:], match.group(0)


def insert_pointer(text, pointer):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[: i + 1] + ["", pointer] + lines[i + 1 :])
    return None


def resolve_pointer_conflicts(repo):
    conflicted = git(repo, "diff", "--name-only", "--diff-filter=U").split()
    resolved, manual = [], []
    for path in conflicted:
        base, ours, theirs = (show(repo, f":{n}:{path}") for n in (1, 2, 3))
        if None not in (base, ours, theirs):
            without, pointer = strip_pointer(ours)
            if pointer and without == base:
                merged = insert_pointer(theirs, pointer)
                if merged is not None:
                    with open(os.path.join(repo, path), "w") as f:
                        f.write(merged)
                    git(repo, "add", path)
                    resolved.append(path)
                    continue
        manual.append(path)
    return resolved, manual


def installed_skills(repo):
    with open(os.path.join(repo, "pstack", "port", "install.sh")) as f:
        body = f.read()
    block = re.search(r"^SKILLS=\((.*?)^\)", body, re.S | re.M).group(1)
    return set(block.split())


def triage(repo, base, incoming):
    ported = installed_skills(repo)
    before = set(git(repo, "ls-tree", "--name-only", f"{base}:pstack/skills").split())
    upstream = set(git(repo, "ls-tree", "--name-only", f"{incoming}:pstack/skills").split())
    report = []

    for skill in sorted(upstream - before):
        text = show(repo, f"{incoming}:pstack/skills/{skill}/SKILL.md") or ""
        terms = sorted(set(CURSOR_TERMS.findall(text)))
        verdict = f"Cursor terms: {', '.join(terms)}" if terms else "no Cursor terms, likely installable as-is"
        report.append(f"new upstream skill {skill}: {verdict}. Port it and add it to SKILLS in port/install.sh, or remove it with `git rm -r pstack/skills/{skill}`.")

    for skill in sorted((before - upstream) & ported):
        report.append(f"ported skill {skill} is gone upstream. Remove it from SKILLS in port/install.sh.")

    diff = git(repo, "diff", base, incoming, "--", "pstack/skills")
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and current:
            skill = current.split("/")[2] if current.count("/") >= 3 else None
            if skill in ported and CURSOR_TERMS.search(line):
                report.append(f"upstream added a Cursor term to {current}: {line[1:].strip()[:160]}")
    return report


def merge_head(repo):
    return subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True).returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", help="what to merge (default: <cursor/plugins remote>/main)")
    parser.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--report", help="write the outcome as JSON to this file")
    args = parser.parse_args()
    repo = os.path.realpath(args.repo)

    def write_report(**fields):
        if args.report:
            with open(args.report, "w") as f:
                json.dump(fields, f, indent=2)

    if merge_head(repo):
        incoming = "MERGE_HEAD"
        print("picking up the merge already in progress")
    else:
        if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
            sys.exit("the working tree has uncommitted changes; commit or stash them first")
        if args.ref and args.no_fetch:
            incoming = args.ref
        else:
            remote = upstream_remote(repo)
            if not args.no_fetch:
                git(repo, "fetch", "-q", remote)
            incoming = args.ref or f"{remote}/main"
        if not git(repo, "rev-list", f"HEAD..{incoming}").strip():
            print(f"already up to date with {incoming}")
            write_report(status="up_to_date", incoming=git(repo, "rev-parse", incoming).strip())
            return
        proc = subprocess.run(["git", "-C", repo, "merge", "--no-commit", "--no-ff", incoming], capture_output=True, text=True)
        print(proc.stdout.strip() or proc.stderr.strip())
        # 0 is a clean merge and 1 is conflicts. Anything else, or no merge left in progress, means git refused to merge.
        if proc.returncode not in (0, 1) or not merge_head(repo):
            sys.exit(f"git merge {incoming} failed (exit {proc.returncode}):\n{proc.stderr.strip()}")

    base = git(repo, "merge-base", "HEAD", incoming).strip()
    dropped = prune_and_keep_ours(repo)
    if dropped:
        print(f"dropped {dropped} upstream paths outside the port")
    resolved, manual = resolve_pointer_conflicts(repo)
    for path in resolved:
        print(f"resolved {path}: took upstream's text and restored the pstack-harness pointer")
    for path in manual:
        print(f"CONFLICT needs a person: {path}")

    report = triage(repo, base, incoming)
    if report:
        print("\nto port:")
        for item in report:
            print(f"- {item}")

    write_report(
        status="conflicts" if manual else "merged",
        incoming=git(repo, "rev-parse", incoming).strip(),
        dropped=dropped,
        resolved=resolved,
        manual=manual,
        to_port=report,
    )
    print("\nnext: " + ("resolve the conflicts above, then " if manual else "") + "review with `git diff --cached`, commit the merge, run port/install.sh, and run port/smoke/smoke.py.")
    sys.exit(1 if manual else 0)


if __name__ == "__main__":
    main()
