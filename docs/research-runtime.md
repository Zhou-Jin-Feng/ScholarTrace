# Research runtime and output contracts

The delivery service separates the original deterministic UI demo, injected offline
research, and the disabled live execution route. Offline research is explicitly
labelled `offline_fixture`; it is engineering evidence, not a semantic-quality result.
There is no automatic model fallback or credential discovery in the research assembly.

## Bounded assembly

`ResearchPipeline` composes the existing search normalization/ranking, versioned
arXiv acquisition, DocuMind 3.0.0 ingestion and binding repository, M2 retrieval and
analysis, M4 validation/independent verification, and structured report generator.
The exact approved plan version/digest, providers, year range, exclusions and 3–5
paper boundary are checked. All retrieval finishes before generation starts.

Optional OpenAlex citation expansion is one round, at most five discovered
candidates, and requires OpenAlex in the approved scope. Discovered candidates
are normalized and relevance-filtered before access resolution. Selected papers
follow acquisition → ingestion → analysis lifecycle gates. An edge alone is not
full-text evidence. ScholarGraph remains disabled. M4 follow-up requests are
returned as proposals; they do not trigger an unapproved extra run.

Unsupported claims are excluded from synthesis input. Partial/conflicted claims
retain deterministic visible markers in the final report. Missing evidence does
not become a successful answer. Missing subquestion attribution is returned as
null rather than assigned to an arbitrary subquestion.

## Persistence and recovery

The runtime uses separate task metadata, business artifacts, event ledger and
research-effect journal files. It is a single-process, single-user deployment;
multiple delivery processes must not share a data directory.

Every journaled operation commits its intent before dispatch. Its validated result
and measured usage commit together. Completed operations replay from the journal;
pending/unknown outcomes retain exposure and are not automatically reissued.
Only explicitly confirmed **before-dispatch** failures allow another attempt,
with a maximum of two. Cancelling an in-flight operation does not prove its
external effect or fee was cancelled.

Startup marks abandoned queued/running/cancelling tasks `interrupted`, without
replaying them. Explicit fixture recovery requires the previously approved plan
version/digest and allows at most two resume requests. Unknown operations still
refuse replay. Live recovery and automatic provider reconciliation are disabled.
Completed export artifacts are retained rather than overwritten on recovery.

## Call accounting

`MeteredModelTransport` can wrap the existing strong-model HTTP adapters. The
composition supplies an approved HTTPS endpoint, exact model, reference prices,
input/output ceilings and task-wide cost/call envelope. It uses SQLite write
transactions to reserve before HTTP dispatch across concurrent connections.
Successful usage and response replay are durable; absent/invalid usage, uncertain
timeouts, redirects and non-success responses hold the reservation. Reference
usage is **not a provider bill**. Provider overruns require reconciliation and
must not be represented as costs below their known exposure.

This transport is verified with synthetic HTTP responses only. It does not enable
the default live executor. Live Coordinator/Verifier/Synthesis composition,
approved data scope and provider billing reconciliation remain
prerequisites before real execution. No API key should be stored in a journal.
Runtime response bodies belong only in ignored, access-controlled local data.

### Explicit authorization and journal projections

A server-injected `RuntimePolicy` defines provider identities, reference prices,
limits and permitted data fields. Real Gate A requires an `authorization` payload
of type `PlanningAuthorization`:
the policy digest, immutable task envelope and a planning phase allowance. Gate B
requires `execution_authorization` bound to the current plan version/digest. The
old amount-only payload remains valid for Demo, but cannot authorize real calls.
Policy availability and a healthy provider do not constitute task approval.

The original effect tables retain their layout. Four additive tables track the
effect schema version, task grants, phase grants and operation contexts. This
effect schema version is independent of task database schema 4. Phase preparation
is pending until persistence and approval audit succeed; incomplete cross-database
work cannot dispatch. Changing a phase generation does not replenish its allowance
or extend the task deadline. Remote, local and non-model requests have separate
counters, with task and phase ceilings checked in the same intent transaction.

The effect journal remains authoritative. Budget responses expose
`journal_accounting`, nullable `measured_usage`, and `projection_state`.
Pending projection or uncertain effects must not appear as a confirmed zero bill.
Known overrun usage remains recorded; uncertain exposure is retained. Reprojecting
confirmed usage is idempotent and never invokes the provider again. These transport
and approval primitives do not yet enable the complete live research pipeline.

### Explicit dependency checks

`POST /api/v1/health/dependencies/probe` follows the deployment authentication
policy and requires a server-injected runtime policy. It checks metadata only:
remote `/v1/models`, Ollama `/api/tags`, and DocuMind `/api/v1/health/ready`.
The default limits are one request per target, two seconds per target, 256 KiB
per response, no redirects, and a 30-second cache. Concurrent refreshes share the
existing refresh; ordinary dependency reads use cached facts without HTTP.

The API uses a fresh client without environment proxies or provider credentials.
A protected metadata endpoint may therefore report access denied. This is not
proof that generation is unavailable. The model list checks the configured model
name, not an immutable deployment digest or the correctness of reference prices.
DocuMind must report version 3.0.0 and retrieval readiness; overall HTTP 503 is
acceptable only when that retrieval component is ready. Search stays `unknown`
because no approved side-effect-free metadata contract is available. Expired facts
return to `unknown`; metadata success always leaves task `approved` false.
Draining refuses new checks. None of these probes generates text, loads a model,
uploads papers, or establishes production execution readiness by itself.

## Read and recovery endpoints

All endpoints use the existing `/api/v1/research/tasks/{task_id}` prefix and
deployment authentication policy:

- `GET /research-result`: structured persisted research; 409 if absent.
- `GET /claims`: included/excluded claims and actual verification metadata;
  409 if no research artifact exists, not fabricated empty success.
- `GET /evidence/{evidence_id}`: verbatim excerpt, provenance and nullable
  location fields. Cross-task or missing evidence returns 404.
- `GET /timeline`: persisted task events, including actual completed research
  stages. `occurred_at` distinguishes original stage time from delivery import time.
- `POST /resume`: exact `plan_version` and `plan_digest`; explicit interrupted
  offline task recovery only. It does not create new approval or raise budgets.
- `GET /report?format=...`: existing export interface; research Markdown/JSON/HTML
  use the actual persisted result, not the old demo's fixed counts.

The default demo still has no evidence graph. The browser workbench and Chinese
multipage PDF export are implemented and covered by local synthetic validation.
That evidence does not establish live research quality, all-client compatibility,
font embedding, or PDF/A conformance. No new release version is assigned here.
