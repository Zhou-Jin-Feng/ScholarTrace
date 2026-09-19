// Explicit localhost-only offline acceptance server; not part of default npm test.
require('./register.cjs');
const assert = require('node:assert/strict');
const api = require('../src/api/endpoints.ts');
const { EVENT_KINDS } = require('../src/hooks/useTaskEvents.ts');
const base = new URL(process.env.SCHOLARTRACE_TEST_BASE_URL);
assert.equal(base.hostname, '127.0.0.1');
const originalFetch = global.fetch;
global.fetch = (url, options) => originalFetch(new URL(url, base), options);
const token = () => null;

async function main() {
  const created = await global.fetch('/api/v1/research/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: 'What determines retrieval?', execution_mode: 'demo' }),
  });
  assert.equal(created.status, 201);
  const task = await created.json();
  await assert.rejects(api.getClaims(token, task.task_id), e => e.status === 409);
  const plan = await api.generatePlan(token, task.task_id, 0);
  const done = await api.approvePlan(token, task.task_id, {
    action: 'approve', plan_version: plan.plan_version, plan_digest: plan.plan_digest,
  });
  assert.equal(done.status, 'degraded');
  assert.equal(done.metrics.execution_mode, 'offline_fixture');
  const claims = await api.getClaims(token, task.task_id);
  assert.equal(claims.claims.length, 3);
  assert.equal(claims.excluded_count, 1);
  const statuses = new Set(claims.claims.map(c => c.verification.status));
  assert.deepEqual(statuses, new Set(['supported', 'partially_supported', 'unsupported']));
  const evidence = await api.getEvidence(token, task.task_id, claims.claims[0].evidence_ids[0]);
  assert.equal(evidence.binding.documind_version, '3.0.0');
  assert.equal(evidence.excerpt_is_verbatim, true);
  assert.ok(evidence.excerpt.length > 0);
  assert.equal(evidence.inference_note, null);
  assert.ok(claims.claims.every(c => c.sub_question === null));
  const response = await global.fetch(`/api/v1/research/tasks/${task.task_id}/events`);
  const kinds = [...(await response.text()).matchAll(/^event: (.+)$/gm)].map(m => m[1]);
  for (const kind of kinds) assert.ok(EVENT_KINDS.includes(kind), `unhandled event: ${kind}`);
  assert.ok(kinds.includes('retrieval_completed'));
  assert.ok(kinds.includes('exports_ready'));
  console.log('PASS: TypeScript ↔ isolated offline research; verified claims, evidence provenance, actual stage SSE');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
