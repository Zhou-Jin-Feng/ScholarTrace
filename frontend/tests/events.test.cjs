require('./register.cjs');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const { create, act } = require('react-test-renderer');
const { useTaskEvents } = require('../src/hooks/useTaskEvents.ts');

class FakeEventSource {
  static instances = [];
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; FakeEventSource.instances.push(this); }
  addEventListener(kind, listener) { (this.listeners[kind] ??= []).push(listener); }
  close() { this.closed = true; }
  emit(kind, payload) {
    const event = { data: typeof payload === 'string' ? payload : JSON.stringify(payload) };
    if (kind === 'message') this.onmessage?.(event);
    for (const listener of this.listeners[kind] ?? []) listener(event);
  }
}
function event(sequence, kind) {
  return { event_id: `event:${sequence}`, task_id: 'task:test', sequence, kind,
    node: 'delivery', created_at: '2026-09-16', payload: {} };
}

test('real React hook consumes named events, deduplicates, resumes and clears on task change', () => {
  const oldSource = global.EventSource, oldWindow = global.window;
  global.EventSource = FakeEventSource;
  global.window = global;
  let result, renderer;
  const token = () => null;
  function Harness({ id, access = token }) { result = useTaskEvents(id, access); return null; }
  try {
    act(() => { renderer = create(React.createElement(Harness, { id: 'task:test' })); });
    const source = FakeEventSource.instances.at(-1);
    act(() => { source.emit('search_completed', event(1, 'search_completed')); });
    assert.equal(result.events.length, 1);
    act(() => { source.emit('search_completed', event(1, 'search_completed')); });
    assert.equal(result.events.length, 1);
    act(() => { renderer.update(React.createElement(Harness, { id: 'task:test', access: () => null })); });
    assert.equal(FakeEventSource.instances.at(-1), source, 'inline token accessor must not reconnect');
    act(() => { source.onerror(); });
    assert.equal(result.status, 'reconnecting');
    assert.equal(result.events.length, 1);
    act(() => { result.retryNow(); });
    const resumed = FakeEventSource.instances.at(-1);
    assert.ok(resumed.url.includes('last_event_id=event%3A1'));
    act(() => { resumed.emit('exports_ready', event(2, 'exports_ready')); });
    assert.equal(result.isTerminal, true);
    assert.equal(resumed.closed, true);
    act(() => { renderer.update(React.createElement(Harness, { id: null })); });
    assert.equal(result.events.length, 0);
    assert.equal(result.isTerminal, false);
    assert.equal(result.lastSequence, null);
    assert.equal(result.status, 'idle');
    act(() => { source.emit('exports_ready', event(99, 'exports_ready')); });
    assert.equal(result.events.length, 0, 'stale callbacks must be ignored');
  } finally {
    if (renderer) act(() => renderer.unmount());
    global.EventSource = oldSource; global.window = oldWindow;
  }
});

test('stream_end closes a resumed terminal stream without inventing an event', () => {
  const oldSource = global.EventSource, oldWindow = global.window;
  global.EventSource = FakeEventSource; global.window = global;
  let result, renderer;
  const token = () => null;
  function Harness() { result = useTaskEvents('task:test', token); return null; }
  try {
    act(() => { renderer = create(React.createElement(Harness)); });
    const source = FakeEventSource.instances.at(-1);
    act(() => { source.emit('message', '{}'); source.emit('message', 'invalid JSON'); });
    assert.equal(result.events.length, 0);
    act(() => { source.emit('stream_end', { reason: 'terminal' }); });
    assert.equal(result.isTerminal, true);
    assert.equal(result.events.length, 0);
    assert.equal(result.status, 'closed');
  } finally {
    if (renderer) act(() => renderer.unmount());
    global.EventSource = oldSource; global.window = oldWindow;
  }
});

test('retry attempts are bounded and terminal export failure closes without reconnecting', () => {
  const oldSource = global.EventSource, oldWindow = global.window;
  global.EventSource = FakeEventSource;
  const pending = new Map(); let nextId = 0;
  global.window = {
    setTimeout(fn) { pending.set(++nextId, fn); return nextId; },
    clearTimeout(id) { pending.delete(id); },
  };
  let result, renderer;
  const token = () => null;
  function Harness() { result = useTaskEvents('task:test', token); return null; }
  try {
    act(() => { renderer = create(React.createElement(Harness)); });
    for (let i = 0; i < 6; i++) {
      act(() => { FakeEventSource.instances.at(-1).onerror(); });
      if (i < 5) {
        assert.equal(result.attempt, i + 1);
        assert.equal(pending.size, 1);
        const callback = [...pending.values()][0]; pending.clear();
        act(() => callback());
      }
    }
    assert.equal(result.status, 'error');
    assert.equal(pending.size, 0);
    act(() => { result.retryNow(); });
    const source = FakeEventSource.instances.at(-1);
    act(() => { source.emit('task_failed', event(1, 'task_failed')); });
    assert.equal(result.isTerminal, false, 'failure precedes export finalization');
    act(() => { source.emit('task_terminal_no_exports', event(2, 'task_terminal_no_exports')); });
    assert.equal(result.isTerminal, true);
    assert.equal(source.closed, true);
    act(() => { source.onerror(); result.retryNow(); });
    assert.equal(FakeEventSource.instances.at(-1), source);
    assert.equal(pending.size, 0);
  } finally {
    if (renderer) act(() => renderer.unmount());
    global.EventSource = oldSource; global.window = oldWindow;
  }
});
