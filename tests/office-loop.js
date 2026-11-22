'use strict';

/* G32-B regression: the office frame policy — pure logic, asserts only.
 * Boot: this file is standalone (spawned directly by package.json test). */

const assert = require('assert');
const { createFramePolicy } = require('../assets/office-loop.js');

let fails = 0;

function case_(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (e) {
    fails++;
    console.error(`FAIL - ${name}: ${e.message}`);
  }
}

case_('first tick draws, then honors the ambient cap', () => {
  const p = createFramePolicy({ ambientMs: 100, activeMs: 33, liteMs: 200 });
  assert.strictEqual(p.decide(0).draw, true);
  assert.strictEqual(p.decide(50).draw, false, 'inside ambient window');
  assert.strictEqual(p.decide(100).draw, true, 'at the cap it draws');
  assert.strictEqual(p.decide(150).draw, false);
});

case_('traveling character redraws at the active cap', () => {
  const p = createFramePolicy({ ambientMs: 100, activeMs: 33, animating: () => true });
  assert.strictEqual(p.decide(0).draw, true);
  assert.strictEqual(p.decide(20).draw, false, 'inside active window');
  assert.strictEqual(p.decide(33).draw, true, '30 fps cap holds');
  assert.strictEqual(p.decide(33).mode, 'active');
});

case_('pixel-lite / reduced-motion slows ambient to the lite cap', () => {
  let lite = true;
  const p = createFramePolicy({ ambientMs: 100, liteMs: 200, lite: () => lite });
  p.decide(0);
  assert.strictEqual(p.decide(150).draw, false, 'lite window is wider');
  assert.strictEqual(p.decide(200).draw, true);
  assert.strictEqual(p.decide(200).mode, 'lite');
  lite = false;
  // dirty flag still forces a redraw in any mode
  p.dirty = true;
  assert.strictEqual(p.decide(210).draw, true, 'dirty bypasses the cap');
});

case_('dirty forces a redraw inside any window', () => {
  const p = createFramePolicy({ ambientMs: 100 });
  p.decide(0);
  p.dirty = true;
  assert.strictEqual(p.decide(10).draw, true);
  assert.strictEqual(p.dirty, false, 'consumed by the draw');
  assert.strictEqual(p.decide(20).draw, false);
});

case_('stats report rolling frame-gap percentiles', () => {
  const p = createFramePolicy({});
  p.noteFrame(0);
  for (let i = 1; i <= 100; i++) p.noteFrame(i * 50); // steady 50 ms ticks
  const s = p.stats();
  assert.strictEqual(s.samples, 100);
  assert.ok(s.p50 <= 51 && s.p50 >= 49, `p50 ~50 got ${s.p50}`);
  assert.ok(s.max <= 51, `max <=51 got ${s.max}`);
});

case_('stats reject absurd gaps (tab was backgrounded)', () => {
  const p = createFramePolicy({});
  p.noteFrame(0);
  p.noteFrame(60000); // background tab gap
  p.noteFrame(60050);
  const s = p.stats();
  assert.strictEqual(s.samples, 1, 'only the sane dt survives');
});

case_('empty history answers samples 0, never NaN', () => {
  const p = createFramePolicy({});
  assert.deepStrictEqual(p.stats(), { samples: 0 });
});

if (!process.env.PIXEL_SKIP_BOOTED) {
  process.exitCode = fails ? 1 : 0;
  console.log(`\n${fails ? 'FAILED' : 'PASSED'}: ${fails} failed`);
}
