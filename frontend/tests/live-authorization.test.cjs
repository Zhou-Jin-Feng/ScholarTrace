require('./register.cjs');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const { LiveAuthorization } = require('../src/features/plans/LiveAuthorization.tsx');

const estimate = { production_composed: true, policy_sha256: 'a'.repeat(64), runtime_policy: {
  remote: { model: 'synthetic', model_version: 'v1', endpoint: 'https://model.invalid/v1/chat/completions',
    input_cny_per_million: '1', output_cny_per_million: '2' },
  local: { model: 'local', endpoint: 'http://localhost:11434/api/chat' },
  documind_url: 'http://localhost:8000', data_fields: ['question'],
  allowed_search_providers: ['arxiv'], max_papers: 3,
} };
const response = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});
const token = () => null;
const check = tree => tree.root.findByProps({ type: 'checkbox' });
const submit = tree => tree.root.findByType('form').props.onSubmit({ preventDefault() {} });

test('live planning requires review, edits invalidate consent, retry retains original grant', async () => {
  const original = global.fetch;
  const calls = []; let tree, pending;
  global.fetch = async (url, init = {}) => {
    calls.push([url, init]);
    if (url.endsWith('/estimate')) return response(estimate);
    if (url.endsWith('/acknowledge-cost')) return response({});
    return response({ detail: 'synthetic planning failure' }, 409);
  };
  try {
    await act(async () => { tree = create(React.createElement(LiveAuthorization, {
      token, taskId: 'task:first', busy: false,
      run: work => { pending = work().catch(() => {}); },
    })); });
    act(() => submit(tree));
    assert.equal(calls.length, 1);
    act(() => check(tree).props.onChange({ target: { checked: true } }));
    const amount = tree.root.findAllByProps({ type: 'number' })[0];
    act(() => amount.props.onChange({ target: { value: '1' } }));
    assert.equal(check(tree).props.checked, false);
    act(() => check(tree).props.onChange({ target: { checked: true } }));
    await act(async () => { submit(tree); await pending; });
    await act(async () => { submit(tree); await pending; });
    const grants = calls.filter(([u]) => u.endsWith('/acknowledge-cost'))
      .map(([, init]) => JSON.parse(init.body).authorization);
    assert.equal(grants.length, 2);
    assert.deepEqual(grants[0], grants[1]);
    assert.equal(grants[0].policy_sha256, estimate.policy_sha256);
    assert.equal(grants[0].planning.max_external_requests, 0);
    assert.equal(calls.filter(([u]) => u.endsWith('/approve')).length, 0);
  } finally { if (tree) act(() => tree.unmount()); global.fetch = original; }
});

test('live execution refuses excessive allowance and binds the reviewed plan', async () => {
  const original = global.fetch; const calls = []; let tree, pending;
  global.fetch = async (url, init = {}) => {
    calls.push([url, init]); return response(url.endsWith('/estimate') ? estimate : {});
  };
  const plan = { plan_version: 3, plan_digest: 'b'.repeat(64),
    budget_plan: { max_cny: 1, max_api_calls: 2 } };
  try {
    await act(async () => { tree = create(React.createElement(LiveAuthorization, {
      token, taskId: 'task:second', plan, busy: false, run: work => { pending = work(); },
    })); });
    const amount = () => tree.root.findAllByProps({ type: 'number' })[0];
    act(() => amount().props.onChange({ target: { value: '2' } }));
    act(() => check(tree).props.onChange({ target: { checked: true } }));
    act(() => submit(tree));
    assert.equal(calls.length, 1);
    act(() => amount().props.onChange({ target: { value: '1' } }));
    act(() => check(tree).props.onChange({ target: { checked: true } }));
    await act(async () => { submit(tree); await pending; });
    const approval = JSON.parse(calls.find(([u]) => u.endsWith('/approve'))[1].body);
    assert.equal(approval.plan_version, 3);
    assert.equal(approval.plan_digest, plan.plan_digest);
    assert.equal(approval.execution_authorization.policy_sha256, estimate.policy_sha256);
    assert.equal(calls.filter(([u]) => u.endsWith('/generate')).length, 0);
  } finally { if (tree) act(() => tree.unmount()); global.fetch = original; }
});


test('refreshed planning reuses the persisted authorization without extending deadline', async () => {
  const originalFetch = global.fetch;
  const saved = { policy_sha256: estimate.policy_sha256, max_cny: '1', max_remote_calls: 10,
    max_local_calls: 10, max_external_requests: 30, max_wall_clock_seconds: 900,
    deadline_at: new Date(Date.now() + 300000).toISOString(), generation: 1,
    planning: { max_cny: '0.2', max_remote_calls: 1, max_local_calls: 0, max_external_requests: 0 } };
  let tree, pending; const calls = [];
  global.fetch = async (url, init = {}) => {
    calls.push([url, init]);
    if (url.endsWith('/estimate')) return response({ ...estimate,
      has_task_authorization: true, planning_authorization: saved });
    return response({});
  };
  try {
    await act(async () => { tree = create(React.createElement(LiveAuthorization, {
      token, taskId: 'task:restored', busy: false, run: work => { pending = work(); },
    })); });
    assert.equal(check(tree).props.checked, false);
    assert.equal(calls.length, 1);
    assert.ok(tree.root.findAllByType('fieldset').some(n => n.props.disabled));
    act(() => check(tree).props.onChange({ target: { checked: true } }));
    await act(async () => { submit(tree); await pending; });
    const ack = calls.find(([url]) => url.endsWith('/acknowledge-cost'));
    assert.deepEqual(JSON.parse(ack[1].body).authorization, saved);
  } finally { if (tree) act(() => tree.unmount()); global.fetch = originalFetch; }
});
