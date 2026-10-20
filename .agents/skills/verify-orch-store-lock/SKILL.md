---
name: verify-orch-store-lock
description: "Model-check the orch store lock with TLA+: at most one live orch process writes the store at a time. Use when a change touches acquireLock, its create or takeOver steps, the lock release, holderIsDead, or how openStore takes and drops the lock in pstack/skills/poteto-mode/scripts/orch/store.ts, or to ask whether two orch processes can both hold .orch.lock."
---

# Verify the orch store lock

Every orch command that writes the store first takes `<store>/.orch.lock`. The promise: **at most one live orch process writes the store at a time.** If two do, they write `units.tsv`, `ledger.tsv`, `gates.md`, `frontier.json`, and the inbox drain over each other, and the orchestrator loses or corrupts plan state.

`StoreLock.tla` models every way three orch processes can interleave the lock's filesystem calls, and TLC checks the promise, `MutualExclusion`, in all of them.

## Run it

```
.agents/skills/verify-orch-store-lock/check.sh
```

It needs Java 11 or newer, and `curl` the first time, when it downloads `tla2tools.jar` v1.7.4 to `~/.cache/tla2tools/` and checks its SHA-256. Set `TLA2TOOLS_JAR` to use a copy you already have. Both configurations take under 10 seconds together. TLC runs one worker, so a counterexample is the shortest one and the same on every run. It exits 1 when a configuration finds a counterexample, and prints it.

## Expected result at the sync commit

- `StoreLockNoCrash.cfg`, three processes that never die: passes, 739 distinct states.
- `StoreLock.cfg`, three processes, one of which may die at any step: fails `MutualExclusion` with the known finding below, in 16 states.

Anything else is new and needs a look: a different trace in `StoreLock.cfg`, any failure in `StoreLockNoCrash.cfg`, or a `TypeOK` failure.

### Known finding: two processes take over the same stale lock

Reported upstream as cursor/plugins#730, with a fix proposed in cursor/plugins#733.

The trace names the processes `p1`, `p2`, and `p3`:

1. p1 takes the lock and dies without running release (states 2 to 4), so `.orch.lock` holds p1's dead pid.
2. p2 calls `acquireLock`: `open(path, "wx")` fails with EEXIST, `readFile` returns `p1`, and `holderIsDead("p1")` is true, so p2 goes to `takeOver` (states 5 to 7).
3. p3 gets EEXIST and also reads `p1`, before p2 changes anything (states 8 and 9).
4. p2 unlinks the stale lock, creates its own, writes its pid, and starts writing the store (states 10 to 12).
5. p3's `holderIsDead("p1")` is still true, because it checks the pid it read in step 3, not the file at the path now. p3's `unlink(path)` deletes p2's live lock (states 13 and 14).
6. p3 creates its own lock and also starts writing the store. p2 and p3 both hold the lock (states 15 and 16).

## Coverage

Sync commit: `a2bc39e1abb071f54425d68da8cbae5e4fb8fd61`. The model was last confirmed to describe the covered code at this commit.

Covered code, all in `pstack/skills/poteto-mode/scripts/orch/store.ts`:

- `LOCK_FILE`, `holderIsDead`, and `acquireLock` with its `create` and `takeOver` closures and the release closure it returns.
- In `openStore`: `ensureLock`, which shares one `lockRequest` per process, so one process acquires once, and `close`, which runs the release.

To see whether a change touches it:

```
git diff a2bc39e1abb071f54425d68da8cbae5e4fb8fd61 -- pstack/skills/poteto-mode/scripts/orch/store.ts
rg -n 'LOCK_FILE|orch\.lock|acquireLock|holderIsDead' pstack/skills/poteto-mode/scripts
```

Read every hunk in the functions above. The `rg` finds new code that reads or writes the lock somewhere else, which is covered code too.

## How the model maps to the code

Each action is one filesystem call or one `process.kill`, so TLC interleaves processes exactly where the kernel can.

| Action in `StoreLock.tla` | Code in `store.ts` |
| --- | --- |
| `Open` | `acquireLock`'s first `create()`: `open(path, "wx")`. EEXIST goes to `ReadHolder`. |
| `Write` | `create()`: `handle.writeFile(pid)` and `handle.close()`. The write goes to the inode this process opened, which may no longer be at the path. |
| `ReadHolder` | `readFile(path)`, trimmed. Empty or unreadable is `"unknown"`. |
| `CheckHolder` | `holderIsDead(holder)`: `process.kill(pid, 0)` fails with ESRCH. Otherwise acquireLock throws `store lock held by pid`. |
| `Unlink` | `takeOver()`: `unlink(path)`. ENOENT throws. |
| `ReOpen` | `takeOver()`: `create()` again. EEXIST throws `store lock held by pid`. |
| `Work` | `acquireLock` has returned. The process writes the store until `close()`. |
| `ReleaseRead`, `ReleaseUnlink` | The release closure: `readFile(path)` equals its pid, then `unlink(path)`. ENOENT is ignored. |
| `Crash` | The process dies between two calls and never runs release. |

## Tell a bug from a modeling error

Replay each step of a counterexample against `store.ts`. If a step cannot happen in the code, the model is wrong: fix the model, and say what was wrong. If every step can happen, it is a bug. Report it as steps in the code's words, as in the known finding. Never weaken `MutualExclusion` or the model to make the check pass. Change product code only when the user asks.

## When the covered code changes

1. Change the actions to match the new code. Keep one filesystem call as one action. A new file in the protocol, such as the `.orch.lock.takeover` file that #733 proposes, gets its own variables and actions.
2. Run `check.sh`.
3. If it passes, show that it can still fail. Break the new code the way the promise guards against, such as `"wx"` changed to `"w"` in `create()`, so that two processes can create the lock. Mirror the break in the model, confirm that `check.sh` fails, then drop the break in both.
4. Update this file: the covered code, the expected result, and the known finding. When the stale takeover is fixed, `StoreLock.cfg` should pass, so remove the known finding.
5. Move the sync commit to the commit you checked, and only after you have confirmed that the model describes the code at that commit.

## What this does not cover

- `force`, which `orch` passes to `openStore` to take over a live holder's lock on purpose. It breaks the promise by design and is not modeled.
- More than three processes or more than one crash. One crash and three processes are enough for the known finding.
- Pid reuse. The model assumes a dead pid is never handed to a new process during a run.
- Filesystems where `open(path, "wx")` is not an atomic exclusive create, such as some network filesystems.
- What the store does while it holds the lock, and concurrency inside one process beyond `ensureLock`'s shared `lockRequest`.
- Whether a stale lock is always recovered. A process that dies between `open` and `writeFile` leaves an empty lock. Every later process reads it as `"unknown"`, which `holderIsDead` never calls dead, so no process takes it over without `force`.

## Download pin

`check.sh` pins `tla2tools.jar` from the official v1.7.4 release by its SHA-256. GitHub publishes no digest for assets that old, so the hash was recorded from a download of that release URL whose size matched the release asset, 2274532 bytes.
