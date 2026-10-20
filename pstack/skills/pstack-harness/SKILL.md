---
name: pstack-harness
description: "Maps pstack's Cursor tools, model slugs, and paths to Claude Code and Codex. Other pstack skills read it before they spawn subagents, pick models, ask questions, or read transcripts."
disable-model-invocation: true
---

# pstack harness

pstack was written for Cursor. This file translates its Cursor vocabulary for Claude Code and Codex. When a pstack skill or playbook names a Cursor tool, model slug, or path, apply the mapping here and otherwise follow the skill as written.

You are in Claude Code if your tools include `Agent`. You are in Codex if your tools include `spawn_agent`.

## Paths

- pstack lives at `~/.agents/pstack`. Skills are `~/.agents/pstack/skills/<name>/SKILL.md`. Agent definitions are in `~/.agents/pstack/agents/`.
- Model config is `~/.agents/pstack-models.md`. Read it wherever a skill says `~/.cursor/rules/pstack-models.mdc` or "the `pstack-models.mdc` rule".
- A skill that writes a project-local skill to `.cursor/skills/<name>/` writes it to `.agents/skills/<name>/` instead, with `.claude/skills/<name>` symlinked to it so both harnesses find it.
- A personal skill that would go in `~/.cursor/skills/<name>/` goes in `~/.agents/skills/<name>/`, with `~/.claude/skills/<name>` symlinked to it. Codex reads `~/.agents/skills` directly. It lives outside any repo, so it gets no worktree or PR. Tell the user its path.
- In Codex, the sandbox keeps a repo's `.agents/` read-only, and `~/.agents/` and `~/.claude/` are outside it. Run each shell command that writes there with `sandbox_permissions: "require_escalated"` and a `justification` naming the skill, from the first attempt. If the write is denied, stop and tell the user. Never put the skill somewhere Codex does not look, such as `.cursor/skills/`.
- To find existing skills where a skill searches `.cursor/skills/` or `~/.cursor/skills/`, search `.agents/skills/`, `.claude/skills/`, `~/.agents/skills/`, `~/.claude/skills/`, and `~/.codex/skills/`, following symlinks.
- A skill you write with `disable-model-invocation: true` also needs `agents/openai.yaml` containing `policy:` and `  allow_implicit_invocation: false`. Codex ignores the frontmatter flag.

## Running another pstack skill

Most pstack skills are user-only, so they are hidden from you and the `Skill` tool cannot load them. When a skill says to run, use, apply, or read another skill (`how`, the **arena** skill, the **laziness-protocol** principle skill), read `~/.agents/pstack/skills/<name>/SKILL.md` and follow it. Principle skills are named `principle-<name>`.

| pstack says | Use |
|---|---|
| `tdd` | `pstack-tdd` |
| `teach` | `pstack-teach` |
| `create-skill` (Cursor built-in) | The harness's `skill-creator` skill |
| `deslop`, `control-ui`, `control-cli` (`cursor-team-kit`) | Installed under the same names. Invoke them normally. |
| `verify-this` (`cursor-team-kit`) | Not installed. Save the before/after evidence yourself. |
| Cursor's built-in `babysit` | Not present. The Babysit playbook is the only one. |

## Tools

| Cursor | Claude Code | Codex |
|---|---|---|
| `Task` subagent | `Agent` | `spawn_agent` (`task_name`, `message`, `model`, `reasoning_effort`), then `wait_agent` |
| `subagent_type: generalPurpose` | `pstack-effort-<effort>` at the effort Models picks, or `general-purpose` for an `inherit-parent` entry | The default agent type |
| `readonly: true` | `pstack-effort-<effort>` told "read-only, do not edit files", since `Explore` cannot carry an effort. Use `Explore` only for an `inherit-parent` entry. | Say "read-only, do not edit files" in the message |
| `run_in_background: true` | Agents already run in the background and notify you when done. In a headless run (`claude -p`) the session ends with your turn, so never end a turn while an agent or background command is still running. | `spawn_agent` returns at once. Collect with `wait_agent`. |
| `model` | `model`: `opus`, `sonnet`, `haiku`, or `fable`. The effort Models picks chooses the `pstack-effort-<effort>` agent type. `port/install.sh` generates one for each effort. | `model` and `reasoning_effort` |
| `environment: "cloud"`, `cloud_base_branch` | No cloud workers. Run locally. A writer gets its own worktree (`isolation: "worktree"`). | No cloud workers. Run locally. Create a git worktree per writer and name it in the message. |
| `AskQuestion` | `AskUserQuestion` | `request_user_input` when available, else ask in plain text |
| An approval gate with no human present (`claude -p`, `codex exec`) | Put the pending choices in the final message and stop. Take no gated or "automatic" outside action, such as filing to a tracker. | Same |
| todolist | Your task or todo tool | `update_plan` |
| `/loop` | The `loop` skill (`/loop 1h <tick prompt>`), or `Monitor` for an event | See the tick rule under Long runs |
| `/visualize` | A Mermaid diagram, or a chart image you render with a short script and give by path | A Mermaid diagram, or a chart image you render with a short script and give by path |
| MCP discovery (the `mcps/` directory) | MCP tools are named `mcp__<server>__<tool>`. Search deferred tools too. | `codex mcp list`, plus the MCP tools in your tool list |

Named agents:

| pstack agent | Claude Code | Codex |
|---|---|---|
| `poteto-agent` | `subagent_type: "poteto-agent"` for an `inherit-parent` entry. Otherwise use `pstack-effort-<effort>` at the effort Models picks, with the Codex message opening. | A default agent whose message starts with the body of `~/.agents/pstack/agents/poteto-agent.md` |
| `comment-sicko` | `subagent_type: "comment-sicko"` | A default agent whose message starts with the body of `~/.agents/pstack/agents/comment-sicko.md` |

## Models

Skill text names Cursor slugs (`grok-4.7-xhigh-fast`, `claude-opus-5-5-xhigh`) as defaults. Ignore them. Resolve every role from `~/.agents/pstack-models.md`, and use the table below when the file or the role's line is missing.

An entry is `<harness>:<model>[@<effort>]`, where harness is `claude` or `codex`, or `inherit-parent` (also written `auto`), which means a native subagent on your own model. An entry is native when its harness is yours and foreign otherwise.

- Panel roles (`arena runners`, `architect runners`, `interrogate reviewers`) get one seat per entry.
- `arena cross-judge pool` and `reflect tooling` get one seat. Prefer a foreign entry, since that is a different model family from yours. For `reflect tooling`, stay native when the transcript's lookups need MCP servers only your harness has configured.
- Every other role takes the first native entry. With no native entry, it takes the first entry and runs it foreign.

If the file has the line `foreign seats: off`, never run a foreign entry, because a foreign seat sends code or transcripts to the other vendor. Drop every foreign entry before resolving. Panels then seat only their native entries, and the pool and `reflect tooling` take their first native entry. A role left with no native entry runs as `inherit-parent`. Without that line, foreign seats are on.

Run a native entry as a subagent with its model and effort, as the Tools table says. Run a foreign entry through the other harness's CLI, below.

Skills say to pick each subagent's reasoning level and fast or standard speed for the job. Here that moves only the effort. Start from the entry's effort, or `xhigh` when it has none, and move it as the skill says among `low`, `medium`, `high`, `xhigh`, and `max`. Keep the entry's harness and model. If the model does not support that effort, use the nearest one it does. A foreign seat passes the effort to its CLI. Neither harness has fast mode per subagent, so every subagent runs at standard speed. An `inherit-parent` entry stays on your own model and effort.

| Role | Default |
|---|---|
| `feature, refactoring`, `bug-fix`, `perf-issue`, `hillclimb`, `how explorer`, `why investigators`, `reflect tooling`, `swarm workers` | `claude:sonnet, codex:gpt-6.1-sol` |
| `judgment and prose`, `hardest tasks`, `how explainer`, `why synthesizer`, `reflect judgment, divergent, synthesizer` | `claude:opus, codex:gpt-6-astra` |
| `arena runners`, `architect runners`, `interrogate reviewers`, `arena cross-judge pool` | `claude:opus, codex:gpt-6-astra` |

If a model is rejected, use that role's default for the same harness and say so.

## Foreign seats

A foreign seat cannot see your conversation. Write its full prompt to a file that stands alone (task, paths, rubric, output shape), run the CLI in the background, and read its final message from the output file. Wait for every seat before you synthesize. In a headless run (`claude -p`, `codex exec`) the session ends with your turn, so stay in the turn until each seat's command exits. Run it in the seat's own worktree when it writes files, otherwise in the repo root. Omit the effort flag when the entry has none.

From Claude Code, a `codex:` seat:

```bash
codex exec -m <model> -c model_reasoning_effort=<effort> -s read-only --skip-git-repo-check \
  -C <dir> -o <out.md> - < <prompt.md>
```

A seat that writes files uses `-s workspace-write`.

From Codex, a `claude:` seat:

```bash
cd <dir> && claude -p --model <model> --effort <effort> \
  --disallowedTools "Edit,Write,NotebookEdit" < <prompt.md> > <out.md>
```

A seat that writes files uses `--permission-mode auto` in place of `--disallowedTools`.

Each CLI needs network access and its own login. Inside Codex's sandbox, `claude` answers "Not logged in". So in Codex, run the `claude` command with `sandbox_permissions: "require_escalated"` and a `justification` naming the seat, from the first attempt. If Claude Code's Bash sandbox is on, run `codex` with the sandbox disabled for that call.

Never work around a seat's login. Do not read keychain entries, copy or point at another config directory (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`), or edit `~/.claude.json` or `~/.codex/auth.json`. If escalation is denied, report that seat as BLOCKED with the reason and finish with the seats that ran.

## Transcripts

Wherever a skill reads the active workspace's `agent-transcripts/` directory, use `~/.agents/pstack/port/transcripts.py`. It reads both harnesses' session logs:

- `transcripts.py current` prints this session's transcript path. Use it in place of guessing the newest file or checking a first line.
- `transcripts.py list [--cwd DIR] [--days N] [--harness claude|codex] [--subagents]` lists sessions from both harnesses whose working directory is DIR (default: the current one) or below it, newest first by real modification time. It prints TSV: modified, harness, session id, path, title. It leaves out subagent threads unless you pass `--subagents`. `--days` defaults to 7.
- `transcripts.py dump <path> [--no-tools] [--max-chars N]` turns a transcript into one readable line per user message, assistant message, tool call, and tool result. A raw log is mostly bookkeeping, so dump before reading or grepping. `--no-tools` keeps only the conversation. When a skill hands a transcript to a subagent, write the dump to a file with a high limit (`transcripts.py dump <path> --max-chars 20000 > /tmp/pstack-transcript-<id>.txt`) and pass that path. User turns are never cut. A `[...N more chars]` marker means the entry was cut, so read the file it names from disk before judging it.

Raw logs live at `~/.claude/projects/<slug>/<session-id>.jsonl` (Claude Code) and `~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread-id>.jsonl` (Codex). Stay inside the active project's transcripts, as the Cursor rule says. `list` does that by default.

## Long runs

For Orchestrate, Autopilot, Autonomous run, Multi-phase plan, Session pickup, and Pause safely:

- **Scale.** Everything runs on this machine. Keep 3 to 5 writers and 1 to 3 live UI lanes in flight. Default to a flat tree where you drain every worker yourself. A Claude Code subagent ends with its turn, so a sub-coordinator is one blocking pass per wave, not a durable layer.
- **Resume an agent.** Claude Code: `SendMessage` to its id or name, a deferred tool you load first. Codex: `send_message` to a running agent, `followup_task` for more work. Either one restarts an idle agent, so never resume one just to check on it.
- **Check on an agent.** Claude Code has no agent list. Record each id from the spawn result in the unit's row, then probe side effects: `git -C <worktree> log -1`, `gh pr view`. No notification and no side effect past the expected runtime means stuck. Codex: `list_agents`, or `wait_agent` with a short `timeout_ms`.
- **Stop an agent.** Claude Code: `TaskStop` on its task id. Codex: `interrupt_agent`. For a hold, stop it, write the stop in the standing orders, and respawn from the brief and branch on release. The worktree survives.
- **The hourly tick.** Claude Code: `/loop 1h <tick prompt>`. Codex: with agents in flight, `wait_agent` with a one-hour `timeout_ms`, which returns early on a completion. With none in flight, `sleep 3600` with the command timeout set above it. A plan keeps the literal `/loop 1h` line in Codex too, since `check-plan.mjs` requires it.
- **Waiting on CI inside a subagent.** `gh pr checks <pr> --watch`. In Claude Code, run it with `run_in_background` or through `Monitor`, since foreground `sleep` is blocked.
- **Reader and verifier lanes.** A lane that checks out a SHA gets its own detached worktree, `git worktree add --detach /tmp/swarm-<pr>/wt-<n> <sha>`, with its own port and browser profile. Remove it when done. Never `git checkout` in the shared checkout.
- **Trunk copies of pstack.** A plan line like `git show origin/main:pstack/skills/<path>` assumes pstack lives in the repo you are working on. It does not here. Read `~/.agents/pstack/skills/<path>` from disk and keep the literal line in plans, since `check-plan.mjs` requires it. Run that checker as `node ~/.agents/pstack/skills/poteto-mode/scripts/check-plan.mjs <plan.md>`.
- **The agent store.** Where a playbook says "the current agent's store (path in the system prompt)", use `~/.agents/pstack-store/<repo-slug>/`. Pass `--store` to every `orch` call, because shell state does not persist between calls.
- **Frontier without `gt`.** `orch frontier set` shells out to Graphite's `gt`. Without it, build `frontier.json` from `gh pr list --json number,headRefName,headRefOid,baseRefName`.
- **Screenshots and video.** A terminal shows neither. Record with `control-ui`'s CDP screencast or `screencapture -v`, then give file paths or attach them to a PR comment.
- **After a restart.** Nothing local survives. Resume the coordinator (`claude --continue`, `codex resume`) and re-arm the tick. Treat every in-flight unit as dead. Check `git worktree list`, commit and push dirty worktrees as `wip:`, then respawn. For an overnight run, keep the Mac awake with `caffeinate -i`.

## Not available outside Cursor

- Cloud agents, the Cursor dashboard, and Cursor restarts. Every worker is local, and local subagents do not survive a restart.
- A Cursor cloud-agent URL in Session pickup. Only its pushed branch or PR can be picked up.
- Unattended multi-day programs at the scale of hundreds of agents. Shrink the program to what one supervised machine can run.
- Cursor automations and webhooks (`make-bot-ui`, `benny`).
- Origin. Use it only when `command -v origin` succeeds, as the playbooks say. Otherwise `gh`.
- A built-in PR tool. An MCP server is not one. Where a playbook says to use the run's built-in PR tool, use the resolved forge.
- Bugbot is a GitHub app. It applies only where the repo has it installed.
- `scripts/watch-pr` and `scripts/orch` need `bun`. Their first run installs dependencies, which needs network. Without `bun`, read PR state with `gh pr view --json` and `gh pr checks`, and keep the orchestrate ledger as a TSV by hand.
