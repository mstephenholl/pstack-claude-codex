#!/usr/bin/env bash
# Links the pstack skills that work outside Cursor into Claude Code and Codex.
# Idempotent: re-run after pulling upstream or editing the lists below.
# `install.sh --uninstall` removes every link and generated agent it made.
# PSTACK_HARNESSES picks the harnesses to install into, "claude codex" when
# unset; links in a harness left out are removed. The `pstack` command
# (port/pstack) sets it from its saved choice.
# Runs under macOS's stock bash 3.2, which treats an empty "${array[@]}" as
# unbound under set -u, hence the ${array[@]+"${array[@]}"} expansions.
#
# The clone must live outside ~/.agents/skills. Codex scans that directory
# recursively and would load every pstack skill, ported or not.
set -euo pipefail

# pstack skills ported so far. Anything not listed stays uninstalled.
# pstack-harness is read by path through ~/.agents/pstack, so it is not linked.
SKILLS=(
  architect
  arena
  automate-me
  benchmark-checklist
  blast-radius
  bro
  correct
  create-formal-verification-skill
  create-verification-skill
  figure-it-out
  how
  in-your-own-words
  interrogate
  ketchup
  ketchup-video
  maintain-formal-verification-skill
  maintain-verification-skill
  no-comments
  poteto-mode
  pstack-tdd
  pstack-teach
  recall
  reflect
  setup-pstack
  sharpen
  show-me-your-work
  statechart
  swarm
  technical-writing
  typescript-best-practices
  unslop
  why
  principle-attack-the-premise
  principle-boundary-discipline
  principle-build-the-lever
  principle-encode-lessons-in-structure
  principle-exhaust-the-design-space
  principle-experience-first
  principle-explain-the-number
  principle-fix-root-causes
  principle-foundational-thinking
  principle-guard-the-context-window
  principle-laziness-protocol
  principle-make-operations-idempotent
  principle-migrate-callers-then-delete-legacy-apis
  principle-minimize-reader-load
  principle-model-the-domain
  principle-never-block-on-the-human
  principle-outcome-oriented-execution
  principle-prove-it-works
  principle-redesign-from-first-principles
  principle-separate-before-serializing-shared-state
  principle-sequence-verifiable-units
  principle-subtract-before-you-add
  principle-test-behavior-not-implementation
  principle-type-system-discipline
)

# cursor-team-kit skills that poteto-mode calls. They need no porting.
# A sparse clone needs `git sparse-checkout add cursor-team-kit`.
TEAM_KIT_SKILLS=(
  control-cli
  control-ui
  deslop
)

# pstack subagents. Claude Code only; Codex spawns them per pstack-harness.
AGENTS=(
  comment-sicko
  poteto-agent
)

pstack="$(cd -P "$(dirname "$0")/.." && pwd)"
root="$(dirname "$pstack")"
agent_target="$HOME/.claude/agents"
harnesses=" ${PSTACK_HARNESSES-claude codex} "
failed=0

if [[ ${1:-} == --uninstall ]]; then
  SKILLS=()
  TEAM_KIT_SKILLS=()
  AGENTS=()
  uninstall=1
else
  uninstall=0
fi

# Codex ignores `disable-model-invocation` and reads agents/openai.yaml instead.
write_codex_policy() {
  local dir=$1 policy=$1/agents/openai.yaml
  grep -q '^disable-model-invocation: true' "$dir/SKILL.md" || return 0
  if [[ -e $policy ]]; then
    grep -q 'allow_implicit_invocation: false' "$policy" ||
      echo "warn: $policy exists without allow_implicit_invocation: false" >&2
    return 0
  fi
  mkdir -p "$dir/agents"
  printf 'policy:\n  allow_implicit_invocation: false\n' >"$policy"
}

# True when a link points into another pstack clone, or at a pstack path that
# no longer exists, so moving or re-cloning pstack relinks instead of failing.
from_other_clone() {
  local dest other
  dest=$(readlink "$1")
  case $dest in
    */cursor-team-kit/skills/*) other=${dest%/cursor-team-kit/skills/*} ;;
    */pstack/skills/* | */pstack/agents/*) other=${dest%/pstack/*} ;;
    */pstack) other=${dest%/pstack} ;;
    *) return 1 ;;
  esac
  [[ $other != "$root" ]] && { [[ -f $other/pstack/port/install.sh ]] || [[ ! -e $dest ]]; }
}

# link <source> <link path>
link() {
  local source=$1 target=$2
  if [[ -L $target && $(readlink "$target") == "$source" ]]; then
    return 0
  fi
  if [[ -L $target ]] && from_other_clone "$target"; then
    rm "$target"
    ln -s "$source" "$target"
    echo "relinked $target"
    return 0
  fi
  if [[ -e $target || -L $target ]]; then
    echo "skip: $target already exists and is not a pstack link" >&2
    failed=1
    return 0
  fi
  ln -s "$source" "$target"
  echo "linked $target"
}

# Removes links into this clone, or another pstack clone, whose names are not
# in the given list.
# prune <dir> <suffix> <name>...
prune() {
  local dir=$1 suffix=$2 link name
  shift 2
  for link in "$dir"/*; do
    [[ -L $link ]] || continue
    [[ $(readlink "$link") == "$root/"* ]] || from_other_clone "$link" || continue
    name=$(basename "$link" "$suffix")
    [[ " $* " == *" $name "* ]] && continue
    rm "$link"
    echo "unlinked $link"
  done
}

sources=()
for name in ${SKILLS[@]+"${SKILLS[@]}"}; do sources+=("$pstack/skills/$name"); done
for name in ${TEAM_KIT_SKILLS[@]+"${TEAM_KIT_SKILLS[@]}"}; do sources+=("$root/cursor-team-kit/skills/$name"); done

installed=()
for dir in ${sources[@]+"${sources[@]}"}; do
  if [[ ! -f $dir/SKILL.md ]]; then
    echo "missing: $dir/SKILL.md" >&2
    failed=1
    continue
  fi
  write_codex_policy "$dir"
  installed+=("$dir")
done

if ((uninstall)); then
  if [[ -L $HOME/.agents/pstack && $(readlink "$HOME/.agents/pstack") == "$pstack" ]]; then
    rm "$HOME/.agents/pstack"
    echo "unlinked $HOME/.agents/pstack"
  fi
else
  mkdir -p "$HOME/.agents"
  link "$pstack" "$HOME/.agents/pstack"
fi

for harness in claude codex; do
  target="$HOME/.$harness/skills"
  if ((!uninstall)) && [[ $harnesses == *" $harness "* ]]; then
    mkdir -p "$target"
    prune "$target" "" ${SKILLS[@]+"${SKILLS[@]}"} ${TEAM_KIT_SKILLS[@]+"${TEAM_KIT_SKILLS[@]}"}
    for dir in ${installed[@]+"${installed[@]}"}; do
      link "$dir" "$target/$(basename "$dir")"
    done
  elif [[ -d $target ]]; then
    prune "$target" ""
  fi
done

claude=0
if ((!uninstall)) && [[ $harnesses == *" claude "* ]]; then
  claude=1
  mkdir -p "$agent_target"
  prune "$agent_target" .md ${AGENTS[@]+"${AGENTS[@]}"}
  for name in ${AGENTS[@]+"${AGENTS[@]}"}; do
    link "$pstack/agents/$name.md" "$agent_target/$name.md"
  done
elif [[ -d $agent_target ]]; then
  prune "$agent_target" .md
fi

# Claude Code's Agent tool takes a model per spawn but not an effort, so each
# effort gets a generated agent that pins it. Skills move a subagent's effort
# off its role's entry to fit the job, so every effort needs one.
efforts=()
if ((claude)); then
  efforts=(low medium high xhigh max)
fi

for file in "$agent_target"/pstack-effort-*.md; do
  [[ -f $file ]] || continue
  effort=$(basename "$file" .md)
  effort=${effort#pstack-effort-}
  [[ " ${efforts[*]-} " == *" $effort "* ]] && continue
  rm "$file"
  echo "removed $file"
done
for effort in ${efforts[@]+"${efforts[@]}"}; do
  file="$agent_target/pstack-effort-$effort.md"
  content=$(sed "s/{{effort}}/$effort/g" "$pstack/port/effort-agent.md")
  [[ -f $file && $(cat "$file") == "$content" ]] && continue
  printf '%s\n' "$content" >"$file"
  echo "wrote $file"
done

exit $failed
