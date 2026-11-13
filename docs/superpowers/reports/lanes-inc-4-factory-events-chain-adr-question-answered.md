# Question: lanes-inc-4-factory-events-chain-adr (W-128)

I can't draft amendment 24's credentials section without one authority answer (Q1). Q2 is smaller,
and I can carry it if you prefer. I haven't written either amendment yet. Facts were read at
orchestrator `2cf43757`, intent-packages `78bb302`, security-standards `1baec77`, and sds-hq
`e5c4547`.

## Q1 (blocking): does a lifecycle emitter that isn't an approver get the append role?

Every intent-packages lifecycle command emits to the chain through the same `FactoryEventsEmitter`
(`cli.py` 129 `transition`, 171 `approve`, 189 `revise`, 203 `supersede`). So a single append path
serves the approvers and every other lifecycle caller.

One scheduled lane emits lifecycle events without approving: the rotation proposer. It runs
`revise` and `transition` (`rotation_proposer/standing.py` 281 and 290) and never `approve`
(its docstring, line 13). After 3f-5 it runs in a VPS container.

D-40 B gives append credentials to "the approvers", and D-42 A extends a write path to "the Mac
nightly". Neither names the rotation proposer.

An emit failure doesn't stop `transition` or `revise`: each catches `EmitError` and records
`event_id: None` in the lineage (`operations.py` 158-164 and 351-357). Only `approve` aborts on a
failed emit (288-305). Nothing reads `package.transitioned` or `package.revised` events. The two
verifiers match only `package.approved` (intent-packages `operations.py` 503; orchestrator
`package_sources.py` 294).

- **(A) The rotation proposer also holds the append role.** Every lifecycle event lands in the one
  chain. Cost: one more holder of a credential that can append any event, a forged
  `package.approved` included. The event's `actor` is self-declared (`FACTORY_AGENT_ID`, emitter
  line 92), so the chain can't tell which holder appended it.
- **(B) Only the approvers and the nightly hold it (recommended).** The approvers are your
  `approve`, the cycle's `standing-approve`, and the bump proposer's `approve --by-policy`. After
  3f-5, the rotation proposer's `revise` and `transition` write no chain event: the lineage records
  each with `event_id: None`, as it does for any failed emit today. No approval check changes. The
  chain loses that lane's lifecycle events, which nothing reads.

Under either option, the new emitter never falls back to a local file. With no chain configured,
the emitter fails, so an `approve` writes nothing.

## Q2: may a person's hand verification on the Mac read with the read-only role?

The chain is read in the `orchestrator` CLI process tree, not the server (`cli.py` 13 is the only
importer of `package_sources`). One carry makes three reads:

1. `factory_events verify` (`package_sources.py` 231-253).
2. The event scan (276-296).
3. intent-packages' `verify-approval`, through `default_chain_checker` (`operations.py` 511-530),
   which runs as a subprocess (`package_sources.py` 168).

Readers beyond the carrier:

- your hand `orchestrator intake-package` (`cli.py` 254).
- the `factory` journey's `emit-intake-payload` (intent-packages `factory/journey.py` 141-148).
- intent-packages `verify-approval` run by hand (`cli.py` 211-214).

D-40 B names "read-only for the carrier".

- **(A) Recommended.** The read-only role is also read by those hand paths on the Mac. It can't
  append, so this widens nothing that writes.
- **(B)** Only the carrier reads the chain. A hand intake then can't verify an approval and fails
  closed.

If you pick A for Q2, I name each hand path as a reader in the amendment. If you pick B, the
amendment says each hand path fails closed after the cut-over.
