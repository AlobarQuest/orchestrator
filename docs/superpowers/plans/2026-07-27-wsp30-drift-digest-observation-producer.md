# WS-P3.0 — Drift Digest Observation Producer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily infra-drift audit post one normalized, deduped, non-fatal-on-failure observation per Coolify instance to the orchestrator's `POST /api/v1/observations`, giving the observation spine its first external producer.

**Architecture:** A new TypeScript module in `infraops-mcp-server` mirrors the proven `src/change-manager/` pattern — a pure normalizer, a thin `fetch` client, and a CLI — invoked from `scripts/drift-audit.sh` in the same best-effort idiom used for the change-manager sync. The orchestrator gains **no code**: only a new M2M credential attributed to the `drift-reconciler` registry actor. Observations are append-shaped and content-addressed so an unchanged re-post dedups and a new audit run appends.

**Tech Stack:** TypeScript (ESM, Node ≥18, `tsc` → `dist/`), vitest, bash + shellcheck, BWS for secrets, Coolify env for the orchestrator credential.

**Spec:** `docs/superpowers/specs/2026-07-27-wsp30-drift-digest-observation-producer-design.md` (this repo). Read it before Task 1.

## Global Constraints

- **Two repos.** Tasks 1–3 and 5 change `~/Projects/infraops-mcp-server`. Task 4 changes production Coolify env only. Tasks 6–7 produce evidence and close out in `~/Projects/orchestrator`. **No file under `~/Projects/orchestrator/src/` is modified by any task.**
- **The orchestrator never polls.** The producer pushes. Nothing in this plan adds an outbound HTTP client to the orchestrator repo, so no `OUTBOUND_ALLOWLIST` entry is needed.
- **No work units, ever, from this path.** Observations only.
- **No new vocabulary.** `source_system: drift_digest` and `observation_type: drift` already exist. If a payload field seems to demand a vocabulary change, the payload is wrong.
- **Counts only, never raw text.** External error strings, rule descriptions, reasoning, and proposal text never enter `facts` or `summary`.
- **Fact keys must never contain** `log`, `body`, `instruction`, `token`, `credential`, `secret`, `password`, `bearer`, `authorization`, `api_key` — the ingest secret scanner rejects the whole observation on a match. Never key facts by an infra rule key.
- **Facts bounds:** non-empty object, ≤ 4096 bytes encoded, keys ≤ 64 chars, string values ≤ 512 chars, lists ≤ 30 items.
- **`expected_version` is always `0`.** `idempotency_key` ≤ 200 chars. `observed_at` must be timezone-aware and must be the report's `generated_at`, never the post time.
- **Every orchestrator M2M call sends both** `Authorization: Bearer <token>` **and** `X-Credential-Key-Id: <key-id>`.
- **Secrets:** the bearer value never enters a tracked file, a prompt, a log line, a commit message, or a test fixture. Fetched at runtime from BWS by stable UUID; only `sha256(bearer)` goes into Coolify.
- **Existing drift outputs are untouchable.** The change-manager sync, security-drift run, Resend email digest, Healthchecks pings, and the script's exit code must all behave identically before and after.
- Commit after every task. Run `npm run build && npm test` in `infraops-mcp-server` before each commit in Tasks 1–3, and `make check`-equivalent (`npm run build && npm test && shellcheck --severity=warning scripts/*.sh`) before the Task 5 commit.

**Fixed identifiers used across tasks:**

| Thing | Value |
|---|---|
| Orchestrator base URL | `https://sds.alobar.net` |
| Credential key id | `orchestrator-drift-reporter` |
| Registry actor (`agent_id`) | `drift-reconciler` |
| Role | `system` |
| Coolify app uuid (orchestrator, prod) | `eqj5l7k705fhi12x9i74fqf0` |
| Report file | `<report-dir>/<YYYY-MM-DD>.json` |

---

## File Structure

**Create (infraops-mcp-server):**

| File | Responsibility |
|---|---|
| `src/orchestrator/observation.ts` | Pure normalizer: `DriftReport` → `ObservationCommand[]`. All vocabulary, bounds, status/severity mapping and content-addressing live here. No I/O, no `fetch`, no `process.env`. |
| `src/orchestrator/api-client.ts` | Thin `fetch` wrapper. One method: `postObservation`. No knowledge of drift reports. |
| `src/cli/orchestrator-cli.ts` | Arg parsing, file reading, env guard, per-instance fail-open loop, `--dry-run`. No normalization logic. |
| `tests/orchestrator-observation.test.ts` | Normalizer tests. |
| `tests/orchestrator-api-client.test.ts` | Client tests (mocked `fetch`). |
| `tests/orchestrator-cli.test.ts` | CLI arg-parsing and env-guard tests. |

**Modify (infraops-mcp-server):**

| File | Change |
|---|---|
| `scripts/drift-audit.sh` | One added block after the change-manager sync (currently ends line 98). |
| `.bws-secrets.toml` | Regenerated to include the new reporter-token UUID. |

**Modify (orchestrator):**

| File | Change |
|---|---|
| `PROJECT.md` | Close the observation-tail backlog item. |
| `CLAUDE.md` | One new invariant (Task 7). |

The three-file split in `src/orchestrator/` is deliberate and mirrors `src/change-manager/`: the normalizer is the only part with interesting logic and it is pure, so it is exhaustively testable without mocking anything.

---

## Task 1: The observation normalizer

**Files:**
- Create: `~/Projects/infraops-mcp-server/src/orchestrator/observation.ts`
- Test: `~/Projects/infraops-mcp-server/tests/orchestrator-observation.test.ts`

**Interfaces:**
- Consumes: `DriftReport`, `InstanceSection`, `DeltaItem` from `../standards/report.js` (already exist — do not redefine them).
- Produces:
  - `export interface ObservationCommand` — the exact POST body shape (fields listed in Step 3).
  - `export function canonicalJson(value: unknown): string`
  - `export function factDigest(facts: Record<string, unknown>): string` — 12 hex chars.
  - `export function buildInstanceFacts(report: DriftReport, instance: string): Record<string, unknown>`
  - `export function buildObservation(report: DriftReport, instance: string): ObservationCommand | null`
  - `export function buildObservations(report: DriftReport): ObservationCommand[]`
  - `export const INSTANCE_SUBJECTS: Record<string, { subject: string; environment: string }>`

Task 3 calls only `buildObservations`. Tasks 2 and 3 both import the `ObservationCommand` type.

- [ ] **Step 1: Write the failing tests**

Create `tests/orchestrator-observation.test.ts`:

```typescript
import { describe, it, expect } from 'vitest';
import {
  buildInstanceFacts,
  buildObservation,
  buildObservations,
  canonicalJson,
  factDigest,
} from '../src/orchestrator/observation.js';
import type { DriftReport } from '../src/standards/report.js';

const BANNED_KEY_PARTS = [
  'api_key',
  'authorization',
  'bearer',
  'body',
  'credential',
  'instruction',
  'log',
  'password',
  'secret',
  'token',
];

function report(overrides: Partial<DriftReport> = {}): DriftReport {
  return {
    generated_at: '2026-07-27T07:00:07Z',
    instances: {
      prod: {
        ok: true,
        summary: {
          total_proposals: 1,
          by_risk: { safe: 1, caution: 0, destructive: 0 },
          by_kind: { remediation: 1, question: 0 },
        },
        proposals: [],
      },
      dev: {
        ok: true,
        summary: {
          total_proposals: 0,
          by_risk: { safe: 0, caution: 0, destructive: 0 },
          by_kind: { remediation: 0, question: 0 },
        },
        proposals: [],
      },
    },
    totals: {
      total_proposals: 1,
      by_risk: { safe: 1, caution: 0, destructive: 0 },
      by_kind: { remediation: 1, question: 0 },
      instances_ok: 2,
      instances_failed: 0,
    },
    delta: {
      new: [
        {
          instance: 'prod',
          identity: 'prod::coolify.enable_healthcheck::abc',
          description: 'd',
          risk: 'safe',
          reasoning: 'r',
        },
      ],
      resolved: [],
      unchanged: 0,
    },
    ...overrides,
  } as DriftReport;
}

describe('canonicalJson', () => {
  it('sorts object keys recursively and uses compact separators', () => {
    expect(canonicalJson({ b: 1, a: { d: 2, c: 3 } })).toBe('{"a":{"c":3,"d":2},"b":1}');
  });

  it('preserves array order', () => {
    expect(canonicalJson([3, 1, 2])).toBe('[3,1,2]');
  });
});

describe('factDigest', () => {
  it('is stable for equal facts regardless of key insertion order', () => {
    expect(factDigest({ a: 1, b: 2 })).toBe(factDigest({ b: 2, a: 1 }));
  });

  it('changes when any fact changes', () => {
    expect(factDigest({ a: 1 })).not.toBe(factDigest({ a: 2 }));
  });

  it('is 12 hex characters', () => {
    expect(factDigest({ a: 1 })).toMatch(/^[0-9a-f]{12}$/);
  });
});

describe('buildInstanceFacts', () => {
  it('lifts the per-instance summary and counts that instance-s delta items', () => {
    expect(buildInstanceFacts(report(), 'prod')).toEqual({
      report_date: '2026-07-27',
      instance: 'prod',
      instance_ok: true,
      total_proposals: 1,
      by_risk: { safe: 1, caution: 0, destructive: 0 },
      by_kind: { remediation: 1, question: 0 },
      delta_new: 1,
      delta_resolved: 0,
      read_error_count: 0,
    });
  });

  it('does not attribute another instance-s delta items', () => {
    expect(buildInstanceFacts(report(), 'dev')).toMatchObject({
      instance: 'dev',
      total_proposals: 0,
      delta_new: 0,
      delta_resolved: 0,
    });
  });

  it('emits a bounded fact set for an unreachable instance and never its error text', () => {
    const r = report({
      instances: {
        prod: { ok: false, error: 'connect ECONNREFUSED 10.0.0.1:8000 — secret in url' },
      },
    } as Partial<DriftReport>);
    const facts = buildInstanceFacts(r, 'prod');
    expect(facts).toEqual({
      report_date: '2026-07-27',
      instance: 'prod',
      instance_ok: false,
      read_error_count: 0,
    });
    expect(canonicalJson(facts)).not.toContain('ECONNREFUSED');
  });

  it('counts per-endpoint read errors without carrying their text', () => {
    const r = report({
      instances: {
        prod: {
          ok: true,
          summary: {
            total_proposals: 0,
            by_risk: { safe: 0, caution: 0, destructive: 0 },
            by_kind: { remediation: 0, question: 0 },
          },
          proposals: [],
          errors: ['GET /applications failed', 'GET /services failed'],
        },
      },
    } as Partial<DriftReport>);
    const facts = buildInstanceFacts(r, 'prod');
    expect(facts.read_error_count).toBe(2);
    expect(canonicalJson(facts)).not.toContain('/applications');
  });

  it('never produces a fact key containing a scanner-banned substring', () => {
    const facts = buildInstanceFacts(report(), 'prod');
    const keys: string[] = [];
    const walk = (v: unknown): void => {
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        for (const [k, child] of Object.entries(v as Record<string, unknown>)) {
          keys.push(k);
          walk(child);
        }
      }
    };
    walk(facts);
    for (const k of keys) {
      expect(k.length).toBeLessThanOrEqual(64);
      for (const banned of BANNED_KEY_PARTS) {
        expect(k.toLowerCase()).not.toContain(banned);
      }
    }
  });

  it('stays far inside the 4096-byte fact bound', () => {
    expect(Buffer.byteLength(canonicalJson(buildInstanceFacts(report(), 'prod')), 'utf8')).toBeLessThan(1024);
  });
});

describe('buildObservation', () => {
  it('builds the full command for a drifting instance', () => {
    const o = buildObservation(report(), 'prod')!;
    expect(o.source_system).toBe('drift_digest');
    expect(o.observation_type).toBe('drift');
    expect(o.trust_classification).toBe('monitor');
    expect(o.subject_type).toBe('service');
    expect(o.subject_reference).toBe('coolify:prod');
    expect(o.environment).toBe('production');
    expect(o.expected_version).toBe(0);
    expect(o.source_url).toBeNull();
    expect(o.payload_digest).toBeNull();
    expect(o.observed_at).toBe('2026-07-27T07:00:07Z');
    expect(o.idempotency_key).toBe('drift-digest:2026-07-27T07:00:07Z:prod');
    expect(o.idempotency_key.length).toBeLessThanOrEqual(200);
    expect(o.source_reference).toMatch(/^infra-drift:2026-07-27T07:00:07Z:prod:[0-9a-f]{12}$/);
    expect(o.summary.length).toBeLessThanOrEqual(512);
  });

  it('maps dev to the development environment', () => {
    const o = buildObservation(report(), 'dev')!;
    expect(o.subject_reference).toBe('coolify:dev');
    expect(o.environment).toBe('development');
  });

  it('is passed/info on a clean reachable instance', () => {
    const o = buildObservation(report(), 'dev')!;
    expect(o.status).toBe('passed');
    expect(o.severity).toBe('info');
  });

  it('is degraded/warning when proposals exist', () => {
    const o = buildObservation(report(), 'prod')!;
    expect(o.status).toBe('degraded');
    expect(o.severity).toBe('warning');
  });

  it('is critical when any destructive-risk proposal exists', () => {
    const r = report({
      instances: {
        prod: {
          ok: true,
          summary: {
            total_proposals: 2,
            by_risk: { safe: 1, caution: 0, destructive: 1 },
            by_kind: { remediation: 2, question: 0 },
          },
          proposals: [],
        },
      },
    } as Partial<DriftReport>);
    const o = buildObservation(r, 'prod')!;
    expect(o.status).toBe('degraded');
    expect(o.severity).toBe('critical');
  });

  it('is unknown/warning when the instance is unreachable', () => {
    const r = report({
      instances: { prod: { ok: false, error: 'boom' } },
    } as Partial<DriftReport>);
    const o = buildObservation(r, 'prod')!;
    expect(o.status).toBe('unknown');
    expect(o.severity).toBe('warning');
    expect(o.summary).toBe('coolify:prod — instance unreachable');
  });

  it('returns null for an instance with no subject mapping', () => {
    const r = report({ instances: { staging: { ok: true } } } as Partial<DriftReport>);
    expect(buildObservation(r, 'staging')).toBeNull();
  });

  it('varies the source_reference when facts change but generated_at does not', () => {
    const a = buildObservation(report(), 'prod')!;
    const changed = report({
      instances: {
        prod: {
          ok: true,
          summary: {
            total_proposals: 5,
            by_risk: { safe: 5, caution: 0, destructive: 0 },
            by_kind: { remediation: 5, question: 0 },
          },
          proposals: [],
        },
      },
    } as Partial<DriftReport>);
    expect(buildObservation(changed, 'prod')!.source_reference).not.toBe(a.source_reference);
  });

  it('varies the source_reference and idempotency_key when generated_at changes', () => {
    const a = buildObservation(report(), 'prod')!;
    const b = buildObservation(report({ generated_at: '2026-07-28T07:00:07Z' }), 'prod')!;
    expect(b.source_reference).not.toBe(a.source_reference);
    expect(b.idempotency_key).not.toBe(a.idempotency_key);
  });

  it('is byte-identical for the same report, so a re-post dedups', () => {
    expect(JSON.stringify(buildObservation(report(), 'prod'))).toBe(
      JSON.stringify(buildObservation(report(), 'prod')),
    );
  });
});

describe('buildObservations', () => {
  it('emits one command per audited instance', () => {
    const os = buildObservations(report());
    expect(os.map((o) => o.subject_reference).sort()).toEqual(['coolify:dev', 'coolify:prod']);
  });

  it('skips unmapped instances rather than throwing', () => {
    const r = report({
      instances: {
        prod: {
          ok: true,
          summary: {
            total_proposals: 0,
            by_risk: { safe: 0, caution: 0, destructive: 0 },
            by_kind: { remediation: 0, question: 0 },
          },
          proposals: [],
        },
        staging: { ok: true },
      },
    } as Partial<DriftReport>);
    expect(buildObservations(r)).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/Projects/infraops-mcp-server && npx vitest run tests/orchestrator-observation.test.ts
```

Expected: FAIL — `Failed to resolve import "../src/orchestrator/observation.js"`.

- [ ] **Step 3: Write the normalizer**

Create `src/orchestrator/observation.ts`:

```typescript
/**
 * The drift digest's observation contract (WS-P3.0).
 *
 * TWO HALVES THAT MUST SHIP TOGETHER, or the producer wedges on its second post.
 *
 * The orchestrator dedups on `(source_system, source_reference, normalized_fact_hash)`, and
 * `observed_at` is INSIDE that fact hash. Re-recording the same source reference with different
 * facts is rejected as `observation_conflict`; there is no supersession. So:
 *
 *  * `source_reference` embeds the full upstream `generated_at` AND a digest of the facts. A new
 *    audit run has a new `generated_at`, so it appends a new row rather than conflicting; an
 *    identical re-post of the SAME report file is byte-identical and dedups on the idempotency key.
 *  * `observed_at` is the report's `generated_at` -- NEVER the post time. With a wall-clock post
 *    time, an unchanged re-post would produce the same reference but a different fact hash, which
 *    is precisely the conflict branch. Every run. Forever.
 *
 * `observed_at` is deliberately NOT duplicated inside `facts`: the reference already embeds
 * `generated_at`, so it varies whenever `observed_at` varies. Do not "fix" its absence.
 *
 * Facts are counts only. External text -- an unreachable instance's error, per-endpoint read
 * errors, proposal descriptions and reasoning -- never crosses this boundary, and rule keys are
 * never used as fact KEYS (the ingest secret scanner rejects any key containing "log", "body",
 * "credential", ...).
 */
import { createHash } from 'crypto';
import type { DriftReport } from '../standards/report.js';

export interface ObservationCommand {
  idempotency_key: string;
  expected_version: 0;
  source_system: 'drift_digest';
  source_reference: string;
  source_url: null;
  trust_classification: 'monitor';
  subject_type: 'service';
  subject_reference: string;
  environment: string;
  observation_type: 'drift';
  status: 'passed' | 'degraded' | 'unknown';
  severity: 'info' | 'warning' | 'critical';
  observed_at: string;
  summary: string;
  facts: Record<string, unknown>;
  payload_digest: null;
}

/**
 * Logical, stable subject identifiers. Deliberately NOT the instance base URLs: those come from
 * env at runtime and dev's is an OrbStack LAN address, so embedding one in a permanent record
 * would be both unstable and an internal-address leak.
 */
export const INSTANCE_SUBJECTS: Record<string, { subject: string; environment: string }> = {
  prod: { subject: 'coolify:prod', environment: 'production' },
  dev: { subject: 'coolify:dev', environment: 'development' },
};

/** Sorted-key, compact-separator JSON — the same recipe as the orchestrator's `fact_digest()`. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) =>
    a < b ? -1 : a > b ? 1 : 0,
  );
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(',')}}`;
}

/**
 * Our own content address for the facts. This is NOT the orchestrator's `normalized_fact_hash`
 * and is never compared against it — it only has to be deterministic on this side.
 */
export function factDigest(facts: Record<string, unknown>): string {
  return createHash('sha256').update(canonicalJson(facts), 'utf8').digest('hex').slice(0, 12);
}

function count(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

export function buildInstanceFacts(
  report: DriftReport,
  instance: string,
): Record<string, unknown> {
  const section = report.instances[instance];
  const reportDate = report.generated_at.slice(0, 10);
  const readErrorCount = Array.isArray(section?.errors) ? section.errors.length : 0;

  if (!section || section.ok !== true) {
    return {
      report_date: reportDate,
      instance,
      instance_ok: false,
      read_error_count: readErrorCount,
    };
  }

  const summary = section.summary;
  const byRisk = (summary?.by_risk ?? {}) as Record<string, unknown>;
  const byKind = (summary?.by_kind ?? {}) as Record<string, unknown>;

  return {
    report_date: reportDate,
    instance,
    instance_ok: true,
    total_proposals: count(summary?.total_proposals),
    by_risk: {
      safe: count(byRisk.safe),
      caution: count(byRisk.caution),
      destructive: count(byRisk.destructive),
    },
    by_kind: {
      remediation: count(byKind.remediation),
      question: count(byKind.question),
    },
    delta_new: report.delta.new.filter((d) => d.instance === instance).length,
    delta_resolved: report.delta.resolved.filter((d) => d.instance === instance).length,
    read_error_count: readErrorCount,
  };
}

function statusFor(facts: Record<string, unknown>): ObservationCommand['status'] {
  if (facts.instance_ok !== true) return 'unknown';
  return count(facts.total_proposals) > 0 ? 'degraded' : 'passed';
}

function severityFor(facts: Record<string, unknown>): ObservationCommand['severity'] {
  if (facts.instance_ok !== true) return 'warning';
  const byRisk = (facts.by_risk ?? {}) as Record<string, unknown>;
  if (count(byRisk.destructive) > 0) return 'critical';
  return count(facts.total_proposals) > 0 ? 'warning' : 'info';
}

function summaryFor(subject: string, facts: Record<string, unknown>): string {
  if (facts.instance_ok !== true) return `${subject} — instance unreachable`;
  const total = count(facts.total_proposals);
  const noun = total === 1 ? 'standards proposal' : 'standards proposals';
  return `${subject} — ${total} ${noun} (${count(facts.delta_new)} new)`.slice(0, 512);
}

export function buildObservation(
  report: DriftReport,
  instance: string,
): ObservationCommand | null {
  const mapping = INSTANCE_SUBJECTS[instance];
  if (!mapping) return null;

  const facts = buildInstanceFacts(report, instance);
  const observedAt = report.generated_at;

  return {
    idempotency_key: `drift-digest:${observedAt}:${instance}`,
    expected_version: 0,
    source_system: 'drift_digest',
    source_reference: `infra-drift:${observedAt}:${instance}:${factDigest(facts)}`,
    source_url: null,
    trust_classification: 'monitor',
    subject_type: 'service',
    subject_reference: mapping.subject,
    environment: mapping.environment,
    observation_type: 'drift',
    status: statusFor(facts),
    severity: severityFor(facts),
    observed_at: observedAt,
    summary: summaryFor(mapping.subject, facts),
    facts,
    payload_digest: null,
  };
}

/** One command per audited instance. Instances with no subject mapping are skipped, not thrown on. */
export function buildObservations(report: DriftReport): ObservationCommand[] {
  return Object.keys(report.instances)
    .map((instance) => buildObservation(report, instance))
    .filter((o): o is ObservationCommand => o !== null);
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd ~/Projects/infraops-mcp-server && npx vitest run tests/orchestrator-observation.test.ts && npm run build
```

Expected: all tests PASS, `tsc` exits 0.

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/infraops-mcp-server
git add src/orchestrator/observation.ts tests/orchestrator-observation.test.ts
git commit -m "feat(wsp30): normalize the drift digest into orchestrator observations

Pure normalizer: DriftReport -> one ObservationCommand per audited Coolify
instance. Content-addressed source_reference embedding the upstream
generated_at, so an unchanged re-post dedups and a new audit run appends
rather than hitting observation_conflict.

Facts are counts only -- instance error text and per-endpoint read errors are
counted, never carried -- and no fact key can contain a scanner-banned
substring."
```

---

## Task 2: The orchestrator API client

**Files:**
- Create: `~/Projects/infraops-mcp-server/src/orchestrator/api-client.ts`
- Test: `~/Projects/infraops-mcp-server/tests/orchestrator-api-client.test.ts`

**Interfaces:**
- Consumes: `ObservationCommand` (type only) from `./observation.js`.
- Produces:
  - `export interface ObservationResponse { id: string; source_reference: string; recorded_by: string; received_at: string; idempotency_key: string; }`
  - `export class OrchestratorClient` with `constructor(base: string, token: string, credentialKeyId: string)` and `postObservation(cmd: ObservationCommand): Promise<ObservationResponse>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/orchestrator-api-client.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { OrchestratorClient } from '../src/orchestrator/api-client.js';
import type { ObservationCommand } from '../src/orchestrator/observation.js';

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function ok(body: unknown) {
  return { ok: true, status: 201, json: async () => body, text: async () => JSON.stringify(body) };
}

const command: ObservationCommand = {
  idempotency_key: 'drift-digest:2026-07-27T07:00:07Z:prod',
  expected_version: 0,
  source_system: 'drift_digest',
  source_reference: 'infra-drift:2026-07-27T07:00:07Z:prod:abcdef123456',
  source_url: null,
  trust_classification: 'monitor',
  subject_type: 'service',
  subject_reference: 'coolify:prod',
  environment: 'production',
  observation_type: 'drift',
  status: 'degraded',
  severity: 'warning',
  observed_at: '2026-07-27T07:00:07Z',
  summary: 'coolify:prod — 1 standards proposal (1 new)',
  facts: { instance: 'prod' },
  payload_digest: null,
};

describe('OrchestratorClient', () => {
  it('posts the command with BOTH M2M headers and returns the response', async () => {
    fetchMock.mockResolvedValue(ok({ id: 'obs-1', recorded_by: 'drift-reconciler' }));
    const c = new OrchestratorClient('https://sds.example', 'tok', 'orchestrator-drift-reporter');

    const r = await c.postObservation(command);

    expect(r.recorded_by).toBe('drift-reconciler');
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('https://sds.example/api/v1/observations');
    expect(opts.method).toBe('POST');
    expect(opts.headers.Authorization).toBe('Bearer tok');
    expect(opts.headers['X-Credential-Key-Id']).toBe('orchestrator-drift-reporter');
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(opts.body)).toEqual(command);
  });

  it('sends an identifying User-Agent', async () => {
    fetchMock.mockResolvedValue(ok({ id: 'obs-1' }));
    const c = new OrchestratorClient('https://sds.example', 'tok', 'k');
    await c.postObservation(command);
    expect(fetchMock.mock.calls[0][1].headers['User-Agent']).toMatch(/infra-drift/);
  });

  it('strips a trailing slash from the base URL', async () => {
    fetchMock.mockResolvedValue(ok({ id: 'obs-1' }));
    const c = new OrchestratorClient('https://sds.example/', 'tok', 'k');
    await c.postObservation(command);
    expect(fetchMock.mock.calls[0][0]).toBe('https://sds.example/api/v1/observations');
  });

  it('throws with the status and a bounded body on a non-2xx response', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      text: async () => 'observation_conflict',
    });
    const c = new OrchestratorClient('https://sds.example', 'tok', 'k');
    await expect(c.postObservation(command)).rejects.toThrow(/409.*observation_conflict/);
  });

  it('never puts the bearer token in the thrown message', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 401, text: async () => 'unauthorized' });
    const c = new OrchestratorClient('https://sds.example', 'sup3r-s3cret', 'k');
    await expect(c.postObservation(command)).rejects.toThrow(
      expect.objectContaining({ message: expect.not.stringContaining('sup3r-s3cret') }),
    );
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/Projects/infraops-mcp-server && npx vitest run tests/orchestrator-api-client.test.ts
```

Expected: FAIL — `Failed to resolve import "../src/orchestrator/api-client.js"`.

- [ ] **Step 3: Write the client**

Create `src/orchestrator/api-client.ts`:

```typescript
import type { ObservationCommand } from './observation.js';

export interface ObservationResponse {
  id: string;
  source_reference: string;
  recorded_by: string;
  received_at: string;
  idempotency_key: string;
}

/**
 * Minimal client for the orchestrator's observation spine. Every M2M route requires BOTH the
 * bearer and the credential key id; sending one without the other authenticates as nobody.
 *
 * `sds.alobar.net` is not Cloudflare-proxied, so a default UA would work — the explicit one is
 * for attribution in access logs, not to get past a bot check.
 */
export class OrchestratorClient {
  private readonly base: string;

  constructor(
    base: string,
    private token: string,
    private credentialKeyId: string,
  ) {
    this.base = base.replace(/\/+$/, '');
  }

  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.token}`,
        'X-Credential-Key-Id': this.credentialKeyId,
        'Content-Type': 'application/json',
        'User-Agent': 'infra-drift-observer/1 (+infraops-mcp-server)',
        ...(init.headers ?? {}),
      },
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`orchestrator ${path} -> ${res.status}: ${body.slice(0, 200)}`);
    }
    return (await res.json()) as T;
  }

  postObservation(command: ObservationCommand): Promise<ObservationResponse> {
    return this.req<ObservationResponse>('/api/v1/observations', {
      method: 'POST',
      body: JSON.stringify(command),
    });
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd ~/Projects/infraops-mcp-server && npx vitest run tests/orchestrator-api-client.test.ts && npm run build
```

Expected: all tests PASS, `tsc` exits 0.

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/infraops-mcp-server
git add src/orchestrator/api-client.ts tests/orchestrator-api-client.test.ts
git commit -m "feat(wsp30): orchestrator observation API client

Thin fetch wrapper sending both required M2M headers (bearer +
X-Credential-Key-Id). Errors carry the status and a bounded body slice and
never the token."
```

---

## Task 3: The CLI

**Files:**
- Create: `~/Projects/infraops-mcp-server/src/cli/orchestrator-cli.ts`
- Test: `~/Projects/infraops-mcp-server/tests/orchestrator-cli.test.ts`

**Interfaces:**
- Consumes: `buildObservations` and `ObservationCommand` from `../orchestrator/observation.js`; `OrchestratorClient` from `../orchestrator/api-client.js`.
- Produces: `export function parseArgs(argv: string[]): Record<string, string | boolean>` and `export function makeClient(): OrchestratorClient` (exported for the env-guard test). No later task imports these.

Invoked as `node dist/cli/orchestrator-cli.js observe --report-dir <dir> --now <iso> [--dry-run]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/orchestrator-cli.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { parseArgs, makeClient } from '../src/cli/orchestrator-cli.js';

const saved = { ...process.env };
beforeEach(() => {
  delete process.env.ORCHESTRATOR_API_BASE;
  delete process.env.ORCHESTRATOR_M2M_TOKEN;
  delete process.env.ORCHESTRATOR_CREDENTIAL_KEY_ID;
});
afterEach(() => {
  process.env = { ...saved };
});

describe('orchestrator-cli parseArgs', () => {
  it('parses the subcommand and flags', () => {
    const a = parseArgs(['observe', '--report-dir', '/r', '--now', '2026-07-27T07:00:07Z']);
    expect(a.command).toBe('observe');
    expect(a['report-dir']).toBe('/r');
    expect(a.now).toBe('2026-07-27T07:00:07Z');
  });

  it('parses --dry-run as a boolean flag', () => {
    expect(parseArgs(['observe', '--dry-run'])['dry-run']).toBe(true);
  });
});

describe('orchestrator-cli makeClient', () => {
  it('throws when the base URL is missing', () => {
    process.env.ORCHESTRATOR_M2M_TOKEN = 'tok';
    expect(() => makeClient()).toThrow(/ORCHESTRATOR_API_BASE/);
  });

  it('throws when the token is missing', () => {
    process.env.ORCHESTRATOR_API_BASE = 'https://sds.example';
    expect(() => makeClient()).toThrow(/ORCHESTRATOR_M2M_TOKEN/);
  });

  it('builds a client when both are present', () => {
    process.env.ORCHESTRATOR_API_BASE = 'https://sds.example';
    process.env.ORCHESTRATOR_M2M_TOKEN = 'tok';
    expect(() => makeClient()).not.toThrow();
  });

  it('defaults the credential key id to orchestrator-drift-reporter', () => {
    process.env.ORCHESTRATOR_API_BASE = 'https://sds.example';
    process.env.ORCHESTRATOR_M2M_TOKEN = 'tok';
    const c = makeClient() as unknown as { credentialKeyId: string };
    expect(c.credentialKeyId).toBe('orchestrator-drift-reporter');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd ~/Projects/infraops-mcp-server && npx vitest run tests/orchestrator-cli.test.ts
```

Expected: FAIL — `Failed to resolve import "../src/cli/orchestrator-cli.js"`.

- [ ] **Step 3: Write the CLI**

Create `src/cli/orchestrator-cli.ts`:

```typescript
#!/usr/bin/env node
/**
 * Posts the day's drift digest to the orchestrator's observation spine (WS-P3.0).
 *
 *   node dist/cli/orchestrator-cli.js observe --report-dir /reports --now 2026-07-27T07:00:07Z
 *   node dist/cli/orchestrator-cli.js observe --report-dir /reports --dry-run
 *
 * Fail-open per instance: one instance failing to post never suppresses the other's row, and the
 * run always prints a counted summary line. Exits non-zero if any instance failed, so the caller
 * can log a WARN -- but the caller must keep that non-fatal: the drift loop is never hostage to
 * the orchestrator being reachable.
 */
import fs from 'fs';
import path from 'path';
import { OrchestratorClient } from '../orchestrator/api-client.js';
import { buildObservations } from '../orchestrator/observation.js';
import type { DriftReport } from '../standards/report.js';

export function parseArgs(argv: string[]): Record<string, string | boolean> {
  const args: Record<string, string | boolean> = {};
  if (argv[0] && !argv[0].startsWith('--')) args.command = argv[0];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next !== undefined && !next.startsWith('--')) {
      args[key] = next;
      i++;
    } else args[key] = true;
  }
  return args;
}

export function makeClient(): OrchestratorClient {
  const base = process.env.ORCHESTRATOR_API_BASE ?? '';
  const token = process.env.ORCHESTRATOR_M2M_TOKEN ?? '';
  const keyId = process.env.ORCHESTRATOR_CREDENTIAL_KEY_ID ?? 'orchestrator-drift-reporter';
  if (!base) throw new Error('ORCHESTRATOR_API_BASE must be set');
  if (!token) throw new Error('ORCHESTRATOR_M2M_TOKEN must be set');
  return new OrchestratorClient(base, token, keyId);
}

async function doObserve(reportDir: string, now: string, dryRun: boolean): Promise<void> {
  const date = now.slice(0, 10);
  const file = path.join(reportDir, `${date}.json`);
  const report = JSON.parse(fs.readFileSync(file, 'utf-8')) as DriftReport;
  const commands = buildObservations(report);

  if (dryRun) {
    for (const command of commands) {
      process.stdout.write(`${JSON.stringify(command, null, 2)}\n`);
    }
    process.stdout.write(`observations: would post ${commands.length} (dry run)\n`);
    return;
  }

  const client = makeClient();
  let posted = 0;
  let failed = 0;

  for (const command of commands) {
    try {
      const response = await client.postObservation(command);
      posted++;
      process.stdout.write(
        `observed ${command.subject_reference} -> id=${response.id} recorded_by=${response.recorded_by}\n`,
      );
    } catch (e) {
      failed++;
      process.stdout.write(
        `WARN: ${command.subject_reference} observation failed: ${e instanceof Error ? e.message : String(e)}\n`,
      );
    }
  }

  process.stdout.write(`observations: posted=${posted} failed=${failed} of ${commands.length}\n`);
  if (failed > 0) process.exitCode = 1;
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  const now = typeof args.now === 'string' ? args.now : new Date().toISOString();
  const reportDir = typeof args['report-dir'] === 'string' ? args['report-dir'] : undefined;
  if (args.command === 'observe') {
    if (!reportDir) throw new Error('observe requires --report-dir');
    await doObserve(reportDir, now, args['dry-run'] === true);
  } else {
    throw new Error(`unknown command: ${String(args.command)} (use observe)`);
  }
}

if (process.argv[1] && process.argv[1].endsWith('orchestrator-cli.js')) {
  main().catch((e) => {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(1);
  });
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd ~/Projects/infraops-mcp-server && npx vitest run tests/orchestrator-cli.test.ts && npm run build
```

Expected: all tests PASS, `tsc` exits 0.

- [ ] **Step 5: Prove the dry run works against the real report**

```bash
cd ~/Projects/infraops-mcp-server
node dist/cli/orchestrator-cli.js observe \
  --report-dir "$HOME/infra-drift/reports" \
  --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --dry-run
```

Expected: two full JSON commands printed (`coolify:prod`, `coolify:dev`) and `observations: would post 2 (dry run)`. If today's report file does not exist yet, pass `--now` set to the most recent report's date (`ls ~/infra-drift/reports/*.json | tail -1`).

Sanity-check by eye before continuing: `expected_version` is `0`, `observed_at` equals the report's `generated_at` (**not** the time you ran the command), and no fact key contains `log`.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/infraops-mcp-server
git add src/cli/orchestrator-cli.ts tests/orchestrator-cli.test.ts
git commit -m "feat(wsp30): orchestrator-cli observe — post the day's drift digest

Fail-open per instance with a counted summary line, --dry-run for local
inspection, and an env guard mirroring change-mgr-cli. Exits non-zero when any
instance failed so the caller can log a WARN without making the drift loop
hostage to the orchestrator."
```

---

## Task 4: Mint the reporter credential and roll it out (production ops)

No repository code changes. This task ends with production accepting the new credential.

**Files:**
- Modify: `~/Projects/infraops-mcp-server/.bws-secrets.toml` (regenerated, not hand-edited)

**Interfaces:**
- Produces: the BWS secret UUID for the reporter token, which Task 5 substitutes into `drift-audit.sh`. Record it in the task's commit message and in the closeout note.

**Run every step of this task in one shell session** — `$SECRET_ID`, `$TOKEN_HASH` and `$CONTAINER` are set in one step and read in later ones.

Prefer the infraops MCP for everything here: `mcp__infraops__vps_exec` for container commands and `coolify_list_app_envs` / `coolify_update_app_env` for env writes. The `ssh` forms below are the shape of the command, for reference — do not reach for raw SSH or the Coolify UI when an infraops tool covers the operation.

- [ ] **Step 1: Pre-flight — confirm the actor resolves in the bundle the RUNNING image carries**

Via `mcp__infraops__vps_exec` (instance `prod`):

```bash
CONTAINER=$(docker ps --format '{{.Names}}' | grep '^eqj5l7k705fhi12x9i74fqf0')
docker exec "$CONTAINER" python3 -c "
import json; b=json.load(open('/app/registry-bundle.json'))
ids=[a['agent_id'] for a in b['actors']]
print(b['source_revision'], len(ids), 'drift-reconciler' in ids)"
```

Expected: `65655ddf58b8f4401262f3192270515ef88b14f7 13 True`.

**If this prints `False`, STOP.** `_m2m_credentials` resolves `agent_id` against this image-baked bundle at startup and fails **closed** — writing the credential would produce a container that will not boot on the next restart, not a 401. Recovering means adding the actor to security-standards, bumping both fields of `security-standards.pin.toml`, rebuilding the image and redeploying, which is a different workstream. Escalate to Devon rather than proceeding.

- [ ] **Step 2: Generate the bearer, store it in BWS, and capture only its hash**

The token value must never be echoed, logged, or written to a tracked file.

```bash
cd ~/Projects/infraops-mcp-server
source ~/Projects/vps-backup/bws-token.sh
PROJECT_ID=$(bws project list --output json | python3 -c \
  "import sys,json;print([p['id'] for p in json.load(sys.stdin) if p['name']=='Ops / Platform'][0])")

TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
TOKEN_HASH="$(printf '%s' "$TOKEN" | shasum -a 256 | cut -d' ' -f1)"
SECRET_ID=$(bws secret create ORCHESTRATOR_DRIFT_REPORTER_TOKEN "$TOKEN" "$PROJECT_ID" \
  --output json | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
unset TOKEN

echo "SECRET_ID=$SECRET_ID"
echo "TOKEN_HASH=$TOKEN_HASH"
```

Expected: a UUID and a 64-char hex hash. Both are safe to display — Coolify stores only the hash. **Record `SECRET_ID`; Task 5 needs it.**

- [ ] **Step 3: Write `ORCHESTRATOR_M2M_CREDENTIALS` — credentials FIRST, alone**

Read the current value, merge in the new key **in process**, and write it back. `/envs` responses carry `real_value` for every variable including database URLs, so never pipe that response through ad-hoc shell tooling.

Using the infraops MCP: `coolify_list_app_envs` for uuid `eqj5l7k705fhi12x9i74fqf0`, locate `ORCHESTRATOR_M2M_CREDENTIALS`, parse its JSON, add:

```json
"orchestrator-drift-reporter": { "agent_id": "drift-reconciler", "token_hash": "<TOKEN_HASH>" }
```

then `coolify_update_app_env` with the merged JSON. If the PATCH returns 500 (it does intermittently on this app), fall back to deleting that env by its uuid and recreating it with the merged value.

**Do not touch `ORCHESTRATOR_M2M_ROLES` in this step.**

- [ ] **Step 4: Restart and verify the credential landed, from inside the container**

Via `mcp__infraops__vps_exec` (instance `prod`). Note this prints key ids, agent ids and a boolean — never a hash or a token:

```bash
docker exec "$CONTAINER" python3 -c "
import os,json
c=json.loads(os.environ['ORCHESTRATOR_M2M_CREDENTIALS'])
r=json.loads(os.environ.get('ORCHESTRATOR_M2M_ROLES','{}'))
print('credential key ids:', sorted(c))
print('agent ids:', {k: v['agent_id'] for k,v in c.items()})
print('role key ids:', sorted(r))
print('roles subset of credentials:', set(r) <= set(c))"
```

Expected: `orchestrator-drift-reporter` present in the credential key ids mapped to `drift-reconciler`, **absent** from the role key ids, and `roles subset of credentials: True`. Confirm the app is healthy: `curl -s -o /dev/null -w '%{http_code}\n' https://sds.alobar.net/health/ready` → `200`.

A credentials-only write is a healthy configuration — the credential simply has no role yet. This is why the order is not negotiable: `main.py` raises when `set(roles) ⊄ set(credentials)` and fails closed, so the reverse order is an outage.

- [ ] **Step 5: Write `ORCHESTRATOR_M2M_ROLES` — roles SECOND, alone**

Same read-merge-write, adding `"orchestrator-drift-reporter": "system"`. Restart.

- [ ] **Step 6: Verify the credential authenticates end to end**

```bash
source ~/Projects/vps-backup/bws-token.sh
TOK="$(bws secret get <SECRET_ID> --output json | python3 -c 'import sys,json;print(json.load(sys.stdin)["value"])')"
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOK" \
  -H "X-Credential-Key-Id: orchestrator-drift-reporter" \
  -H "User-Agent: infra-drift-observer/1" \
  https://sds.alobar.net/api/v1/observations
```

Expected: `200`. A `401` means the hash or key id is wrong; a `403` means the role did not land. Also re-run Step 4's in-container check and confirm `roles subset of credentials: True` and `/health/ready` is `200`.

- [ ] **Step 7: Regenerate the BWS manifest and commit**

```bash
cd ~/Projects/infraops-mcp-server
PYTHONPATH="$HOME/Projects/security-standards/src" python3 -m security_scan.cli generate-manifest .
git diff .bws-secrets.toml   # expect exactly one added entry
git add .bws-secrets.toml
git commit -m "chore(wsp30): register the orchestrator drift-reporter token in the BWS manifest

Secret <SECRET_ID> (ORCHESTRATOR_DRIFT_REPORTER_TOKEN). The bearer is fetched
at runtime by UUID; only sha256(bearer) is stored in Coolify."
```

If `generate-manifest` also wants to add the pre-existing unregistered `APPBRAIN_ACCESS_KEY` entry (`68733abe-682a-4597-b88f-b4750189a56a`), **leave it out of this commit** — it is unrelated manifest drift that predates this work and is backlogged in Task 7.

---

## Task 5: Wire the producer into the daily drift run

**Files:**
- Modify: `~/Projects/infraops-mcp-server/scripts/drift-audit.sh` (insert after line 98, the change-manager sync block)

**Interfaces:**
- Consumes: `SECRET_ID` from Task 4; `dist/cli/orchestrator-cli.js` from Task 3.
- Produces: nothing later tasks import.

- [ ] **Step 1: Insert the block**

In `scripts/drift-audit.sh`, immediately after the change-manager sync block (which ends with the `&& log "change-mgr sync ok" || log "WARN: change-mgr sync failed (non-fatal)"` line) and **before** the `# ── Best-effort: machine security-posture drift` comment, add:

```bash
# ── Best-effort: the day's digest → orchestrator observations (non-fatal) ───────
# WS-P3.0. Posts one observation per audited Coolify instance to the orchestrator's
# observation spine. Deliberately outside RC/RC_REMEDIATE: the drift loop is never
# hostage to the orchestrator being reachable, but a failure is always logged, never
# silent.
export ORCHESTRATOR_API_BASE="${ORCHESTRATOR_API_BASE:-https://sds.alobar.net}"
export ORCHESTRATOR_M2M_TOKEN="$(get_secret_by_id "${BWS_ORCHESTRATOR_OBS_SECRET_ID:-<SECRET_ID>}")"
export ORCHESTRATOR_CREDENTIAL_KEY_ID="${ORCHESTRATOR_CREDENTIAL_KEY_ID:-orchestrator-drift-reporter}"
node "$REPO/dist/cli/orchestrator-cli.js" observe --report-dir "$REPORT_DIR" --now "$NOW" >>"$LOG_FILE" 2>&1 \
  && log "orchestrator observation ok" || log "WARN: orchestrator observation failed (non-fatal)"
```

Substitute the real UUID from Task 4 for `<SECRET_ID>`.

- [ ] **Step 2: Verify the script still lints and the exit-code path is untouched**

```bash
cd ~/Projects/infraops-mcp-server
shellcheck --severity=warning scripts/drift-audit.sh
grep -n 'RC\b\|RC_REMEDIATE' scripts/drift-audit.sh | tail -5
```

Expected: shellcheck silent (exit 0), and the final `exit` line still depends only on `RC` and `RC_REMEDIATE` — the new block assigns neither.

- [ ] **Step 3: Run the whole script end to end**

```bash
cd ~/Projects/infraops-mcp-server && bash scripts/drift-audit.sh; echo "exit=$?"
tail -30 ~/Library/Logs/infra-drift.log
```

Expected in the log, all four present and in this order: the remediate rc line, `change-mgr sync ok`, `observed coolify:prod -> id=… recorded_by=drift-reconciler` plus `observations: posted=2 failed=0 of 2`, then `orchestrator observation ok`, then `security-drift run ok`. The script's exit code must match what it was before this change for the same drift state.

This run is also drill step 2 of Task 6 — record its output there.

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/infraops-mcp-server
git add scripts/drift-audit.sh
git commit -m "feat(wsp30): post the daily drift digest to the orchestrator

One added best-effort block after the change-manager sync. Touches neither RC
nor RC_REMEDIATE, so the script's exit code, the Healthchecks ping, the Resend
digest and the change-manager sync are unchanged. A failed post logs a WARN and
the loop continues."
```

**Rollback for this task**, if anything downstream misbehaves: revert this commit, or set
`BWS_ORCHESTRATOR_OBS_SECRET_ID` to an unset value so the token resolves empty and the CLI's env
guard fails the block harmlessly. Nothing in the drift loop or the orchestrator depends on the
observations being posted. To retire the credential itself, remove it from
`ORCHESTRATOR_M2M_ROLES` **first**, then `ORCHESTRATOR_M2M_CREDENTIALS` — the reverse of Task 4,
for the same fail-closed reason.

---

## Task 6: The verification drill

**Files:**
- Create: `~/docs/software-delivery-system/2026-07-27-wsp30-drill-evidence.md`

**Interfaces:** none. This task produces evidence, which Task 7 cites.

Two of the six runs are supposed to fail. Record the actual output of each — a drill whose evidence is a summary rather than output proves nothing.

**Run all six drills in one shell session:** `$SYS_TOK`, the `obs` helper, and `$GENERATED_AT` (set in drill 3) are read by later drills.

Set up a reusable read helper (never echo the token):

```bash
source ~/Projects/vps-backup/bws-token.sh
export SYS_TOK="$(bws secret get 221a48d5-3f29-4898-b300-b4820140c880 --output json \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["value"])')"
obs() {
  curl -s -H "Authorization: Bearer $SYS_TOK" -H "X-Credential-Key-Id: orchestrator-system" \
       -H "User-Agent: wsp30-drill/1" \
       "https://sds.alobar.net/api/v1/observations?source_system=drift_digest" \
    | python3 -c "
import sys,json
rows=json.load(sys.stdin)
print('count:', len(rows))
for r in rows:
    print(r['id'], r['subject_reference'], r['status'], r['severity'], r['observed_at'], 'by', r['recorded_by'])"
}
```

- [ ] **Step 1: Drill 1 — dry run**

```bash
cd ~/Projects/infraops-mcp-server
node dist/cli/orchestrator-cli.js observe --report-dir "$HOME/infra-drift/reports" \
  --now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --dry-run
obs
```

Expected: two commands printed; `obs` count unchanged from before the run (a dry run posts nothing).

- [ ] **Step 2: Drill 2 — first real post**

Use the full-script run from Task 5 Step 3, then:

```bash
obs
```

Expected: count increased by 2; both rows show `recorded_by drift-reconciler` — assert this explicitly, it is the entire point of the credential decision — with `coolify:prod` and `coolify:dev` as subjects. Record the two ids.

- [ ] **Step 3: Drill 3 — same-report re-run dedups**

```bash
cd ~/Projects/infraops-mcp-server
REPORT_DATE=$(ls ~/infra-drift/reports/*.json | tail -1 | xargs basename | sed 's/\.json$//')
GENERATED_AT=$(python3 -c "import json;print(json.load(open('$HOME/infra-drift/reports/$REPORT_DATE.json'))['generated_at'])")
source ~/Projects/vps-backup/bws-token.sh
ORCHESTRATOR_API_BASE=https://sds.alobar.net \
ORCHESTRATOR_M2M_TOKEN="$(bws secret get <SECRET_ID> --output json | python3 -c 'import sys,json;print(json.load(sys.stdin)["value"])')" \
  node dist/cli/orchestrator-cli.js observe --report-dir "$HOME/infra-drift/reports" --now "$GENERATED_AT"
obs
```

Expected: the CLI prints `posted=2 failed=0` again, but the returned `id=` values are **the same two ids** as drill 2, and `obs` count is **unchanged**. That is the dedup: the orchestrator returned the existing rows on the idempotency key.

If the count grew instead, the idempotency key is not deterministic — stop and fix the normalizer rather than continuing.

- [ ] **Step 4: Drill 4 — a second report appends without conflict**

```bash
cd ~/Projects/infraops-mcp-server
python3 - <<'PY'
import json, os, pathlib
src = sorted(pathlib.Path(os.path.expanduser('~/infra-drift/reports')).glob('*.json'))[-1]
report = json.loads(src.read_text())
report['generated_at'] = '2026-07-27T23:59:59Z'
out = pathlib.Path('/tmp/wsp30-drill/2026-07-27.json')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report))
print(out)
PY
source ~/Projects/vps-backup/bws-token.sh
ORCHESTRATOR_API_BASE=https://sds.alobar.net \
ORCHESTRATOR_M2M_TOKEN="$(bws secret get <SECRET_ID> --output json | python3 -c 'import sys,json;print(json.load(sys.stdin)["value"])')" \
  node dist/cli/orchestrator-cli.js observe --report-dir /tmp/wsp30-drill --now 2026-07-27T23:59:59Z
obs
```

Expected: `posted=2 failed=0` with **two new ids**, count up by 2, and **no `observation_conflict` anywhere in the output**. This is the failure mode the normalizer's docstring is about; if a 409 appears, the reference is not varying with `observed_at`.

- [ ] **Step 5: Drill 5 — existing outputs unchanged**

```bash
grep -E 'change-mgr sync|security-drift run|email|healthcheck|hc ping' ~/Library/Logs/infra-drift.log | tail -20
```

Expected: the change-manager sync line, the security-drift line, the Resend digest line and the Healthchecks success ping all present for the Task 5 Step 3 run, identical in form to runs preceding this workstream. Confirm the digest email arrived.

- [ ] **Step 6: Drill 6 — failure is non-fatal and visible**

```bash
cd ~/Projects/infraops-mcp-server
ORCHESTRATOR_API_BASE=https://sds.alobar.net ORCHESTRATOR_M2M_TOKEN=deliberately-invalid \
  node dist/cli/orchestrator-cli.js observe --report-dir "$HOME/infra-drift/reports" \
  --now "$GENERATED_AT"; echo "cli exit=$?"
obs
```

Expected: two `WARN: coolify:… observation failed: orchestrator /api/v1/observations -> 401` lines, `observations: posted=0 failed=2 of 2`, CLI exit `1`, and `obs` count unchanged. The failure is loud in the log and creates no partial state.

Then confirm the wrapper keeps it non-fatal — temporarily point the script's secret id at a nonexistent UUID and run the whole script:

```bash
BWS_ORCHESTRATOR_OBS_SECRET_ID=00000000-0000-0000-0000-000000000000 \
  bash scripts/drift-audit.sh; echo "script exit=$?"
tail -5 ~/Library/Logs/infra-drift.log
```

Expected: `WARN: orchestrator observation failed (non-fatal)` in the log, the script's exit code driven only by `RC`/`RC_REMEDIATE` (unchanged from drill 5's run), and the Healthchecks success ping still sent.

- [ ] **Step 7: Write the evidence file and commit**

Write `~/docs/software-delivery-system/2026-07-27-wsp30-drill-evidence.md` containing, verbatim, the command and output of all six drills, the two-plus-two observation ids, the in-container credential/roles verification from Task 4, and the pre-flight bundle check. `~/docs` is not a git repository — do not attempt to commit there.

---

## Task 7: Close the backlog item and capture what was learned

**Files:**
- Modify: `~/Projects/orchestrator/PROJECT.md` (the observation-tail backlog item, currently line 47)
- Modify: `~/Projects/orchestrator/CLAUDE.md` (append one invariant, **below** `<!-- code-standards:end -->`)
- Create: `~/docs/software-delivery-system/2026-07-27-wsp30-closeout-evidence.md`

- [ ] **Step 1: Close the backlog item**

Change the `- [ ] (P2) The WS-P2.6 traceability query observation tail…` item to `- [x]` and append to its text:

```
DONE 2026-07-27 (WS-P3.0): first external producer wired — `drift-audit.sh` posts one
observation per audited Coolify instance (`source_system=drift_digest`,
`observation_type=drift`, `subject_type=service`) under a dedicated
`orchestrator-drift-reporter` credential attributed to the `drift-reconciler` registry
actor. Six-run drill evidenced (first post, same-report dedup, second-report append with
no conflict, existing outputs unchanged, two negative paths).
**Caveat, deliberately not overclaimed:** these rows do NOT appear in the traceability
chain — `services/traceability.py` filters the observation hop on `subject_type="work_unit"`
— so exit criterion #6's observation node is still not exercised end to end. That needs
per-unit observations or a traceability change; tracked separately below.
Plan: docs/superpowers/plans/2026-07-27-wsp30-drift-digest-observation-producer.md.
Closeout: ~/docs/software-delivery-system/2026-07-27-wsp30-closeout-evidence.md
```

- [ ] **Step 2: Add the two follow-up backlog items**

Append to the `## Backlog` section:

```
- [ ] (P2) Exit criterion #6's observation node is still not exercised end to end: `services/traceability.py` filters the observation hop on `subject_type="work_unit"` and the unit id, so the WS-P3.0 drift observations (`subject_type=service`) never appear in a traceability chain. Decide between (a) teaching the traceability query to include environment- or service-scoped observations for a chain's deployment environment, or (b) accepting that only per-unit observations count and finding a producer that emits them. Blocks any claim that the observation tail is answerable from the traceability query. — added 2026-07-27
- [ ] (P3) Per-application drift observations and observation supersession. WS-P3.0 posts an append-shaped per-instance digest because day-over-day changes to the same finding would need supersession, which does not exist (`observation_conflict` rejects the same source reference with different facts). Once WS-P2.8 exists and supersession is specified, revisit emitting one observation per drifting application (`subject_reference` = the app), which is the shape that can become proposed work. — added 2026-07-27
```

Also add, to `~/Projects/infraops-mcp-server`'s own `PROJECT.md` backlog:

```
- [ ] (P3) `.bws-secrets.toml` is missing the `APPBRAIN_ACCESS_KEY` secret (`68733abe-682a-4597-b88f-b4750189a56a`) that `scripts/drift-audit.sh:62` consumes — manifest drift predating WS-P3.0. Regenerate the manifest and verify no other consumed UUID is unregistered. — added 2026-07-27
```

- [ ] **Step 3: Add the invariant to the orchestrator's CLAUDE.md**

Append to the `## Known Non-obvious Invariants` section — which must stay **below** `<!-- code-standards:end -->`, or `code-standards sync` silently deletes it:

```markdown
- **An M2M credential's `agent_id` is resolved against a registry bundle BAKED INTO THE IMAGE, and
  an unresolvable one is a boot failure, not a 401.** `_m2m_credentials` (`main.py:140`) calls
  `registry.resolve(agent_id)` at startup against `/app/registry-bundle.json`, built at image-build
  time from the security-standards tree at `security-standards.pin.toml`'s `revision`. So checking
  that an actor exists in git — even at exactly the pinned revision — does **not** establish that
  the running image carries it: the image may predate the pin. Ask production before writing the
  env var:
  `docker exec <container> python3 -c "import json;b=json.load(open('/app/registry-bundle.json'));print(b['source_revision'],[a['agent_id'] for a in b['actors']])"`.
  Getting this wrong fails **closed** on the next restart, which is the same outage shape as the
  WS-6.3 roles-before-credentials write. Verified 2026-07-27 (WS-P3.0) on image
  `8da4af3-wsp27inc2-amd64`: bundle revision `65655ddf…`, 13 actors, `drift-reconciler` present.
- **The traceability query's observation hop is unit-scoped, so most observation producers are
  invisible to it.** `services/traceability.py` filters observations on
  `subject_type="work_unit"` AND the unit id. An observation about a service, endpoint, monitor or
  environment — which is what every external monitor naturally produces — lands in
  `GET /api/v1/observations` and in nothing else. Do not treat "wired an observation producer" as
  "exercised the traceability chain's observation node"; WS-P3.0 wired the first producer and that
  node remains unexercised.
```

- [ ] **Step 4: Verify the CLAUDE.md edit survives a stanza re-render**

```bash
cd ~/Projects/orchestrator
grep -n 'code-standards:end' CLAUDE.md
grep -n 'agent_id.*resolved against a registry bundle' CLAUDE.md
```

Expected: the invariant's line number is **greater** than the `code-standards:end` line number.

- [ ] **Step 5: Write the closeout note**

Write `~/docs/software-delivery-system/2026-07-27-wsp30-closeout-evidence.md` covering: both decisions and their rationale, the pre-flight bundle check, the rollout order actually used, the six drill results with ids, the two scoped-out items and why, the follow-ups filed, and the production state at close (image tag, credential key id, observation count by source system).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/orchestrator
git add PROJECT.md CLAUDE.md
git commit -m "docs(wsp30): close the observation-tail item; capture two invariants

Closes the P2 observation-tail backlog item with the six-run drill evidence,
and files the two follow-ups it deliberately did not close: the traceability
hop is unit-scoped so exit criterion #6's observation node is still unexercised,
and per-application drift observations wait on supersession.

Invariants captured: an M2M credential's agent_id resolves against the
IMAGE-BAKED registry bundle (so a git check is not evidence, and a miss is a
boot failure rather than a 401), and the traceability observation hop is
unit-scoped."

cd ~/Projects/infraops-mcp-server && git add PROJECT.md && git commit -m "chore: backlog the .bws-secrets.toml APPBRAIN_ACCESS_KEY manifest gap"
```

---

## Definition of Done

- Tasks 1–3 committed in `infraops-mcp-server` with `npm run build && npm test` green.
- The reporter credential exists, is attributed to `drift-reconciler`, and authenticates against production (Task 4 Step 6 returns `200`).
- `drift-audit.sh` posts on every daily run, non-fatally, with a counted log line.
- All six drills evidenced verbatim, including the two negative paths.
- The change-manager sync, security-drift run, Resend digest, Healthchecks ping and script exit code are demonstrably unchanged.
- `PROJECT.md` item closed with its caveat; three follow-ups filed; two invariants captured.
- Closeout note written.
