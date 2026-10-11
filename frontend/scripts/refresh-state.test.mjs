import assert from 'node:assert/strict';
import test from 'node:test';
import { semanticIndex, semanticOccurrence } from '../src/refresh-state.ts';

test('refresh identity stays on the same duplicate trace control when unrelated controls or new events appear', () => {
  const link = 'payment-section/event-1/link-same-url';
  const keys = ['close', link, link, 'download-section/event-1/link-same-url'];
  const saved = semanticOccurrence(keys, 2);
  assert.deepEqual(saved, { key: link, occurrence: 1 });
  assert.equal(semanticIndex(['close', 'new-event/link-same-url', ...keys.slice(1)], saved), 3);
  assert.equal(semanticIndex(['close', 'download-section/event-1/link-same-url'], saved), -1);
});

test('scoped disclosure identities preserve one event record without opening or focusing another event', () => {
  const saved = semanticOccurrence(['event-1/details', 'event-2/details'], 1);
  assert.equal(semanticIndex(['event-new/details', 'event-1/details', 'event-2/details'], saved), 2);
  assert.equal(semanticIndex(['event-1/details'], saved), -1);
  assert.equal(semanticOccurrence([], -1), null);
});
