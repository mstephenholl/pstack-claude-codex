#!/usr/bin/env python3
"""Static checks for the Claude Code and Codex port of pstack.

  pstack/port/check.py

Checks that every skill is well formed for both harnesses, that the port's
wiring is in place (the pstack-harness pointer, Codex invocation policy, and
a harness mapping for every Cursor term a ported skill uses), that relative
references in skills and READMEs resolve, and that the README catalog lists
exactly the installed skills. Prints one line per problem and exits 1 if any.
Warnings (Cursor mentions no known term covers) are printed but do not fail.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
PSTACK = os.path.join(ROOT, "pstack")
SKILLS_DIR = os.path.join(PSTACK, "skills")
TEAM_KIT_DIR = os.path.join(ROOT, "cursor-team-kit", "skills")
HARNESS = os.path.join(SKILLS_DIR, "pstack-harness", "SKILL.md")
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
POINTER = re.compile(r"^Outside Cursor, first read `~/\.agents/pstack/skills/pstack-harness/SKILL\.md` in full\..*$", re.M)

# Cursor terms a ported skill may use, and the text in pstack-harness that maps each one.
CURSOR_TERMS = [
    (r"`Task`|\bTask (?:tool|call|subagent|prompts?)\b|Spawn `Task`", r"`Task` subagent", "the Task tool"),
    (r"AskQuestion", r"`AskQuestion`", "AskQuestion"),
    (r"pstack-models\.mdc|\.cursor/rules", r"pstack-models\.mdc", "the pstack-models.mdc rule"),
    (r"(?<!~/)\.cursor/skills", r"`\.cursor/skills/<name>/`", "project .cursor/skills paths"),
    (r"~/\.cursor/skills", r"`~/\.cursor/skills/<name>/`", "personal ~/.cursor/skills paths"),
    (r"agent-transcripts|\.cursor/projects", r"agent-transcripts/", "Cursor transcripts"),
    (r"grok-\d|gpt-5\.6-sol-max|claude-opus-5-5-(max|xhigh)|xhigh-fast", r"Cursor slugs", "Cursor model slugs"),
    (r"cursor-team-kit", r"`cursor-team-kit`", "cursor-team-kit skills"),
    (r"`create-skill`|\bcreate-skill\b", r"`create-skill`", "Cursor's create-skill"),
    (r"environment: \"cloud\"|cloud_base_branch", r"`environment: \"cloud\"`", "cloud workers"),
    (r"`/loop`|Cursor's `/loop`", r"`/loop`", "Cursor's /loop"),
    (r"`/visualize`", r"`/visualize`", "Cursor's /visualize"),
    (r"\bmcps/", r"`mcps/`", "MCP discovery"),
    (r"generalPurpose", r"`subagent_type: generalPurpose`", "the generalPurpose subagent"),
    (r"`readonly`|readonly: true", r"`readonly: true`", "readonly subagents"),
    (r"run_in_background", r"`run_in_background: true`", "background subagents"),
    (r"\btodolist\b", r"todolist", "Cursor's todolist"),
    (r"\bcloud agents?\b|Cursor dashboard|Cursor restart", r"Cloud agents, the Cursor dashboard, and Cursor restarts", "cloud agents"),
    (r"\bBugbot\b|\bbugbot\b", r"Bugbot", "Bugbot"),
    (r"\bOrigin\b|`origin pr", r"\bOrigin\b", "the Origin forge"),
    (r"Comment Sicko|comment-sicko", r"comment-sicko", "the Comment Sicko agent"),
    (r"poteto-agent", r"`poteto-agent`", "the poteto-agent agent"),
    (r"Cursor'?s? built-in", r"Cursor('s)? built-in", "Cursor built-ins"),
]
UNCOVERED_CURSOR = re.compile(r"\bCursor\b|\.cursor\b")
TRANSCRIPT_TERMS = re.compile(r"agent-transcripts")

problems, warnings = [], []


def rel(path):
    return os.path.relpath(path, ROOT)


def read(path):
    with open(path, errors="replace") as f:
        return f.read()


def install_list(name):
    body = read(os.path.join(PSTACK, "port", "install.sh"))
    block = re.search(rf"^{name}=\((.*?)^\)", body, re.S | re.M)
    return [w for w in block.group(1).split() if not w.startswith("#")] if block else []


def frontmatter(path):
    text = read(path)
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    fields = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            fields[m.group(1)] = value
    return fields


def skill_files(skill_dir):
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", "__pycache__")]
        for name in filenames:
            if name.endswith(".md"):
                yield os.path.join(dirpath, name)


def check_structure(skills, team_kit, agents):
    on_disk = sorted(d for d in os.listdir(SKILLS_DIR) if os.path.isdir(os.path.join(SKILLS_DIR, d)))
    for d in sorted(set(on_disk) - set(skills) - {"pstack-harness"}):
        problems.append(f"pstack/skills/{d} is not in SKILLS in port/install.sh. Port it and add it, or remove it.")
    for d in sorted(set(skills) - set(on_disk)):
        problems.append(f"SKILLS in port/install.sh lists {d}, but pstack/skills/{d} does not exist.")
    kit_on_disk = sorted(d for d in os.listdir(TEAM_KIT_DIR) if os.path.isdir(os.path.join(TEAM_KIT_DIR, d)))
    if sorted(kit_on_disk) != sorted(team_kit):
        problems.append(f"cursor-team-kit/skills holds {kit_on_disk} but TEAM_KIT_SKILLS lists {sorted(team_kit)}.")

    dirs = [(os.path.join(SKILLS_DIR, d), d in skills) for d in on_disk] + [(os.path.join(TEAM_KIT_DIR, d), True) for d in kit_on_disk]
    for skill_dir, installed in dirs:
        name = os.path.basename(skill_dir)
        path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(path):
            problems.append(f"{rel(skill_dir)} has no SKILL.md.")
            continue
        fm = frontmatter(path)
        if fm is None:
            problems.append(f"{rel(path)} has no YAML frontmatter.")
            continue
        if fm.get("name") != name:
            problems.append(f"{rel(path)}: name is {fm.get('name')!r}, but the folder is {name!r}. Both harnesses expect them to match.")
        if not NAME.match(name) or len(name) > 64:
            problems.append(f"{rel(path)}: {name!r} is not a lowercase kebab-case name of at most 64 characters.")
        description = fm.get("description", "")
        if not description:
            problems.append(f"{rel(path)} has no description.")
        elif len(description) > 1024:
            problems.append(f"{rel(path)}: the description is {len(description)} characters, over the 1024 limit.")
        if not installed:
            continue
        user_only = fm.get("disable-model-invocation") == "true"
        policy = os.path.join(skill_dir, "agents", "openai.yaml")
        explicit_only = os.path.isfile(policy) and re.search(r"allow_implicit_invocation:\s*false", read(policy))
        if user_only and not explicit_only:
            problems.append(f"{rel(path)} is user-only but has no agents/openai.yaml with allow_implicit_invocation: false. Run port/install.sh.")
        if explicit_only and not user_only:
            problems.append(f"{rel(policy)} hides a skill from Codex that Claude Code lets the model invoke.")

    for name in agents:
        path = os.path.join(PSTACK, "agents", f"{name}.md")
        if not os.path.isfile(path):
            problems.append(f"AGENTS in port/install.sh lists {name}, but pstack/agents/{name}.md does not exist.")
        elif (frontmatter(path) or {}).get("name") != name:
            problems.append(f"pstack/agents/{name}.md: the frontmatter name does not match the file name.")


def check_wiring(skills, team_kit):
    harness = read(HARNESS)
    ported = [os.path.join(SKILLS_DIR, s) for s in skills] + [os.path.join(TEAM_KIT_DIR, s) for s in team_kit]
    for skill_dir in ported:
        files = list(skill_files(skill_dir))
        text = {f: read(f) for f in files}
        corpus = "\n".join(text.values())
        skill_md = text.get(os.path.join(skill_dir, "SKILL.md"), "")
        uses = [label for term, _, label in CURSOR_TERMS if re.search(term, corpus)]
        in_team_kit = skill_dir.startswith(TEAM_KIT_DIR)
        if uses and not in_team_kit:
            pointer = POINTER.search(skill_md)
            if not pointer:
                problems.append(f"{rel(skill_dir)}/SKILL.md uses Cursor terms ({', '.join(uses)}) but has no pstack-harness pointer that says to read it in full.")
            elif TRANSCRIPT_TERMS.search(corpus) and "transcripts.py" not in pointer.group(0):
                problems.append(f"{rel(skill_dir)}/SKILL.md reads transcripts, so its pointer should name port/transcripts.py.")
        for term, marker, label in CURSOR_TERMS:
            if re.search(term, corpus) and not re.search(marker, harness):
                problems.append(f"pstack-harness does not map {label}, which {rel(skill_dir)} uses.")
        for path, body in text.items():
            for n, line in enumerate(body.splitlines(), 1):
                if POINTER.match(line) or not UNCOVERED_CURSOR.search(line):
                    continue
                if not any(re.search(term, line) for term, _, _ in CURSOR_TERMS):
                    warnings.append(f"{rel(path)}:{n}: Cursor mention no known term covers: {line.strip()[:120]}")


def check_references(skills, team_kit):
    ported = [os.path.join(SKILLS_DIR, s) for s in skills + ["pstack-harness"]] + [os.path.join(TEAM_KIT_DIR, s) for s in team_kit]
    readmes = [os.path.join(ROOT, "README.md"), os.path.join(PSTACK, "README.md"), os.path.join(PSTACK, "port", "README.md")]
    link = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
    backticked = re.compile(r"`((?:\.\./)*(?:references|playbooks|scripts|agents|features)/[^`\s]*)`")
    in_repo = re.compile(r"(?<![\w/~.:-])(pstack/(?:skills|port|agents)/[\w./-]+)")
    home_pstack = re.compile(r"~/\.agents/pstack/((?:skills|port|agents)/[\w./-]+)")
    for path in [f for d in ported for f in skill_files(d)] + readmes:
        body = read(path)
        base = os.path.dirname(path)
        skill_root = next((d for d in ported if path.startswith(d + os.sep)), base)
        for target in link.findall(body):
            if re.match(r"^[a-z]+:", target) or not re.search(r"[./]", target):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, target))):
                problems.append(f"{rel(path)} links to {target}, which does not exist.")
        # The harness and the READMEs name paths generically, not relative to themselves.
        generic = path in readmes or path.startswith(os.path.join(SKILLS_DIR, "pstack-harness") + os.sep)
        for target in [] if generic else backticked.findall(body):
            if re.search(r"[<>*{}]", target):
                continue
            candidates = [os.path.join(base, target), os.path.join(skill_root, target)]
            if not any(os.path.exists(os.path.normpath(c)) for c in candidates):
                problems.append(f"{rel(path)} refers to `{target}`, which does not exist.")
        for target in in_repo.findall(body):
            target = target.rstrip(".")
            if not re.search(r"[<>*{}]", target) and not os.path.exists(os.path.join(ROOT, target)):
                problems.append(f"{rel(path)} refers to {target}, which does not exist.")
        for target in home_pstack.findall(body):
            target = target.rstrip(".")
            if not re.search(r"[<>*{}]", target) and not os.path.exists(os.path.join(PSTACK, target)):
                problems.append(f"{rel(path)} refers to ~/.agents/pstack/{target}, which does not exist.")


def check_catalog(skills, team_kit):
    # Only the table whose header has a Skill column is the catalog.
    listed, column = set(), None
    for line in read(os.path.join(PSTACK, "README.md")).splitlines():
        if not line.startswith("|"):
            column = None
            continue
        cells = [c.strip() for c in line.split("|")]
        if column is None:
            column = cells.index("Skill") if "Skill" in cells else -1
        elif column > 0 and len(cells) > column:
            listed.update(re.findall(r"`([a-z0-9*-]+)`", cells[column]))
    expected = {s for s in skills if not s.startswith("principle-")} | set(team_kit)
    for name in sorted(expected - listed):
        problems.append(f"pstack/README.md's skill table does not list {name}.")
    if any(s.startswith("principle-") for s in skills) and "principle-*" not in listed:
        problems.append("pstack/README.md's skill table has no principle-* row.")
    for name in sorted(listed - expected - {"principle-*"}):
        problems.append(f"pstack/README.md's skill table lists {name}, which is not an installed skill.")


def main():
    skills, team_kit, agents = install_list("SKILLS"), install_list("TEAM_KIT_SKILLS"), install_list("AGENTS")
    if not skills:
        sys.exit("could not read SKILLS from pstack/port/install.sh")
    check_structure(skills, team_kit, agents)
    check_wiring(skills, team_kit)
    check_references(skills, team_kit)
    check_catalog(skills, team_kit)
    for w in warnings:
        print(f"warning: {w}")
    for p in problems:
        print(f"problem: {p}")
    print(f"{len(problems)} problems, {len(warnings)} warnings across {len(skills)} pstack skills and {len(team_kit)} team-kit skills.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
