# pstack for Codex

[pstack](https://github.com/cursor/plugins/tree/main/pstack) is Lauren Tan's (poteto) set of engineering skills for Cursor: rigorous playbooks, parallel design and review, verification, and a principles library. Upstream also ships it as a Claude Code plugin. This fork runs it in Codex, where upstream has no install, and lets Codex panels seat Claude models next to OpenAI ones.

The skill text is upstream's. Each ported skill carries one extra line that points the agent to `pstack-harness`, a skill that maps Cursor's tools, model names, paths, and transcripts to Codex, and to Claude Code for a Claude seat.

## Requirements

- Codex.
- `git`, `python3`, and `gh`.
- Optional: Claude Code, logged in, if you want Codex panels to seat Claude models. See [Using Claude Code and Codex together](#using-claude-code-and-codex-together).
- Optional: `bun` for the Babysit and Orchestrate helper scripts, and `tmux` for `control-cli`.

## Set up

1. Install:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/mstephenholl/pstack-claude-codex/main/pstack/port/bootstrap.sh | bash
   ```

   This clones pstack to `~/.local/share/pstack`, checks out the newest release, puts the `pstack` command in `~/.local/bin`, and runs `pstack install`. That links each skill into `~/.codex/skills` and points `~/.agents/pstack` at the clone. It installs for Codex even when Claude Code is also on your PATH, because Claude Code gets pstack from upstream's plugin. It never overwrites a file it did not create.

2. Choose models with `pstack configure`. It detects the models Codex offers, asks for a reasoning budget and whether to allow foreign seats (see [Models](#models)), and writes `~/.agents/pstack-models.md`. In a Codex session, `$setup-pstack` walks you through the same choices. For scripts, pass `--budget`, `--foreign-seats`, and `--set "ROLE=ENTRY"` with `--yes`.

3. Check the result with `pstack doctor`. It prints each problem with the command that fixes it.

To install from a clone you manage yourself, clone the repository anywhere outside `~/.agents/skills`, which Codex scans recursively, and run `pstack/port/pstack install` in it.

| Command | What it does |
|---|---|
| `pstack install [--codex] [--claude] [--claude-read-rules]` | Links the skills, and puts `pstack` on your PATH. It remembers which harnesses you chose. With no flags and no saved choice, it installs for Codex. `--claude` installs this fork into Claude Code as well, which most people should not need. See [Installing this fork into Claude Code](#installing-this-fork-into-claude-code). |
| `pstack configure` | Chooses models, the reasoning budget, and foreign seats. `--dry-run --json` previews without writing. |
| `pstack doctor` | Checks links, the models file against the models you have, and optional tools. |
| `pstack update [--head]` | Moves an install to the newest release, or fast-forwards a clone that tracks a branch, then reinstalls and runs `doctor`. `--head` switches a release install to the branch tip. |
| `pstack uninstall [--purge]` | Removes every link and generated agent, and the `pstack` command. `--purge` also deletes the models file, and the clone if the bootstrap created it. |
| `pstack status` | Shows the version, clone, harnesses, and models file. |

## Use

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
| | `recall` | Catching up on recent work from your Codex and Claude Code sessions. |
| | `blast-radius` | What a change could break outside the diff. |
| | `bro` | The last message again, in plain language. |
| | `ketchup` | What changed in the chat since your last message, briefly, with pictures where they help and each decision you need to make. |
| | `ketchup-video` | The same catch-up as a short silent video of slides, with each decision you need to make as text under it. |
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
| | `create-formal-verification-skill` | A project skill that formally verifies one property where a bug costs the most and tests reach the least, such as a race, a retry, or a state machine. |
| | `maintain-formal-verification-skill` | Keeping that skill true to the code after the code changes, and telling drift in the model from a bug in the code. |
| | `statechart` | A Mermaid chart of what the user sees, generated from a TLA+ model, with each bug TLC finds drawn as a numbered path, or a TLA+ skeleton from a chart a PM agreed to. |
| | `control-ui`, `control-cli` | Driving a browser or Electron UI, or a CLI or TUI, to capture evidence. |
| | `show-me-your-work` | A decision log for long or unattended runs. |
| | `benchmark-checklist` | Vetting a performance number before you report or act on it. |
| Improve your setup | `reflect` | Turning lessons from a session into skill edits. |
| | `automate-me` | A personal `<name>-mode` skill drafted from how you work. |
| | `correct` | Making the mistakes agents keep repeating in your repo impossible, starting with architecture and types. |
| | `setup-pstack` | Models and reasoning budget. |
| Principles | `principle-*` | 24 engineering principles that `poteto-mode` reads as needed. You can also invoke them directly. |

## Models

`~/.agents/pstack-models.md` holds one line per role, and each entry names a harness and a model, as in `codex:gpt-6-astra@xhigh` or `claude:opus@xhigh`. Coding roles default to `gpt-6.1-sol`, and judgment and writing roles default to `gpt-6-astra`. Codex runs each role on its own `codex:` entry.

The panels in `interrogate`, `arena`, and `architect` seat one reviewer per entry. With `foreign seats: on` and Claude Code installed, `pstack configure` adds a Claude entry to each role, Opus for judgment and Sonnet for code, and a panel seats it by running `claude -p`. That sends the diff, the task, or the session transcript to Anthropic. With `foreign seats: off`, everything stays in Codex, and each panel seats two Codex models instead. Change it with `pstack configure --foreign-seats on|off` or `$setup-pstack`.

Each entry's effort is where a subagent starts. Skills raise or lower it to fit the job, but keep the entry's model.

## Using Claude Code and Codex together

Run pstack from two sources, one per harness:

| | Claude Code | Codex |
|---|---|---|
| **Install** | `/plugin marketplace add cursor/plugins`, then `/plugin install pstack@cursor-plugins` | The bootstrap one-liner in [Set up](#set-up) |
| **Run a skill** | `/pstack:poteto-mode` | `$pstack:poteto-mode` |
| **Models** | `/pstack:setup-pstack` writes `~/.claude/rules/pstack-models.md` | `pstack configure` writes `~/.agents/pstack-models.md` |
| **Update** | Update the plugin through `/plugin` | `pstack update` |
| **Maintained by** | Upstream, the same day a skill changes | This fork, through a daily sync with upstream |

The two installs do not share anything, so set them up in either order. The bootstrap installs this fork for Codex only, even with Claude Code on your PATH, so you never get two copies of the skills in Claude Code.

Some things work differently across the two:

- **Mixed panels run from Codex.** Turn on foreign seats in `pstack configure`, and Codex panels seat Claude through `claude -p` next to OpenAI models. Codex asks for escalation the first time, because `claude` runs outside its sandbox. Upstream's Claude Code plugin runs any non-Claude role on the session's own model, so a panel in Claude Code seats only Claude models.
- **Two models files.** Each harness reads its own, so a change in one does not reach the other. Rerun the setup in the harness you want to change.
- **Session history.** In Codex, `recall`, `reflect`, `automate-me`, and `show-me-your-work` read both your Codex and Claude Code sessions through `port/transcripts.py`. In Claude Code, upstream's plugin reads only that workspace's Claude Code sessions.
- **Team-kit skills.** This fork ships `deslop`, `control-ui`, and `control-cli` for Codex. Upstream's Claude Code plugin does not include them, though some `poteto-mode` playbooks call them.
- **Skill names.** Upstream's plugin keeps upstream's `tdd` and `teach`, as `/pstack:tdd` and `/pstack:teach`. In Codex they are `pstack-tdd` and `pstack-teach`.

### Moving from this fork's Claude Code install

Earlier releases of this fork also installed into Claude Code. To switch Claude Code to upstream's plugin:

1. In Claude Code, run `/plugin marketplace add cursor/plugins` and `/plugin install pstack@cursor-plugins`, then `/pstack:setup-pstack`.
2. Run `pstack install --codex`, then `pstack configure`. The install removes this fork's links and agents from Claude Code and keeps Codex. Your models file still names only Claude models, so until `configure` adds Codex's defaults next to them, every role in Codex runs on the session's model, and `pstack doctor` warns. If you do not use Codex, run `pstack uninstall` instead.
3. If you added read rules with `--claude-read-rules`, remove them from `~/.claude/settings.json` by hand. Nothing else uses them.

`pstack doctor` warns while this fork is still installed in Claude Code. Once `settings.json` enables `pstack@cursor-plugins`, it stops telling you to install the plugin.

### Installing this fork into Claude Code

`pstack install --claude` still links this fork's skills and agents into Claude Code, with unprefixed names such as `/poteto-mode`. Use it only without upstream's plugin, because the two together list every skill twice. With it, `pstack install --claude-read-rules` lets Claude Code read pstack's files without asking.

## Differences from Cursor's pstack

- `tdd` and `teach` are renamed `pstack-tdd` and `pstack-teach` to avoid clashing with other skills of the same name.
- There are no cloud agents. Every worker runs locally, and a worker that writes gets its own git worktree. The Orchestrate and Autopilot playbooks run on one supervised machine.
- Subagents run at standard speed. Codex has no fast mode per subagent, so skills that pick a subagent's reasoning level and speed change only its effort.
- `recall`, `reflect`, `automate-me`, and `show-me-your-work` read Codex and Claude Code session logs through `port/transcripts.py`.
- The `comment-sicko` and `poteto-agent` agents are Claude Code agents. Codex starts a default agent with their instructions.
- Not ported: `make-bot-ui` and the `benny` automations need Cursor automations. `poteto-help` and Cursor's setup guide describe Cursor's install, modes, and UI, so they are not included. Use `pstack doctor` and this README instead.

## Update and uninstall

`pstack update` moves to the newest release, or fast-forwards a clone that tracks a branch, and reinstalls. `pstack uninstall` removes everything the install set up, and `--purge` also removes your models file and the managed clone. The [releases page](https://github.com/mstephenholl/pstack-claude-codex/releases) lists them.

Maintainers pull upstream pstack changes with `pstack/port/merge-upstream.py`, which a daily workflow also runs. [`port/README.md`](port/README.md) covers that, porting a new skill, CI, releases, and the smoke test.

## License

MIT. pstack is © 2026 Lauren Tan ([`LICENSE`](LICENSE)). `deslop`, `control-ui`, and `control-cli` are © 2026 Cursor ([`cursor-team-kit/LICENSE`](../cursor-team-kit/LICENSE)).
