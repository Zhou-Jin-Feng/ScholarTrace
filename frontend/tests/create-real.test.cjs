require('./register.cjs');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const { CreateTask } = require('../src/features/tasks/CreateTask.tsx');

test('explicit real selection creates an unapproved real task, default stays demo', async () => {
  const original = global.fetch;
  const calls = []; let tree;
  global.fetch = async (url, init) => {
    calls.push([url, JSON.parse(init.body)]);
    return new Response(JSON.stringify({ task_id: 'task:created' }), { status: 201 });
  };
  try {
    act(() => { tree = create(React.createElement(CreateTask, {
      token: () => null, onCreated: () => {},
    })); });
    assert.equal(tree.root.findByType('select').props.value, 'success');
    assert.ok(tree.root.findAllByType('option').some(o => o.props.value === 'real'));
    act(() => tree.root.findByType('textarea').props.onChange({ target: { value: 'Synthetic question' } }));
    act(() => tree.root.findByType('select').props.onChange({ target: { value: 'real' } }));
    await act(async () => tree.root.findByType('form').props.onSubmit({ preventDefault() {} }));
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], '/api/v1/research/tasks');
    assert.equal(calls[0][1].execution_mode, 'real');
    assert.equal(calls[0][1].authorization, undefined);
  } finally { if (tree) act(() => tree.unmount()); global.fetch = original; }
});
