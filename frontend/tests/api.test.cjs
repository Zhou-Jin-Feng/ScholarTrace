require('./register.cjs');
const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const { apiRequest } = require('../src/api/client.ts');
const { generatePlan, approvePlan } = require('../src/api/endpoints.ts');
const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; });

test('FastAPI nested stale-plan error preserves code, message and request ID', async () => {
  global.fetch = async () => new Response(JSON.stringify({ detail: {
    code: 'plan_version_stale', detail: 'Read the current plan',
  } }), { status: 409, headers: { 'X-Request-ID': 'request:test' } });
  await assert.rejects(apiRequest('/example', () => null), error => {
    assert.equal(error.isStalePlan, true);
    assert.equal(error.message, 'Read the current plan');
    assert.equal(error.requestId, 'request:test');
    return true;
  });
});

test('string, validation-array, and non-JSON errors stay human-readable', async () => {
  for (const [body, message] of [
    [JSON.stringify({ detail: 'not found' }), 'not found'],
    [JSON.stringify({ detail: [{ loc: ['body', 'action'], msg: 'Required' }] }), 'body.action: Required'],
    ['<html>server failure</html>', 'Request failed with status 422'],
  ]) {
    global.fetch = async () => new Response(body, { status: 422 });
    await assert.rejects(apiRequest('/example', () => null), e => e.message === message);
  }
});

test('generation acknowledges Gate A before generate and sends no invented token', async () => {
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push([url, options]);
    return new Response(JSON.stringify({ plan_version: 1 }));
  };
  await generatePlan(() => null, 'task:test', 0);
  assert.deepEqual(calls.map(c => c[0]), [
    '/api/v1/research/tasks/task:test/plan/acknowledge-cost',
    '/api/v1/research/tasks/task:test/plan/generate',
  ]);
  assert.deepEqual(JSON.parse(calls[0][1].body), { acknowledged_max_cny: 0 });
  assert.equal(calls[1][1].body, undefined);
});

test('failed cost acknowledgement never generates a plan', async () => {
  let calls = 0;
  global.fetch = async () => { calls++; return new Response('{}', { status: 409 }); };
  await assert.rejects(generatePlan(() => null, 'task:test', 0));
  assert.equal(calls, 1);
});

test('modify sends reviewed content without renaming modified_plan', async () => {
  let sent;
  global.fetch = async (_, options) => { sent = JSON.parse(options.body); return new Response('{}'); };
  const payload = { action: 'modify', plan_version: 1, plan_digest: 'a'.repeat(64),
    modified_plan: { sub_questions: ['revised'], exclusions: [],
      source_scope: { providers: ['arxiv'], year_from: 2020, year_to: 2025, min_papers: 3, max_papers: 5 },
      budget_plan: { max_cny: 0, max_api_calls: 0, max_wall_clock_seconds: 60, estimate_source: 'fixture', is_actual_bill: false } } };
  await approvePlan(() => null, 'task:test', payload);
  assert.deepEqual(sent, payload);
});
