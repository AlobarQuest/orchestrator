# Open decisions after the n8n onboarding

**Date:** 2026-10-23. **Owner:** Devon decides; the fork-sync session carries out each decision.

AlobarQuest/n8n-as-code and AlobarQuest/n8n-manager are onboarded to the fork-sync lane (ADR-0056
amendments 2 and 3). n8n-as-code landed v2.4.1, v2.5.0, v2.6.0 and v2.7.0 through the gate, and its
declaration follows every release. This page lists what the onboarding left for Devon, one
decision per section. Each section ends with its status, which is updated as it is resolved.

## 1. The project install that reads upstream directly

`~/Projects/listing-prep` has the n8n-as-code Claude plugin 2.2.6 installed at project scope from
the `n8nac-marketplace` marketplace, whose source is `https://github.com/EtienneLescot/n8n-as-code.git`
(`~/.claude/plugins/known_marketplaces.json`, `installed_plugins.json`). It reads upstream with no
review, so an update would take upstream's plugin as published, including the unpinned
`npx -y @n8n-as-code/mcp` MCP server that v2.6.0 added.

Options: remove the project install and the marketplace; or keep it and accept the bypass.

**Status:** resolved 2026-10-23. Devon chose removal. The project install was uninstalled
(`claude plugin uninstall n8n-as-code@n8nac-marketplace --scope project`, which also cleared
`listing-prep`'s untracked `.claude/settings.json`), and `n8nac-marketplace` was removed.
`listing-prep` loads n8n-as-code 2.3.0 from the user-scope `devon-plugins` install (section 2).

## 2. The user-scope plugin comes from a pre-gate checkout

The user-scope n8n-as-code plugin 2.3.0 is installed from the `devon-plugins` marketplace, a local
directory (`~/Developer/devon-plugins`). Its `n8n-as-code/` folder is a separate git clone of the
fork, checked out at `43e3e360`, a commit from the fork's old sync lane that the gate never
reviewed. That plugin has no MCP server.

The gated fork's `main` holds plugin 2.4.1, which has the unpinned MCP server. Devon decided on
2026-10-22 to keep the plugin off the tool installer until the hardening session pins that server
(`2026-10-21-handoff-fork-hardening.md`, "The second case").

Options: keep 2.3.0 until hardening pins the MCP server, then install from the fork through the
tool installer; or uninstall the plugin now.

**Status:** resolved 2026-10-23. Devon chose to keep 2.3.0 from `devon-plugins` until the hardening
session pins the MCP server; the plugin then moves to an install from the gated fork through the
tool installer. Nothing changed on the machine.

## 3. The global `n8nac` is a link to an old checkout

`/opt/homebrew/bin/n8nac` resolves through `npm link` to `~/Projects/n8n-as-code/packages/cli`, a
checkout of the old lane at v2.4.0 with six local commits that were never pushed, built locally.
The plugin's skill runs `npx --yes n8nac …`.

Options: replace the link with an n8nac built from the gated fork's `main` (v2.7.0) through a tool
installer row (a workspace-aware npm install, scripts disabled); or leave it until the plugin
question in section 2 is settled, since both change together.

Measured on 2026-10-23:

- `npx` resolves `n8nac` from `<npm prefix>/bin` (`/opt/homebrew/bin/n8nac`) and downloads from
  the registry when that path is missing, so the link can't be removed without a replacement: the
  skill would then run upstream's newest published `n8nac` unreviewed.
- The CLI's knowledge commands (`skills search`, `node-info`, `node-schema`, `guides`, `docs`,
  `examples`) need about 60 MB of generated data. Generating it clones n8n, runs `pnpm install`
  with install scripts, builds n8n's node packages and loads them with `require`, so it can't run
  on the operator machine.

**Status:** resolved 2026-10-23. Devon chose the tool-installer row, with the knowledge data taken only
as the `dist/assets/*.json` files of upstream's published `@n8n-as-code/skills` at the fork's
version (integrity-checked; none of its code is used). The row installs the whole built
checkout (the CLI's sibling packages are workspace links), and owns `<npm prefix>/bin/n8nac` so
the skill's `npx` resolves the gated build. The hardening session should remove the need for that
prefix link. The row landed in orchestrator #493 (`576ecf16`); the tool installer's pass that
afternoon installed n8nac 2.7.0 at `e238c7a`, passed every probe, and repointed
`/opt/homebrew/bin/n8nac` at `~/.local/share/sds-tools/npm/bin/n8nac`. From a cold directory,
`npm exec --no -- n8nac --version` printed 2.7.0 and `npx --yes n8nac skills node-info` answered
from the published data.

## 4. Two local clones from the old lane

`~/Projects/n8n-as-code` (section 3) and `~/Developer/devon-plugins/n8n-as-code` (section 2) are
clones of the fork from before its `main` was rebuilt; both carry a `pre-fork-sync-baseline` tag.
Six commits in `~/Projects/n8n-as-code` exist nowhere else: the old lane's CI (`43e3e360`,
`d53147ce`, `df4a0ed4`, `444d81f2`), which the fork-sync lane replaced, a `PROJECT.md` portfolio
manifest (`a019528c`) and a `.gitignore` change (`429461ce`).

Options: reset each to the gated `main` after sections 2 and 3 no longer depend on them, keeping
the six local commits on a branch or a bundle first; or leave them.

**Status:** open.

## 5. Upstream reports

The reviews found five upstream issues that the lane reports and does not fix. None reaches the
operator machine through the fork today.

| Issue | Where | Seen in |
|-|-|-|
| A workspace `.env` can set the n8n host, so a cloned repository can receive the API key | `packages/cli/src/services/config-service.ts` | v2.6.0 |
| Generated `AGENTS.md` can tell an agent to run a workspace's `node_modules/n8nac` | `packages/skills/src/services/cli-command-resolver.ts` | v2.7.0 |
| The docs downloader builds paths from headings in a remote `llms.txt` | `scripts/download-complete-docs.cjs` | v2.4.1 |
| TLS verification is skipped for private-network hosts without a configured CA | `packages/cli/src/core/services/tls-certificates.ts` | v2.5.0 |
| The VS Code extension accepts a trusted CA from workspace settings | `packages/vscode-extension` | v2.5.0 |

Options: file them as issues on EtienneLescot/n8n-as-code (an outward-facing act Devon confirms);
or record them only.

**Status:** open.
