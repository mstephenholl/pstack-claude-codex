# pstack for Codex

This fork of [cursor/plugins](https://github.com/cursor/plugins) ports [pstack](pstack/README.md) to Codex. Upstream runs pstack in Cursor and ships it as a Claude Code plugin. This fork covers what upstream does not: Codex, and Codex panels that can seat Claude models next to OpenAI ones. It also carries three skills from Cursor's team kit that pstack calls: `deslop`, `control-ui`, and `control-cli`. Everything else from upstream is removed.

## Which install you need

| You use | Install |
|---|---|
| Codex | This fork. |
| Claude Code | Upstream's plugin. |
| Both | Upstream's plugin in Claude Code, and this fork in Codex. See [Using Claude Code and Codex together](pstack/README.md#using-claude-code-and-codex-together). |

## Codex

```sh
curl -fsSL https://raw.githubusercontent.com/mstephenholl/pstack-claude-codex/main/pstack/port/bootstrap.sh | bash
pstack configure
pstack doctor
```

Then start any task that needs rigor by typing `$` in Codex and picking `$pstack:poteto-mode`.

## Claude Code

In Claude Code, run:

```
/plugin marketplace add cursor/plugins
/plugin install pstack@cursor-plugins
```

Then choose models with `/pstack:setup-pstack`, and start with `/pstack:poteto-mode <task>`. Upstream maintains that plugin. See [its README](https://github.com/cursor/plugins/tree/main/pstack).

## More

- [`pstack/README.md`](pstack/README.md) covers the `pstack` command, using the skills, models, running both harnesses, and differences from Cursor's pstack.
- [`pstack/port/README.md`](pstack/port/README.md) covers updating from upstream, porting a new skill, CI, releases, and the smoke test.
- The [releases page](https://github.com/mstephenholl/pstack-claude-codex/releases) lists every release and what changed in it.
