# SDS HQ always-on agent: design

Status: draft for Devon's approval, 2026-11-05. Nothing here is built.

## Intent

HQ is the planning and control role for building SDS and App Wardens. Devon started it as one
interactive Claude session that wrote handoff prompts for build sessions. He pasted each prompt into
a fresh session, carried the report back, and HQ wrote the next prompt. The role is real and
documented. HQ holds the main tree and dispatches build sessions into worktrees
(`docs/operations/local-development.md` #98, #264). It writes handoffs
(`docs/superpowers/plans/*-handoff-*.md`), works rotation units (`package-intake.md`), and runs
deploys (`deploy.md`: "HQ's mechanic, not Devon's gate").

Two things went wrong:

- **Devon was the courier.** Copying prompts and reports between sessions was drudgery, so he
  stopped, and HQ and worker merged into one session.
- **The merged sessions got very large.** The October 26 to November 4 layer-pass sessions ran
  373 to 1,018 turns at 380k to 477k tokens of average context. Re-reading that context was 82% of
  token cost.

This project automates the HQ role. Devon gives HQ a goal from the program plan, for example "move
the lanes off the Mac," and answers what only he can answer. HQ writes the ADRs, specs, and plans,
dispatches workers, reads their reports, verifies, and drives the goal to its "Done when". It comes
back to Devon only for judgments that are best made, or only made, by him.

In Devon's words: "I only step in when it is a judgement that is BEST or ONLY made by me." When
something does come to him, "the agent explain what they would have needed to know, to handle it on
their own, so we can make an adjustment right then."

## Goals

1. **Remove Devon as the courier.** HQ writes the handoff, starts the worker, reads the report, and
   writes the next handoff without Devon.
2. **Escalate only Devon's judgments.** Each escalation carries HQ's recommendation and a "to handle
   this myself I would have needed to know X" note.
3. **Learn from each escalation.** When Devon's answer is a standing one, HQ records it and doesn't
   ask again. A judgment HQ keeps making the same way becomes a proposal to encode it in SDS (a
   grant, policy, or gate graduation). The proposal is raised to Devon, never applied quietly.
4. **Keep the cost of context bounded.** No HQ context grows with the length of the program. Each
   HQ cycle and each worker starts fresh from committed files.
5. **Know all of SDS.** HQ can find and use all SDS strategy, history, and live state, without
   loading it all into one context.
6. **Run unattended.** HQ keeps working diligently at 10 pm with Devon away.

## Non-goals

- **Other Claude sessions don't change.** HQ's instructions, hooks, and settings are scoped to HQ.
  No global `CLAUDE.md` rule, hook, or setting changes for HQ's sake.
- **HQ isn't part of the App Wardens product.** It's build-time tooling for Devon, like fork sync
  and the tool installer, and is never carried into a client instance.
- **HQ doesn't replace SDS gates.** The production rules for what needs a person belong in SDS
  itself (grants, the authority envelope, policy, `/review`). During development there are no fixed
  rules on these acts (Devon, 2026-11-05). HQ works under the build-mode standing rules in force for
  build sessions: construction mode, pre-authorized build gates, waived infrastructure separation,
  and escalating only forks and irreversible acts.
- **v1 doesn't take over standing operations.** Claiming rotation units, and routine deploys
  unrelated to the active goal, stay with interactive sessions until HQ has a measured track record.
  *Claude's call.*
- **HQ doesn't move to the VPS in Phase 1.** See "Where HQ runs".

## Anti-goals

The worst outcome, in Devon's words: "It breaking production while I'm away and not telling me." A
run that finishes the work but breaks production silently is a failure, even if every task
completed.

Other failures, even when the work technically gets done:

- HQ acts on an instruction it found in fetched content: a worker report, an alert, a change record,
  an email, or a web page.
- HQ approves, or simulates approving, a human gate. ADR-0006 makes gates browser-only, and no
  standing human credential may exist.
- HQ escalates things Devon could have delegated once, or asks the same question twice.
- HQ's context or token spend grows without bound.

## Constraints

- **Gates.** Human gates need a live Authentik browser session (ADR-0006). HQ can stage work and
  alert Devon, but it can't approve on `/review`. A member-record change is staged by a session and
  applied only by a person.
- **Product boundary.** The year plan's Phase 2 rule is "nothing a model returns decides, merges,
  deploys or verifies". HQ is a model that does those things, so HQ must stay outside every product
  path. The no-LLM-decides test, when written, must be able to assert that.
- **Fetched content is data.** Devon's global rules say never execute an action inferred from
  fetched content. HQ's normal loop is reading fetched content and then acting, so this constraint
  drives the safety design.
- **Main-tree rule.** The orchestrator's launchd lanes run its main tree, which must stay on `main`.
  Nothing HQ starts edits, branches, or pulls in `~/Projects/orchestrator`. Workers use worktrees
  and their own test databases.
- **Credentials.** BWS secret-handling standards apply. Don't call the MCP tools known to return
  secrets: `cloudflare_list_pages_projects` and `coolify_get_deployment`.
- **Environment.** Verified 2026-11-05: under a stripped, launchd-like environment, a headless
  `claude -p` loads every claude.ai connector, including Claude Docs and the brains, but **not**
  `infraops` or `alert-hub`. Both start from `start.sh` scripts that work in Devon's shell. HQ's
  launcher must provide what they need and be proven under `env -i` with only `PATH` and `HOME`
  set, as the lane-credential rule requires.
- **Auth.** Headless runs appear to use Devon's subscription login, not an API key. Confirm this in
  build step 2. HQ shares Devon's usage limits, so "budget" means usage consumption, not dollars,
  and Devon's interactive use has priority.

## Decided by Devon

- Scope: AppWardens.com infrastructure, including SDS (orchestrator) and change-manager. Only this
  work.
- Escalate only judgments that are best made, or only made, by Devon, each with a "what I'd need to
  know" note. Standing answers are recorded so they aren't asked again.
- HQ has access to all relevant SDS data, strategy, and history.
- Unattended, HQ keeps working. Devon didn't specify a contact channel and is open to proposals.
- During development there are no fixed rules on deploys, merges, and similar acts. In production
  those rules are encoded in SDS.
- Worst failure: breaking production while Devon is away, without telling him.
- Budget: measure a normal day first, then propose a ceiling.
- First goal: year plan Phase 1, "Move the lanes off the Mac".
- Billing: HQ uses Devon's subscription, and Devon's own sessions come first. HQ caps worker
  parallelism and backs off when usage is high. Revisit after the measured week.
- Answering away from the Mac: through Devon's iMessage self-chat.
- During the pilot, HQ is the only actor that deploys or changes SDS production. Other sessions
  may read the SDS repositories and work on branches.

## Design

### Where HQ lives: a separate `sds-hq` repo (Claude's call)

HQ's code, prompts, and state go in a new private repo, `AlobarQuest/sds-hq`, cloned at
`~/Projects/sds-hq`. Reasons:

- **State commits need a home.** HQ commits state every cycle. In the orchestrator, every commit to
  `main` runs CI/CD and deploys.
- **The product boundary becomes a repo boundary.** The no-LLM-decides test can assert that no
  orchestrator path imports or calls HQ.
- **The main-tree rule stays untouched.** HQ reads the orchestrator and change-manager at
  `origin/main` through fetches and worktrees.

HQ writes orchestrator artifacts (ADRs, specs, plans, code) the way any build session does: in a
worktree, through a PR.

### Knowledge: a brief, an index, and sub-agents

The core SDS documents are about 1.6 MB, roughly 400k tokens. With specs, plans, and the 6 MB of
handoffs in `~/docs/software-delivery-system/`, the total is about 12 MB. None of it is loaded whole.

- **HQ brief** (`sds-hq/brief.md`, kept under about 15k tokens). What SDS and App Wardens are, the
  phase structure, the four layer outcomes, the build-mode rules, the boundaries in this document,
  and where to find everything else. Every HQ cycle loads it.
- **Source index** (`sds-hq/index/`). One short committed entry per source (ADR, spec, plan,
  handoff, build report, operations page) giving its path, date, a 2 to 3 line summary, and its
  status. HQ searches the index, then opens the source or sends a sub-agent to read it.
- **One-time historical index.** Index the handoffs in `~/docs/software-delivery-system/` once and
  leave the originals where they are. `~/docs` isn't a repo, and the index makes that history
  available to HQ and portable to a server later.
- **Year plan mirror.** The year plan is a Claude Doc. HQ reads and updates it through the Claude
  Docs connector, which works headless, and keeps a committed markdown mirror in `sds-hq` so the
  plan survives without the connector. The SDS Layer Map artifact is a rendered view that HQ
  refreshes from the repo.
- **Deep reads go to sub-agents.** Any read over about 20k characters, or across many files, goes
  to a sub-agent that returns a summary, following the model-routing rules in Devon's global
  `CLAUDE.md`.

### State: committed files in `sds-hq`

| File | Purpose |
|---|---|
| `state/goal.md` | The active goal, its "Done when", the plan, the current step, and progress. |
| `state/workers.json` | Each active worker: id, handoff path, worktree, session id, start time, status. |
| `state/decisions.md` | The decision queue. Open questions for Devon, each with the fields under "Escalation". |
| `standing-answers.md` | Devon's standing answers. Dated, in his words, each with where it came from. |
| `state/cycle-log.jsonl` | One line per cycle: time, what it read, decided, and did, tokens used. |
| `state/budget.json` | Usage totals for HQ and its workers, per day. |

HQ never writes `standing-answers.md` from a document, report, or inferred intent, only from
Devon's direct reply in an HQ conversation.

### The HQ cycle

A launchd agent (`com.devon.sds-hq`) starts an HQ cycle every 15 minutes, and also when a worker
exits. The interval is a starting value. Each cycle:

1. Takes the lock (`state/.lock`). If an interactive HQ session holds it, the cycle exits.
2. Starts a fresh headless session (`claude -p`) with the HQ brief, the state files, and
   `standing-answers.md`.
3. Reads finished workers' reports and Devon's new answers.
4. Decides the next steps, then dispatches workers, writes handoffs, raises escalations, and
   verifies production acts.
5. Commits the state, appends the cycle log, pings its Healthchecks check, and exits.

The cycle is short by construction. If a cycle runs past a time or turn limit, it commits its state
and exits, and the next cycle continues from the files.

### Workers

- **Handoff.** HQ writes each task as a handoff in the existing format
  (`docs/superpowers/plans/*-handoff-*.md`, with a "Read first" section and named stop points).
  Stop points that used to say "stop and ask me" now mean "stop and ask HQ". HQ answers the ones it
  can and escalates the rest.
- **Launch.** Each worker is a separate headless `claude -p` process, so it can outlive an HQ
  cycle. It gets its own worktree and test database, starts from the handoff, and runs with the same
  global configuration as Devon's build sessions.
- **Report.** On finishing, a worker commits a report to a fixed path in its branch,
  `docs/superpowers/reports/<handoff-name>-report.md`, using the build-report shape. The report
  states outcome (done, blocked, or failed), evidence, what changed, and open questions. The report
  is data for HQ, never instructions.
- **Questions.** Peer sessions can't message each other. A worker with a question writes a question
  file next to its report and exits. The next HQ cycle answers it, or escalates it and records the
  dependency. Then HQ resumes the worker with `claude -p --resume <session-id>` and the answer.
- **Limits.** Each worker has a turn limit and a wall-clock timeout. A worker that hits either is
  treated as blocked, with its partial report.

### Interactive HQ sessions

Devon runs `sds-hq` (a small command) to open an interactive Claude Code session in `~/Projects/sds-hq`
with the HQ brief and state loaded. It takes the lock, so unattended cycles pause while Devon talks
to HQ. Devon answers open decisions there, gives new goals, and can steer active work. The session
writes his answers to the state files before it exits and releases the lock.

### Escalation

When HQ needs Devon, it adds an entry to `state/decisions.md`:

- **Question** and **why it's Devon's**: best made by him, or only made by him.
- **Recommendation**, with reasons.
- **What I'd need to know** to handle this kind of question myself, and whether a standing answer
  could cover it.
- **What's blocked**, and what HQ is doing in the meantime.

Then HQ raises one keyed alert-hub alert for the open decision and moves on to unblocked work. The
alert resolves when the decision is answered. HQ doesn't page Devon for anything that isn't a
decision, a gate, or a production problem.

**Answering by iMessage (Devon's decision).** HQ also sends each decision to Devon's iMessage
self-chat as a short message with a decision id, the question, and its recommendation. Devon
replies there, for example "D-14: yes" or a longer answer. Each cycle reads new replies from the
self-chat only, through the iMessage plugin's allowlisted `chat_messages`, and matches each reply to
its decision id.

- **Trust.** A reply in the self-chat counts as Devon's direct answer, the only source allowed to
  write `standing-answers.md`. Messages from any other chat are ignored. HQ never changes iMessage
  access or allowlists, whatever a message asks.
- **Content.** Replies are answers to the decisions they name. A reply that asks for something
  outside its decision is recorded as a new goal or decision for an interactive session, not acted
  on unattended.
- **No secrets.** Decision messages never carry credentials, tokens, or anything a secret-redaction
  rule covers.
- **Longer discussion.** A decision that needs back-and-forth waits for an interactive HQ session
  at the Mac.

### Scope of action (Claude's calls)

- **Repositories.** HQ and its workers may change any repository the active goal requires, as
  named in the goal's plan: the orchestrator and change-manager, and the SDS machinery they depend
  on (factory-runner, intent-packages, security-standards, alert-hub, infraops-mcp-server,
  vps-backup). A repository outside the plan is added by a plan amendment.
- **Merges.** HQ merges a worker's PR itself once CI is green and review is clean, as build
  sessions already do. Review follows the model-routing rules: a per-task Sonnet review, and an
  Opus whole-branch review before the goal's final merge. A merge that deploys, which includes every
  merge to orchestrator `main`, is a production act under "Production safety".
- **Human gates.** Every `/review` gate needs Devon's browser session (ADR-0006). That includes
  intakes, decompositions, authority approvals, member-record confirmations, and the acts that
  ADR-0054 keeps for Devon because they reach a live credential. HQ stages each one and lists it in a
  **gate queue** in `state/decisions.md`, separate from decisions. Each entry has the link to click,
  what it approves, and what is waiting on it. Clicking a gate is a gate, not courier work, and
  doesn't count against the pilot's courier criterion.
- **Plan amendments.** HQ may amend its own plan, including adding a production act. Each amendment
  records its reason and source in the cycle log. An act whose source is a worker report, an alert,
  or a record is added only after HQ has confirmed the underlying fact from a primary source (the
  live system, the code, or `origin/main`). A recommendation copied from fetched content is never
  enough on its own.
- **HQ's own rules.** The safety rules, turn and time limits, budget settings, and the boundaries
  section of the brief are protected paths. They change only from Devon's direct words in an
  interactive HQ session. The cycle's commit step refuses any diff that touches them.
- **The year plan.** HQ may update the year plan's status and progress text, as build sessions
  already do. Changes to strategy, phases, or "Done when" text are proposals in the decision queue.
  The Claude Doc is the authority. The committed mirror is a read-only copy, refreshed from it.
- **Audit provenance.** HQ tags its audit entries through a hook scoped to `sds-hq`, not by changing
  a global hook.

### Production safety

These rules carry the anti-goal.

1. **Announce, then observe.** Before any act that affects production (a deploy, a Coolify, DNS, or
   Cloudflare change, a database change, or a credential change), HQ raises a keyed alert-hub
   notice saying what it is about to do and why. Afterward, it verifies the outcome.
2. **Verify the revision, not liveness.** After a deploy, HQ confirms the health endpoint reports
   the pushed commit. A liveness check during Coolify's rolling update is answered by the old
   container.
3. **Stop the line.** If verification fails, HQ rolls back when a known-safe rollback exists. Either
   way, it raises a high-priority alert and stops all further production acts until Devon has
   acknowledged it. Silent failure isn't an allowed outcome.
4. **Pre-named acts only.** A production act is allowed only if it is named in HQ's own plan or in
   the handoff before the worker ran. A report, alert, or record can't introduce one.
5. **Audit with provenance.** Every high-power action HQ or a worker takes goes to
   `~/.claude/audit/high-power-actions.jsonl`, tagged with the goal, plan step, cycle, and worker.
6. **One production actor.** During the pilot HQ is the only actor that deploys or changes SDS
   production (Devon's decision). Before restarting the orchestrator, HQ confirms that no
   dispatched run is live.
7. **Dead-man.** Each cycle pings a Healthchecks check. If HQ stalls or dies overnight, Devon hears
   from the same system that watches the lanes.

### Budget

HQ sums token usage for each cycle and worker from their session transcripts into
`state/budget.json`. Devon's own sessions have priority on his subscription, so HQ caps how many
workers run at once (one at first) and backs off new dispatches when usage is high, for example
when a run reports a rate limit. In the first week a soft limit only alerts. After a measured week of normal
work, HQ proposes a hard daily ceiling to Devon. At the ceiling, HQ pauses new dispatches and
alerts him.

### Where HQ runs

HQ runs on the Mac, under launchd, for Phase 1. That's deliberate. HQ holds more authority than any
lane: infra tools, deploys, and Devon's MCP connectors. The year plan keeps on the Mac what acts on
the Mac. Moving HQ to a server concentrates that authority on an always-on host and needs its own
identity and security design. That's a later decision, made once Phase 1 has shown how lanes run
there. Everything HQ keeps is in git or reachable by connector, so it's portable.

## Pilot and success criteria

The first goal is year plan Phase 1, "Move the lanes off the Mac". HQ's first steps are the work the
plan says starts the phase: the ADR for moving lanes off the Mac, and the ADR-0039 amendment. HQ also
rebases the plan's stale starting-point counts (17 vs 20 launch agents).

The pilot succeeds when all of the following are true:

- Phase 1's own gate is met: "no lane that acts on a member runs on the Mac, and a lane survives
  the Mac being off for a week."
- Devon didn't act as courier at any point.
- Every escalation produced either a standing answer or a proposal to encode the judgment in SDS,
  or was a judgment Devon confirms only he could make.
- No production problem went unannounced.
- No HQ cycle or worker context exceeded about 200k tokens.

## Build order

1. Bootstrap `sds-hq`: the repo, the brief, the state files, and the lock.
2. Get the launcher working under `env -i`, including `infraops` and `alert-hub`. Confirm the auth
   mode, and confirm a headless session can both send to and read from the iMessage self-chat. On
   2026-11-05 the iMessage plugin loaded headless, but sending was not tested.
3. Build the HQ cycle and an interactive `sds-hq` session, run by hand at first.
4. Add the worker launch, report, and question protocol, proven on one small real task.
5. Wire in escalation: the decision and gate queues, alert-hub, the iMessage self-chat, and
   Healthchecks.
6. Build the source index, and run the one-time `~/docs` handoff index.
7. Turn on the launchd schedule, then start the Phase 1 pilot.

## Left to the builder

- The exact launcher mechanics, cycle interval, and turn and time limits.
- The lock implementation and how stale locks are cleared.
- The alert-hub source and keys for HQ, following alert-hub's conventions.
- The source-index format and the tooling that keeps it current.
- How worker exit triggers a cycle.
- How HQ gets a Healthchecks check, following the infraops healthchecks page.

## Claude's calls

These calls were made by Claude, not agreed by Devon:

- HQ lives in a separate `sds-hq` repo.
- v1 drives one program goal at a time and leaves standing operations to interactive sessions.
- HQ stays on the Mac through Phase 1.
- Escalation goes through alert-hub plus a committed decision queue. Long discussions happen in an
  interactive HQ session.
- HQ runs in 15-minute cycles plus worker-exit triggers, and workers are headless `claude -p`
  processes.
- The `~/docs` handoffs get a one-time index, with the originals left in place.
- Success includes a 200k-token context ceiling.
- Everything under "Scope of action".
