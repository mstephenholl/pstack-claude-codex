# pstack for Claude Code and Codex

[pstack](https://github.com/cursor/plugins/tree/main/pstack) is Lauren Tan's (poteto) set of engineering skills for Cursor: rigorous playbooks, parallel design and review, verification, and a principles library. This fork runs them in Claude Code and Codex.

The skill text is upstream's. Each ported skill carries one extra line that points the agent to `pstack-harness`, a skill that maps Cursor's tools, model names, paths, and transcripts to Claude Code and Codex.

## Requirements

- Claude Code, Codex, or both. You only need both if you want a panel to mix Claude and OpenAI models.
- `git`, `python3`, and `gh`.
- Optional: `bun` for the Babysit and Orchestrate helper scripts, and `tmux` for `control-cli`.

## Set up

1. Install:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/mstephenholl/pstack-claude-codex/main/pstack/port/bootstrap.sh | bash
   ```

   This clones pstack to `~/.local/share/pstack`, checks out the newest release, puts the `pstack` command in `~/.local/bin`, and runs `pstack install`. That links each skill into `~/.claude/skills` and `~/.codex/skills`, links the two pstack agents into `~/.claude/agents`, and points `~/.agents/pstack` at the clone. It never overwrites a file it did not create. To install for one harness, end the command with `bash -s -- --claude` or `bash -s -- --codex`.

2. Choose models with `pstack configure`. It detects the models each harness offers, asks for a reasoning budget and whether to allow foreign seats (see [Models](#models)), and writes `~/.agents/pstack-models.md`. In an agent session, `/setup-pstack` in Claude Code or `$setup-pstack` in Codex walks you through the same choices. For scripts, pass `--budget`, `--foreign-seats`, and `--set "ROLE=ENTRY"` with `--yes`.

3. Check the result with `pstack doctor`. It prints each problem with the command that fixes it.

4. Optional, Claude Code only: `pstack install --claude-read-rules` adds read rules to `~/.claude/settings.json`, so Claude Code reads pstack's files without asking.

To install from a clone you manage yourself, clone the repository anywhere outside `~/.agents/skills`, which Codex scans recursively, and run `pstack/port/pstack install` in it.

| Command | What it does |
|---|---|
| `pstack install [--claude] [--codex] [--claude-read-rules]` | Links the skills and agents, and puts `pstack` on your PATH. It remembers which harnesses you chose. With no flags and no saved choice, it installs for the harnesses whose CLI is on your PATH. |
| `pstack configure` | Chooses models, the reasoning budget, and foreign seats. `--dry-run --json` previews without writing. |
| `pstack doctor` | Checks links, agents, the models file against the models you have, and optional tools. |
| `pstack update [--head]` | Moves an install to the newest release, or fast-forwards a clone that tracks a branch, then reinstalls and runs `doctor`. `--head` switches a release install to the branch tip. |
| `pstack uninstall [--purge]` | Removes every link and generated agent, and the `pstack` command. `--purge` also deletes the models file, and the clone if the bootstrap created it. |
| `pstack status` | Shows the version, clone, harnesses, and models file. |

## Use

In Claude Code, type the skill as a command: `/poteto-mode fix the stale cache in cache.py`.

In Codex, type `$` and pick the skill. pstack's skills appear under the `pstack` namespace, as in `$pstack:poteto-mode`. Typing `$poteto-mode` as plain text does not load it. In `codex exec`, write "read ~/.agents/pstack/skills/poteto-mode/SKILL.md and follow it" instead.

Most skills run only when you invoke them, so they stay out of the model's context until then. `setup-pstack`, `deslop`, `control-ui`, and `control-cli` can also trigger on their own.

For anything that needs rigor, start with `poteto-mode`. It matches the task to a playbook (bug fix, feature, refactoring, perf, investigation, babysit, shipping, and others) and runs the other skills when a step calls for them.

| Group | Skill | Use it for |
|---|---|---|
| Start here | `poteto-mode` | Rigorous work of any kind. It picks the playbook. |
| | `figure-it-out` | Large or unusual work no playbook fits. It designs an auditable plan first. |
| Understand | `how` | How code works, and where something should live. |
| | `why` | Why code is the way it is, from git history and whatever MCP sources you have. |
| | `pstack-teach` | A plain explanation built from `how` and `why`. |
| | `recall` | Catching up on recent work from your Claude Code and Codex sessions. |
| | `blast-radius` | What a change could break outside the diff. |
| | `bro` | The last message again, in plain language. |
| | `ketchup` | What changed in the chat since your last message, briefly, with pictures where they help and each decision you need to make. |
| | `in-your-own-words` | Your goal and the problem played back before the agent starts. |
| | `sharpen` | The task sharpened before the agent starts: how an expert would approach it, what you are really after, and how much to specify. |
| Design and review | `architect` | Types and module shape before code, from competing sketches. |
| | `arena` | Several candidates for the same task, merged into the best one. |
| | `interrogate` | Adversarial review of a diff by a panel of models. |
| | `swarm` | Parallel workers for coverage, races, or exploration. |
| Build and clean up | `pstack-tdd` | A failing test first, then the fix. |
| | `no-comments` | Removing narration comments. |
| | `deslop` | Removing AI-generated code slop. |
| | `unslop` | Removing AI tells from prose. |
| | `technical-writing` | Docs, READMEs, PR descriptions, and commit messages. |
| | `typescript-best-practices` | TypeScript conventions. |
| Verify | `create-verification-skill` | A project skill that drives your app the way a user does. |
| | `maintain-verification-skill` | Keeping that skill accurate as the app changes. |
| | `control-ui`, `control-cli` | Driving a browser or Electron UI, or a CLI or TUI, to capture evidence. |
| | `show-me-your-work` | A decision log for long or unattended runs. |
| | `benchmark-checklist` | Vetting a performance number before you report or act on it. |
| Improve your setup | `reflect` | Turning lessons from a session into skill edits. |
| | `automate-me` | A personal `<name>-mode` skill drafted from how you work. |
| | `correct` | Making the mistakes agents keep repeating in your repo impossible, starting with architecture and types. |
| | `setup-pstack` | Models and reasoning budget. |
| Principles | `principle-*` | 24 engineering principles that `poteto-mode` reads as needed. You can also invoke them directly. |

## Models

`~/.agents/pstack-models.md` holds one line per role, and each entry names a harness and a model, as in `claude:opus@xhigh` or `codex:gpt-6-astra@xhigh`. Coding roles default to Sonnet and `gpt-6.1-sol`. Judgment and writing roles default to Opus and `gpt-6-astra`. Each harness uses its own entry.

The panels in `interrogate`, `arena`, and `architect` seat one reviewer per entry. With `foreign seats: on`, a panel can seat the other vendor's model by running that harness's CLI, which sends the diff, the task, or the session transcript to that vendor. With `foreign seats: off`, everything stays in the harness you run, and each panel seats two of that harness's models instead. Change it with `/setup-pstack`.

Each entry's effort is where a subagent starts. Skills raise or lower it to fit the job, but keep the entry's model. Claude Code's `Agent` tool cannot set reasoning effort per spawn, so `install.sh` generates a `pstack-effort-<level>` agent for every effort.

## Differences from Cursor's pstack

- `tdd` and `teach` are renamed `pstack-tdd` and `pstack-teach` to avoid clashing with other skills of the same name.
- There are no cloud agents. Every worker runs locally, and a worker that writes gets its own git worktree. The Orchestrate and Autopilot playbooks run on one supervised machine.
- Subagents run at standard speed. Neither harness has fast mode per subagent, so skills that pick a subagent's reasoning level and speed change only its effort.
- `recall`, `reflect`, `automate-me`, and `show-me-your-work` read Claude Code and Codex session logs through `port/transcripts.py`.
- The `comment-sicko` and `poteto-agent` agents are Claude Code agents. Codex starts a default agent with their instructions.
- Not ported: `make-bot-ui` and the `benny` automations need Cursor automations. `poteto-help` and Cursor's setup guide describe Cursor's install, modes, and UI, so they are not included. Use `pstack doctor` and this README instead.

## Update and uninstall

`pstack update` moves to the newest release, or fast-forwards a clone that tracks a branch, and reinstalls. `pstack uninstall` removes everything the install set up, and `--purge` also removes your models file and the managed clone. The [releases page](https://github.com/mstephenholl/pstack-claude-codex/releases) lists them.

Maintainers pull upstream pstack changes with `pstack/port/merge-upstream.py`, which a daily workflow also runs. [`port/README.md`](port/README.md) covers that, porting a new skill, CI, releases, and the smoke test.

## License

MIT. pstack is © 2026 Lauren Tan ([`LICENSE`](LICENSE)). `deslop`, `control-ui`, and `control-cli` are © 2026 Cursor ([`cursor-team-kit/LICENSE`](../cursor-team-kit/LICENSE)).
