# pstack for Claude Code and Codex

This repository runs pstack in Claude Code and Codex. Upstream's skill text stays as written wherever possible. Each ported skill gets one line under its title that points to the `pstack-harness` skill, which maps Cursor's tools, model slugs, paths, and transcripts to both harnesses. Keeping the diff small keeps upstream merges cheap.

## Layout

| Path | What it is |
|---|---|
| `~/.local/share/pstack`, or wherever you cloned | The clone. The bootstrap puts it here and checks out the newest release. A maintainer's clone tracks `main` instead, with cursor/plugins added as a remote, which the merge script finds by URL. |
| `~/.local/bin/pstack` | Symlink to the `pstack` command, `port/pstack`. |
| `~/.config/pstack/state.json` | The harnesses you installed for, so `update` reinstalls the same ones. |
| `~/.agents/pstack` | Symlink to the clone's `pstack/`. Skills and agents find each other through it. |
| `~/.claude/skills/<name>`, `~/.codex/skills/<name>` | Symlinks to each ported skill. |
| `~/.claude/agents/` | Symlinks to `comment-sicko` and `poteto-agent`, plus the generated `pstack-effort-<level>` agents. |
| `~/.agents/pstack-models.md` | Model per role, written by `pstack configure` or `/setup-pstack`. |

Keep the clone outside `~/.agents/skills`. Codex scans that directory recursively and would load every pstack skill, ported or not.

## Install

Users install with the bootstrap one-liner in [`../README.md`](../README.md), which clones the repository and runs `pstack install`. The `pstack` command (`port/pstack`) is the interface for installing, configuring, checking, updating, and removing. It drives `port/install.sh`, which does the linking:

- It links the skills in `SKILLS` and `TEAM_KIT_SKILLS` into each harness in `PSTACK_HARNESSES`, and the agents into Claude Code. It removes the links from a harness left out.
- For every skill marked `disable-model-invocation: true`, it writes `agents/openai.yaml` with `allow_implicit_invocation: false`, because Codex ignores the frontmatter flag.
- It generates a `pstack-effort-<level>` agent for each effort a `claude:` entry in the models file asks for, since Claude Code's `Agent` tool cannot set effort per spawn.
- It takes over links that point into another pstack clone, or at a clone that was deleted, so moving the clone just works. It never overwrites anything else.

`pstack configure` holds the model logic: detection (`claude --help` and `codex debug models`), defaults per role, the budget, foreign seats, and your overrides. `/setup-pstack` asks the questions and calls it. `PSTACK_DETECT_JSON` fakes detection for tests.

In Codex, user-only skills appear in the `$` picker under the `pstack` namespace, for example `$pstack:poteto-mode`. Typing `$poteto-mode` as plain text in `codex exec` does not load the skill.

## Update from upstream

`main` takes changes only through pull requests, so merge upstream on a branch.

```sh
cd ~/.agents/src/pstack
git remote add upstream https://github.com/cursor/plugins.git   # once
git switch -c upstream-merge origin/main
pstack/port/merge-upstream.py
```

The script fetches the cursor/plugins remote, merges its `main` without committing, and drops every path outside the port, which upstream's changes to other plugins would otherwise bring back. It restores our copy of the two READMEs, `.github/dependabot.yml`, and `.github/SECURITY.md`, whatever upstream changed in them. It resolves every conflict where our only change to a file is the pointer line: it takes upstream's text and restores the pointer. It lists every other conflict for you. It also reports new upstream skills with the Cursor terms they use, ported skills that upstream removed, and Cursor terms that upstream newly added to skills already ported. Those are what need porting.

After resolving, run `install.sh` and then the smoke test. Then commit the merge, push the branch, and open a pull request. Merge it with a merge commit, because a squash would drop upstream's history from `main`. The title is `chore(upstream): merge cursor/plugins`, which passes the `pstack PR` check and is a patch release. Name the repository in `GH_REPO`, since the clone also has cursor/plugins as a remote.

```sh
git commit -m "chore(upstream): merge cursor/plugins"
git push -u origin upstream-merge
GH_REPO=<owner>/<repo> gh pr create --title "chore(upstream): merge cursor/plugins" --fill
GH_REPO=<owner>/<repo> gh pr merge --merge
```

Expect manual conflicts in the files the port rewrote or edited beyond the pointer: `setup-pstack`, `no-comments`, `poteto-mode` (frontmatter name), the `bug-fix` playbook, the three `reflect` reviewer prompts, `worktree-audit.sh`, and the two agent files.

## Port a new skill

1. Put the pointer line under its title. Copy it from any ported skill, including the words "in full". A skill that reads transcripts also names `transcripts.py`.
2. Map any Cursor term it uses that `pstack-harness` does not cover yet. Add the mapping to the harness, not to the skill.
3. Add it to `SKILLS` in `install.sh` and run `install.sh`.
4. Add a scenario to `smoke/smoke.py` if the skill spawns subagents, picks models, or reads transcripts.

## Smoke test

```sh
~/.agents/pstack/port/smoke/smoke.py                      # all ten scenarios, both harnesses
~/.agents/pstack/port/smoke/smoke.py --only how,reflect   # a subset
~/.agents/pstack/port/smoke/smoke.py --recheck --out DIR  # re-score kept runs without rerunning
```

Each scenario runs one skill headless against a throwaway repo with a planted stale-cache bug. The test then reads the transcripts of the parent, its subagents, and any foreign seat, and checks the port's plumbing and the skill's result. A full run takes 40 to 60 minutes at four parallel jobs and spends real tokens on Opus and `gpt-6-astra`. Passing runs are deleted along with their sessions. Failing runs stay in the output directory for inspection. Once you are done with them, delete their sessions and folders.

## CI

Four workflows run on GitHub.

- **`pstack-ci.yml`** runs on every push to `main`, every pull request, and on demand. It runs `port/check.py`, the unit tests in `port/tests/`, `shellcheck`, `poteto-mode`'s bun tests, and `port/tests/install_test.sh` on Ubuntu and macOS, including macOS's stock bash 3.2.
  - The `CI gate` job passes only when every one of those jobs succeeded. It is the one check `main` requires, as Repository settings explains. It runs even when a job fails, because GitHub counts a skipped required check as passing.
  - On a push to `main`, the `Release` job runs after the gate and cuts the release. See Releases.
- **`pstack-pr.yml`** runs when a pull request opens, reopens, gets a push, or has its title or body edited. Its one job, `Pull request title`, runs `port/release.py --check-pr` on the title, body, and number, which the workflow passes through `env` and never through the command line. It lists every problem the merge would cause: a title that is not Conventional Commits, a `Release-As` value that is not the next patch, minor, or major, and a `[skip ci]`-style marker that would stop the release. When the pull request passes, it prints the level the pull request asks for and the release it would join if merged now. It plans that release against the pull request's base commit, which the workflow passes as `--target`, so a `fix` that merges after an unreleased `feat` reads as a minor release. `main` requires it, next to `CI gate`.
- **`pstack-upstream-sync.yml`** runs daily and on demand. It runs `port/sync-upstream.sh`, which merges cursor/plugins with `merge-upstream.py`. When the script resolves everything, it opens or updates a pull request from the `upstream-sync` branch, labels it `needs-porting` if upstream added something to port, and starts CI on the branch for an early result. GitHub holds the pull request's own runs, because the Actions bot opened it, and leaves the dispatched run out of the pull request's checks. So a sync pull request can merge only after a person opens it and clicks **Approve workflows to run**, which starts its `CI gate` and `Pull request title` checks. When conflicts need a person, it opens or updates an issue labeled `upstream-sync` instead.
- **`pstack-settings.yml`** runs daily at 09:41 UTC, on demand, and on a pull request that changes `port/github-settings.py` or the workflow. On a pull request it smoke-tests the checker against the live repository, and it does not compare that pull request's own `github.json`, because nothing has applied it yet. Its one job, `Settings match github.json`, runs `port/github-settings.py --check` and fails when the live repository differs from `github.json`. The Actions token is not an admin, so it can compare only what GitHub shows to any reader. That is the description, `has_issues`, `has_wiki`, and `has_projects`, the topics, private vulnerability reporting, and the rulesets except their bypass actors. GitHub requires Administration read for the merge settings, everything under Actions, code scanning, and the other features. The job lists those as unreadable and does not count them as drift, so a change to one of them in the web UI shows up only when an admin runs `--check`, or when the optional token below is set.

To make the daily run see everything, create a fine-grained token scoped to this repository only, with read-only Administration and Metadata permissions, and store it as the `PSTACK_SETTINGS_TOKEN` repository secret. The workflow uses it in place of the Actions token. The token is optional, and without the secret the run falls back to the Actions token.

Every `uses:` line in the workflows names a full commit SHA, with the release in a comment. A tag can move, and a commit cannot. The repository requires this, and a test fails when a workflow has an action that is not pinned or, outside `actions/` and `github/`, is not in `github.json`'s allowed patterns.

Dependabot, set up in `.github/dependabot.yml`, opens one pull request a week that bumps the minor and patch updates of the pinned actions together, with their comments. The title reads `chore(deps): bump the actions group with N updates`, which asks for a patch release. It changes only `.github/`, so it cuts no release by itself. Each major version bump arrives as its own pull request, so a breaking change to an action gets its own review. The file has no entry for `poteto-mode`'s `bun.lock`, because that lockfile is upstream's and a bump would conflict with the next sync. Dependabot alerts still watch the lockfile, but no pull request follows them.

CodeQL default setup scans the workflows and the Python scripts. GitHub runs it, so no workflow file in this repository does, and `github.json` records its settings.

`check.py` fails when a skill folder is not installed, a skill's name does not match its folder, a user-only skill lacks its Codex policy file, a skill that uses Cursor terms lacks the pointer, `pstack-harness` stops mapping a Cursor term a skill uses, a relative reference or README link is broken, or the README catalog drifts from the installed skills. It warns about Cursor mentions that no known term covers.

Run the same checks locally:

```sh
python3 pstack/port/check.py
python3 -m unittest discover -s pstack/port/tests -p 'test_*.py'
pstack/port/tests/install_test.sh            # add /bin/bash to test macOS's stock bash
DRY_RUN=1 pstack/port/sync-upstream.sh       # from a clone whose origin is the fork; prints pushes and GitHub writes
```

The sync needs three repository settings: Actions enabled (GitHub disables them on new forks), "Allow GitHub Actions to create and approve pull requests" (`can_approve_pull_request_reviews` in `github.json`), and Issues enabled (also off on new forks, and set by `has_issues` in `github.json`).

## Releases

Merging to `main` is the release process. The `Release` job in `pstack-ci.yml` runs `port/release.py --publish` on the pushed commit once the `CI gate` job passes. It releases the commit unless the newest plain `vX.Y.Z` tag already covers it, and it creates the tag and the GitHub release in one API call. Release jobs queue in the `pstack-release` concurrency group and run one at a time, so overlapping merges never race for a tag, and a rerun of a covered commit does nothing.

The script reads the first-parent history of `main` since the newest tag. Each change asks for a level, and the release takes the highest. Upstream commits that a sync brings in sit on a second parent, so only the sync's own merge counts.

| A change since the last release | Level |
|---|---|
| Any merged pull request or direct commit, including a plain title or an upstream sync | Patch |
| A `feat` title | Minor |
| A breaking change, which is `!` after the type or a `BREAKING CHANGE:` line in the body | Minor before 1.0.0, major after |
| A `Release-As: X.Y.Z` line in the pull request body | The level of `X.Y.Z`, when it is the next patch, minor, or major |

`Release-As` sets the level of its own pull request and no other. One pull request can lower itself, so a `feat` with `Release-As: 0.4.3` asks only for a patch, but it never hides another pull request's `feat` or breaking change. `Release-As: 1.0.0` is the way from 0.x to 1.0.0, because the breaking-change rule stops at a minor before then. Any other value is ignored and that pull request keeps the level its title earns. The ignored value shows as a warning in the job summary and as an annotation on the run.

Titles follow Conventional Commits, and a title that does not is a patch. A `BREAKING CHANGE:` or `Release-As:` line inside a fenced code block is not read, so documenting one never triggers it. A tag cannot be moved or deleted once users have it, which is why `Release-As` accepts only the next version.

The job also looks at the files that changed since the newest tag. When every one is under `.github/`, or none changed, it cuts no release, because users run the clone and never `.github/`. That covers Dependabot pin bumps, workflow edits, and an upstream sync that brings nothing into the port. Those merges stay in the range, so the next release counts them in its level and lists them in its notes. The job prints `nothing to release` with the number of changes it holds, and the `pstack PR` check says the pull request would not release.

The notes list the pull request titles under Breaking changes, Features, Fixes, and Other changes, so write each title as the line users read. The `pstack PR` check runs the same parser on a pull request's title and body before merge, so most mistakes show while the pull request is open.

Preview a release with `pstack/port/release.py` on an up-to-date `main`. It prints the decision and the notes and publishes nothing. Add `--target <ref>` for another commit.

```sh
git fetch --tags
pstack/port/release.py                  # the release for HEAD, with its notes
pstack/port/release.py --target <ref>   # the release for another commit
PR_TITLE='feat: add a flag' PR_BODY='' PR_NUMBER=4 pstack/port/release.py --check-pr   # what a pull request would release after HEAD
PR_TITLE='feat: add a flag' PR_BODY='' PR_NUMBER=4 pstack/port/release.py --check-pr --target <base>   # after another base commit
```

On a feature branch the preview lists the branch's own commits, not the one merge commit that the pull request makes, so its version and notes can differ from the real release. Publishing needs `--publish`, which only the release job passes.

A pull request whose title or body contains `[skip ci]`, `[ci skip]`, `[no ci]`, `[skip actions]`, `[actions skip]`, or a line `skip-checks: true` skips CI on its merge commit, so no release runs for it. That change ships with the next merge, and the `pstack PR` check fails the pull request first.

Never push `v*` tags by hand. The release job skips a tag whose name is not plain `vX.Y.Z` and warns about it, and `pstack update` and the bootstrap ignore it.

The bootstrap checks out the newest plain `vX.Y.Z` tag, and `pstack update` moves such a checkout to newer tags only. Every merge to `main` that cuts a release therefore reaches users on their next update, including a merged upstream sync. A clone that tracks a branch, like a maintainer's, follows its branch instead.

## Repository settings

`port/github.json` records the repository settings, rulesets, and security features that releases and upstream syncs depend on. `port/github-settings.py` applies them with `gh` and checks the live repository against them. It needs Python 3.9 or newer and nothing else.

```sh
pstack/port/github-settings.py --repo <owner>/<repo>               # apply them
pstack/port/github-settings.py --repo <owner>/<repo> --dry-run     # print each write instead of making it
pstack/port/github-settings.py --repo <owner>/<repo> --check       # compare with the live repository and write nothing
pstack/port/github-settings.py --repo <owner>/<repo> --config <path>   # read another settings file
```

`--repo` defaults to `$GITHUB_REPOSITORY` and is required, so the script never guesses which repository to change. Apply as an account that administers the repository. The script applies the repository settings, then each endpoint, then each feature, then each ruleset. It finds each ruleset by name and updates it in place, so running it again changes nothing. To change a setting, merge the change to `github.json` first, then apply it as an admin, then run `--check` as an admin. The script refuses to require pinned actions while the default branch still has a `uses:` line that is not a full commit SHA. It names each file and line and writes nothing, so merge the pins before you apply.

`--check` prints a `drift:` line for each difference and a summary line, and exits 1 when anything differs. A list of plain values, such as topics, compares as a set. A ruleset's rule parameters need only be a subset of the live ones, because GitHub adds defaults. GitHub hides some fields and endpoints from a token that is not admin, so for that token a 403, a 404, or a key missing from the response is unreadable and not drift. For an admin the same answers are drift, which catches a misspelled key or path. A 429 or a 5xx stops the check with exit code 2, because an outage says nothing about the repository. A live ruleset that `github.json` does not name is drift too. Run locally as an admin, `--check` sees everything. The daily `pstack settings` run sees only what its token can read and reports the rest in a notice.

`github.json` covers these.

- **Merge commits only.** A squashed or rebased upstream sync would drop cursor/plugins from the history of `main`, and every later sync would conflict on every file. For the same reason, `main` must not require linear history. The merge commit takes the pull request title and body, which is where `release.py` reads the title and any `Release-As:` or `BREAKING CHANGE:` line. Merge pull requests with `gh pr merge --merge`. The Shipping playbook of `poteto-mode` says `--squash`, which this repository rejects.
- **Required `CI gate` and `Pull request title` checks.** The `main` ruleset requires both from GitHub Actions, blocks direct pushes, force pushes, and deletion, and requires every review thread to be resolved. It requires no approvals, because one maintainer cannot approve their own pull request. No ruleset has bypass actors, but an admin can still disable a ruleset.
- **`v*` tags cannot move or be deleted.** Users' clones already hold them. The ruleset leaves creation open, since the `Release` job creates the tags. `pstack update` and the bootstrap ignore names that are not plain `vX.Y.Z`, so a stray tag never reaches a clone as a release. A plain tag that users already fetched stays in their clones even after you delete it.
- **Branches are deleted after a merge**, and a pull request offers to merge `main` into its branch. That is how an open sync pull request picks up a new `CI gate` job.
- **No wiki and no projects.** Documentation lives in this repository, where a pull request reviews it, and a wiki would be a second copy that no pull request reviews.
- **A description and topics.** They are how people find the repository, so the file holds them and a change in the web UI shows up as drift.
- **Only selected actions, pinned by SHA.** GitHub-owned actions and `oven-sh/setup-bun@*` may run, and a workflow whose `uses:` line is not a full commit SHA is rejected. A moved tag would otherwise run new code in a job that holds a token. A new third-party action needs a pattern in `patterns_allowed` first.
- **A read-only workflow token by default.** A job gets write access only where its workflow asks, as the `Release` job does for contents. Creating and approving pull requests stays allowed, because the upstream sync opens its pull request with that token.
- **Approval for every outside contributor.** A workflow run from a fork waits for a maintainer, so an outsider cannot run code in CI uninvited.
- **CodeQL default setup for `actions` and `python`.** It scans the workflows for script injection and the Python scripts for security flaws.
- **Dependabot alerts on, security updates off.** Alerts watch `bun.lock`. A security update would open a pull request that edits upstream's lockfile and conflicts with the next sync.
- **Private vulnerability reporting on.** The **Report a vulnerability** button that `.github/SECURITY.md` points to needs it.
- **Immutable releases on.** A published release keeps its tag and assets, which `pstack update` relies on, and a deleted release's tag name cannot be reused. Never delete a published release. If one goes out wrong, fix forward with the next merge. If a release is deleted anyway, keep its tag, so `release.py` keeps counting from it.

To remove a tag pushed by mistake, an admin disables the `release tags` ruleset in the repository settings, deletes the tag, and enables the ruleset again. Immutable releases do not get in the way, because a tag pushed by hand has no release. `release.py` fails when the newest plain tag is not in the history of the commit it releases, and its error points here.

`merge-upstream.py` deletes every path that its `KEEP` pattern does not match. `KEEP` covers `pstack/`, `README.md`, `.gitignore`, the team kit's license and three of its skills, `.github/workflows/pstack-*.yml`, `.github/dependabot.yml`, and `.github/SECURITY.md`. When upstream changes `README.md`, `pstack/README.md`, `.github/dependabot.yml`, or `.github/SECURITY.md`, the sync keeps our copy. A file anywhere else, such as `.github/CODEOWNERS`, disappears with the next upstream sync unless you widen `KEEP`.

## Uninstall

`pstack uninstall` removes every link and generated agent the install made, `~/.agents/pstack`, the `pstack` command, and the saved state. `--purge` also deletes the models file, and the clone if it is the bootstrap's `~/.local/share/pstack`. Claude Code settings you added stay as they are.

## Behavior to know

- **Foreign seats.** Panels (`arena`, `architect`, `interrogate`) and `reflect`'s tooling lens seat the other harness through its CLI. In Codex, `claude` runs outside the sandbox, so Codex asks for escalation. With `approvals_reviewer = "auto_review"`, the reviewer may deny a seat that would send a session transcript to the other vendor. The seat is then reported as blocked and the run finishes without it. To keep code and transcripts inside the harness you run, put `foreign seats: off` in `~/.agents/pstack-models.md`, or answer `off` in `/setup-pstack`. Every role then runs natively, and each panel seats two native models so it still gets two reviewers.
- **Claude Code prompts** before writing under `.claude/`, for example when `create-verification-skill` links `.claude/skills/verify-<app>`.
- **Read permissions.** `pstack install --claude-read-rules` adds allow rules for `Read(~/.agents/pstack/**)`, `Read(<your clone>/**)`, and `Read(~/.claude/skills/**)` to `~/.claude/settings.json`, so Claude Code stops asking before it reads skill files. An allow rule for a symlinked path must match both the link and its target.
- **Not ported:** `make-bot-ui` and the `benny` automation pack depend on Cursor automations. `automate-me` and the verification skills are ported but interactive, so the smoke test does not cover `automate-me` or `maintain-verification-skill`.
