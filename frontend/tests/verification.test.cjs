require('./register.cjs');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const React = require('react');
const TestRenderer = require('react-test-renderer');
const { VerificationBadge } = require('../src/components/VerificationBadge.tsx');

function view(verifierKind, status = 'supported') {
  const renderer = TestRenderer.create(React.createElement(VerificationBadge, { status, verifierKind }));
  const text = JSON.stringify(renderer.toJSON());
  renderer.unmount();
  return text;
}
test('fixture and deterministic provenance do not claim independent semantic verification', () => {
  for (const kind of ['fixture', 'deterministic']) {
    assert.ok(view(kind).includes('未核验'));
    assert.ok(!view(kind).includes('已核验'));
  }
});
test('actual backend model/human provenance retains verification status', () => {
  for (const kind of ['model', 'human']) {
    assert.ok(view(kind).includes('已核验'));
    assert.ok(view(kind, 'unsupported').includes('不支持'));
  }
});
