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
   newly added to skills already ported. Because the port keeps its own
   pstack/README.md, it also reports each skill our README names whose row
   upstream rewrote in its README, and each ported skill that now needs a
   runtime it did not: a new kind of script, interpreter, or package.json
   engine, or an added line that names a runtime version. Last, it reports
   each file of ours with a line that names a path the merge deletes, such
   as a harness note or a project skill about a script upstream removed.

--report writes the outcome as JSON for CI: status (up_to_date, merged,
or conflicts), the incoming commit, and what was dropped, resolved, left
for a person, and needs porting. When upstream changed only paths the port
drops or keeps its own copy of, the merge would change nothing, so the
script aborts it and reports up_to_date.

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
KEEP_DIRS = ("pstack/", "cursor-team-kit/skills/deslop/", "cursor-team-kit/skills/control-ui/", "cursor-team-kit/skills/control-cli/",
             ".agents/skills/", ".claude/skills/")
KEEP_WORKFLOWS = re.compile(r"\.github/workflows/pstack-[^/]+\.yml")
DROP = re.compile(r"^pstack/((\.cursor-plugin|assets|automations|docs|skills/make-bot-ui|skills/poteto-help)/|plugin\.json$)")
OURS = {"README.md", "pstack/README.md", ".github/dependabot.yml", ".github/SECURITY.md"}

CURSOR_TERMS = re.compile(
    r"\bTask\b|AskQuestion|\.cursor/|pstack-models\.mdc|grok-|gpt-5\.6|claude-opus-5-5-max|agent-transcripts"
    r"|cursor-team-kit|create-skill|environment: \"cloud\"|cloud_base_branch|Cursor's built-in"
)
# What a user needs to run a skill's scripts. Tests are left out, since only upstream's CI runs them.
SCRIPT_KINDS = {".ts": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript", ".tsx": "TypeScript",
                ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".py": "Python",
                ".rb": "Ruby", ".go": "Go", ".rs": "Rust"}
RUNTIME_VERSION = re.compile(r"\b(?:Node(?:\.js)?|Bun|Deno|Python|Java|Ruby|Go)\s+v?\d+(?:\.\d+)+")
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|\.(test|spec)\.")
README_ROW = re.compile(r"^\|.*\(\./skills/([^/)]+)/SKILL\.md\)")


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


def upstream_readme_rows(repo, commit):
    """Upstream's pstack/README.md table rows at commit, by the skill each one links to."""
    rows = {}
    for line in (show(repo, f"{commit}:pstack/README.md") or "").splitlines():
        match = README_ROW.match(line)
        if match:
            rows[match.group(1)] = line
    return rows


def readme_names(repo):
    """The skills our pstack/README.md names one by one in its skill column."""
    names = set()
    for line in (show(repo, "HEAD:pstack/README.md") or "").splitlines():
        cells = line.split("|")
        if line.startswith("|") and len(cells) > 3:
            names.update(re.findall(r"`([a-z0-9-]+)`", cells[2]))
    return names


def script_kinds(repo, commit, skill):
    """What a skill's scripts at commit run on, each with the first file that needs it."""
    prefix = f"pstack/skills/{skill}/"
    kinds = {}
    for entry in git(repo, "ls-tree", "-r", "-z", commit, "--", prefix).split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, _, blob = meta.split()
        rel = path[len(prefix):]
        if TEST_PATH.search(rel):
            continue
        kind = SCRIPT_KINDS.get(os.path.splitext(rel)[1])
        if kind:
            kinds.setdefault(kind, rel)
        if mode == "100755":
            # Read bytes, since an executable can be a binary that is not valid text.
            content = subprocess.run(["git", "-C", repo, "cat-file", "-p", blob], capture_output=True).stdout
            first = content.split(b"\n", 1)[0].decode(errors="replace")
            words = first[2:].split() if first.startswith("#!") else []
            if words and os.path.basename(words[0]) == "env":
                words = [w for w in words[1:] if not w.startswith("-")]
            if words:
                kinds.setdefault(os.path.basename(words[0]), rel)
        if os.path.basename(rel) == "package.json":
            try:
                for name, wanted in json.loads(git(repo, "cat-file", "-p", blob)).get("engines", {}).items():
                    kinds.setdefault(f"{name} {wanted}", rel)
            except (ValueError, AttributeError):
                pass
    return kinds


def ancestors(path):
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def endings(path):
    """Every ending of path that keeps at least two of its parts."""
    parts = path.split("/")
    return ["/".join(parts[i:]) for i in range(len(parts) - 1)]


def deleted_names(repo, incoming):
    """Each of our files with a line that names a path this merge deletes, with the first such line.

    A path is named by any ending of two or more of its parts that no path left in the port shares.
    Our lines are the ones upstream's version lacks, so upstream's own text never counts.
    """
    gone = set(git(repo, "diff", "--cached", "--name-only", "--no-renames", "--diff-filter=D", "HEAD").splitlines())
    if not gone:
        return []
    present = set(git(repo, "ls-files").splitlines())
    present |= {d for path in present for d in ancestors(path)}
    gone |= {d for path in gone for d in ancestors(path)} - present
    taken = {e for path in present for e in endings(path)}
    owners = {}
    for path in gone:
        for ending in endings(path):
            owners.setdefault(ending, set()).add(path)
    names = {ending: paths.pop() for ending, paths in owners.items() if len(paths) == 1 and ending not in taken}
    if not names:
        return []
    pattern = re.compile(r"(?<![\w.-])(" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + r")(?![\w-]|\.\w)")

    found = {}
    current = None
    for line in git(repo, "diff", "--cached", "--no-renames", "-U0", incoming).splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and current:
            text = line[1:]
            for match in pattern.finditer(text):
                found.setdefault((current, names[match.group(1)]), text[max(0, match.start() - 60):].strip()[:160])
    return [f"upstream deleted {path}, and our {ours} still names it: {line}" for (ours, path), line in sorted(found.items())]


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

    rows_before, rows_after = upstream_readme_rows(repo, base), upstream_readme_rows(repo, incoming)
    for skill in sorted(ported & readme_names(repo)):
        if skill in rows_before and skill in rows_after and rows_before[skill] != rows_after[skill]:
            summary = [cell.strip() for cell in rows_after[skill].split("|") if cell.strip()][-1]
            report.append(f"upstream rewrote its README row for {skill}, and the merge keeps our pstack/README.md. "
                          f"Check our row against upstream's: {summary[:160]}")

    for skill in sorted(ported & before & upstream):
        old, new = script_kinds(repo, base, skill), script_kinds(repo, incoming, skill)
        added = [f"{kind} ({new[kind]})" for kind in sorted(set(new) - set(old))]
        if added:
            report.append(f"upstream's {skill} now has scripts that need {', '.join(added)}. "
                          "Check what runs them, and the Requirements in pstack/README.md.")

    diff = git(repo, "diff", base, incoming, "--", "pstack/skills")
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and current:
            skill = current.split("/")[2] if current.count("/") >= 3 else None
            if skill in ported and CURSOR_TERMS.search(line):
                report.append(f"upstream added a Cursor term to {current}: {line[1:].strip()[:160]}")
            if skill in ported and RUNTIME_VERSION.search(line) and not TEST_PATH.search(current):
                report.append(f"upstream added a runtime version to {current}, so check the Requirements in pstack/README.md: {line[1:].strip()[:160]}")
    return report + deleted_names(repo, incoming)


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

    unchanged = not manual and not git(repo, "status", "--porcelain", "--untracked-files=no").strip()
    write_report(
        status="conflicts" if manual else "up_to_date" if unchanged else "merged",
        incoming=git(repo, "rev-parse", incoming).strip(),
        dropped=dropped,
        resolved=resolved,
        manual=manual,
        to_port=report,
    )
    if unchanged:
        git(repo, "merge", "--abort")
        print("\nupstream changes nothing the port keeps, so there is nothing to merge")
        return
    print("\nnext: " + ("resolve the conflicts above, then " if manual else "") + "review with `git diff --cached`, commit the merge, run port/install.sh, and run port/smoke/smoke.py.")
    sys.exit(1 if manual else 0)


if __name__ == "__main__":
    main()
