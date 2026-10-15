#!/usr/bin/env bash
# Runs install.sh against a throwaway HOME and checks what it links, that a
# rerun changes nothing, that it writes an agent for every Claude effort, and
# that --uninstall removes all of it. Pass a bash binary to test a specific one:
#   install_test.sh [/bin/bash]
set -euo pipefail

shell=${1:-bash}
port="$(cd "$(dirname "$0")/.." && pwd)"
home=$(mktemp -d)
trap 'rm -rf "$home"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

count_list() {
  sed -n "/^$1=(/,/^)/p" "$port/install.sh" | grep -cE '^  [a-z]'
}

links_in() {
  find "$1" -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' '
}

effort_agents() {
  find "$home/.claude/agents" -maxdepth 1 -type f -name 'pstack-effort-*.md' 2>/dev/null | wc -l | tr -d ' '
}

skills=$(($(count_list SKILLS) + $(count_list TEAM_KIT_SKILLS)))
agents=$(count_list AGENTS)

HOME=$home "$shell" "$port/install.sh" >/dev/null || fail "install exited nonzero"
[[ $(links_in "$home/.claude/skills") == "$skills" ]] || fail "expected $skills Claude Code skill links, got $(links_in "$home/.claude/skills")"
[[ $(links_in "$home/.codex/skills") == "$skills" ]] || fail "expected $skills Codex skill links, got $(links_in "$home/.codex/skills")"
[[ $(links_in "$home/.claude/agents") == "$agents" ]] || fail "expected $agents agent links"
[[ -L $home/.agents/pstack ]] || fail "the .agents/pstack link is missing"
[[ -f $home/.claude/skills/how/SKILL.md ]] || fail "a skill link does not resolve"
for effort in low medium high xhigh max; do
  grep -q "^effort: $effort\$" "$home/.claude/agents/pstack-effort-$effort.md" 2>/dev/null || fail "no pstack-effort-$effort agent that pins its effort"
done

rerun=$(HOME=$home "$shell" "$port/install.sh")
[[ -z $rerun ]] || fail "a rerun changed something: $rerun"

printf 'swarm workers: claude:sonnet@high, codex:gpt-6.1-sol@high\n' >"$home/.agents/pstack-models.md"
HOME=$home "$shell" "$port/install.sh" >/dev/null || fail "install with a models file exited nonzero"
[[ $(effort_agents) == 5 ]] || fail "a models file changed which effort agents exist"

PSTACK_HARNESSES=codex HOME=$home "$shell" "$port/install.sh" >/dev/null || fail "a Codex-only install exited nonzero"
[[ $(links_in "$home/.claude/skills") == 0 ]] || fail "a Codex-only install left Claude Code skill links"
[[ $(links_in "$home/.claude/agents") == 0 ]] || fail "a Codex-only install left Claude Code agents"
[[ $(effort_agents) == 0 ]] || fail "a Codex-only install left effort agents"
[[ $(links_in "$home/.codex/skills") == "$skills" ]] || fail "a Codex-only install dropped Codex skill links"
PSTACK_HARNESSES="" HOME=$home "$shell" "$port/install.sh" >/dev/null || fail "an install for no harnesses exited nonzero"
[[ $(links_in "$home/.claude/skills") == 0 && $(links_in "$home/.codex/skills") == 0 ]] || fail "an empty PSTACK_HARNESSES still linked skills"
HOME=$home "$shell" "$port/install.sh" >/dev/null || fail "reinstalling both harnesses exited nonzero"
[[ $(links_in "$home/.claude/skills") == "$skills" ]] || fail "reinstalling both harnesses did not restore Claude Code"
[[ $(effort_agents) == 5 ]] || fail "reinstalling both harnesses did not restore the effort agents"

# A second clone takes over the first one's links, and survives the first being deleted.
other=$(cd -P "$(mktemp -d)" && pwd)
trap 'rm -rf "$home" "$other"' EXIT
repo="$(dirname "$(dirname "$port")")"
mkdir -p "$other/clone"
(cd "$repo" && tar cf - --exclude node_modules pstack cursor-team-kit) | (cd "$other/clone" && tar xf -)
HOME=$home "$shell" "$other/clone/pstack/port/install.sh" >"$other/out" 2>&1 || fail "installing from a second clone failed: $(cat "$other/out")"
[[ $(readlink "$home/.claude/skills/how") == "$other/clone/pstack/skills/how" ]] || fail "the second clone did not take over the links"
[[ $(readlink "$home/.agents/pstack") == "$other/clone/pstack" ]] || fail "the second clone did not take over .agents/pstack"
rm -rf "$other/clone"
HOME=$home "$shell" "$port/install.sh" >"$other/out" 2>&1 || fail "reinstalling after deleting the second clone failed: $(cat "$other/out")"
[[ $(readlink "$home/.claude/skills/how") == "$port/../skills/how" || -f $home/.claude/skills/how/SKILL.md ]] || fail "dangling links from a deleted clone were not replaced"

HOME=$home "$shell" "$port/install.sh" --uninstall >/dev/null || fail "uninstall exited nonzero"
left=$(find "$home/.claude" "$home/.codex" "$home/.agents" -type l | wc -l | tr -d ' ')
[[ $left == 0 ]] || fail "$left links survived --uninstall"
[[ $(effort_agents) == 0 ]] || fail "--uninstall left effort agents"
[[ -f $home/.agents/pstack-models.md ]] || fail "--uninstall removed the models file"

# shellcheck disable=SC2016 # $BASH_VERSION must expand in the shell under test.
echo "install test passed with bash $("$shell" -c 'echo $BASH_VERSION'): $skills skills, $agents agents"
