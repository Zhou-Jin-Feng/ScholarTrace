// Runs only against an explicitly supplied local, synthetic acceptance server.
require('./register.cjs');
const assert = require('node:assert/strict');
const api = require('../src/api/endpoints.ts');
const { EVENT_KINDS } = require('../src/hooks/useTaskEvents.ts');
const base = new URL(process.env.SCHOLARTRACE_TEST_BASE_URL);
assert.equal(base.hostname, '127.0.0.1');
const fetch = global.fetch;
global.fetch = (url, options) => fetch(new URL(url, base), options);
const token = () => null;

async function main() {
  const response = await global.fetch('/api/v1/research/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: 'Offline transport contract check', execution_mode: 'demo' }),
  });
  assert.equal(response.status, 201);
  const task = await response.json();
  const plan = await api.generatePlan(token, task.task_id, 0);
  assert.equal(plan.generated_by, 'fixture');
  const edit = Object.fromEntries(['sub_questions', 'source_scope', 'exclusions', 'budget_plan'].map(k => [k, plan[k]]));
  edit.sub_questions = ['Changed scope requires a new review'];
  const modified = await api.approvePlan(token, task.task_id, {
    action: 'modify', plan_version: plan.plan_version, plan_digest: plan.plan_digest,
    modified_plan: edit,
  });
  assert.equal(modified.status, 'waiting_approval');
  const current = await api.getPlan(token, task.task_id);
  assert.equal(current.plan_version, 2);
  assert.deepEqual(current.sub_questions, edit.sub_questions);
  await assert.rejects(api.approvePlan(token, task.task_id, {
    action: 'approve', plan_version: plan.plan_version, plan_digest: plan.plan_digest,
  }), e => e.isStalePlan && typeof e.message === 'string');
  const before = await api.getBudget(token, task.task_id);
  assert.equal(before.reservation, null);
  await api.approvePlan(token, task.task_id, {
    action: 'approve', plan_version: current.plan_version, plan_digest: current.plan_digest,
  });
  // Poll only this synthetic demo, bounded; not a model or provider invocation.
  let budget;
  for (let i = 0; i < 30; i++) {
    budget = await api.getBudget(token, task.task_id);
    if (budget.reservation?.state === 'settled') break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(budget.reservation.state, 'settled');
  assert.equal(budget.reservation.settled.cny, 0);
  assert.equal(budget.measured_usage.api_calls, 0);
  assert.equal(budget.is_actual_bill, false);
  const page = await api.listTasks(token);
  assert.equal(page.total_known, 1);
  assert.equal(typeof page.items[0].event_count, 'number');
  assert.equal('paper_count' in page.items[0], false);
  const stream = await global.fetch(`/api/v1/research/tasks/${task.task_id}/events`);
  const events = [...(await stream.text()).matchAll(/^event: (.+)$/gm)].map(m => m[1]);
  assert.ok(events.includes('exports_ready'));
  for (const kind of events) assert.ok(EVENT_KINDS.includes(kind), `unhandled named event: ${kind}`);
  const health = await api.getDependencies(token);
  assert.equal(health.dependencies.api_strong.state, 'disabled');
  console.log('PASS: actual TypeScript wrappers ↔ local FastAPI; two gates, modify, stale error, budget, list, named SSE');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
