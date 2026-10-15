---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget, across Claude Code and Codex. Detects your available models and writes ~/.agents/pstack-models.md, which overrides the skill defaults. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Configure pstack's models through the `pstack` command, which detects models, validates choices, and writes `~/.agents/pstack-models.md`. Run it as `~/.agents/pstack/port/pstack`, since it may not be on your PATH. First read `~/.agents/pstack/skills/pstack-harness/SKILL.md` for the entry syntax, how each role resolves, and the question tool for your harness.

## Steps

### 1. Load the current state

Run `~/.agents/pstack/port/pstack configure --dry-run --json --yes`. It reports the models each harness offers (`detected`), the current `budget` and `foreign_seats`, every role's entries, roles whose model is unavailable (`needs_choice`), roles the user changed from the defaults (`kept_overrides`), and retired lines it will drop (`dropped`). It writes nothing.

### 2. Ask for a budget and foreign seats

Use your harness's question tool, and name the current value of each.

- Budget, with these labels: `unlimited — max reasoning`, `large — xhigh reasoning`, `medium — high reasoning`, `small — medium reasoning`. They map to `--budget unlimited|large|medium|small`.
- Foreign seats. A foreign seat runs a role on the other harness through its CLI, which sends code or the session transcript to that vendor. Offer `on — panels and reflect's tooling lens mix Claude and OpenAI models` and `off — everything stays in the harness you run it in`. With `off`, each panel seats two native models so it still gets two reviewers.

### 3. Preview and confirm

Run `~/.agents/pstack/port/pstack configure --budget <budget> --foreign-seats <on|off> --dry-run --json --yes`. Show every role with its entries, each `needs_choice` item, each kept override, and each dropped line. Ask whether to accept as-is or change specific roles, offering the `detected` models plus `inherit-parent`. Panel roles (`arena runners`, `architect runners`, `interrogate reviewers`) run one seat per entry, so the list length sets the count.

Add one `--set "<role>=<entry>, <entry>"` per change, for example `--set "swarm workers=claude:haiku, codex:gpt-6-luna"`. Rerun the preview until nothing needs a choice. Never pass `--force` unless the user asks for a model the detection missed.

### 4. Write

Run the same command without `--dry-run`. It writes the file and reruns the install.

### 5. Check and confirm

Run `~/.agents/pstack/port/pstack doctor --offline` and report any problem with its fix. Tell the user the file was written, and that skills read it each time they run, so it applies from the next skill run in either harness. They can rerun this skill, or `pstack configure`, to change it.

### 6. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof (a `verify-*` skill, or an existing harness). If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, read `~/.agents/pstack/skills/create-verification-skill/SKILL.md` and follow it. On no, move on without pushing.
