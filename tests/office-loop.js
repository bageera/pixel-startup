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

case_('pixel-lite / reduced-motion = static: zero idle frames', () => {
  let lite = true;
  const p = createFramePolicy({ ambientMs: 100, lite: () => lite });
  assert.strictEqual(p.decide(0).draw, true, 'first render still happens');
  for (let t = 10; t <= 1000; t += 10) {
    assert.strictEqual(p.decide(t).draw, false, `idle ${t} draws nothing`);
  }
  assert.strictEqual(p.decide(1000).mode, 'static');
  lite = false;
  p.dirty = true;
  assert.strictEqual(p.decide(1010).draw, true, 'dirty still redraws');
  assert.strictEqual(p.decide(1020).draw, false, 'one dirty draw, not a loop');
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

case_('stats.samples is lifetime — the ring saturates, the counter does not', () => {
  const p = createFramePolicy({});
  const N = 300;
  p.noteFrame(0);
  for (let i = 1; i <= N; i++) p.noteFrame(i * 50); // more ticks than the 240 ring holds
  const s = p.stats();
  assert.strictEqual(s.samples, N, `lifetime ticks kept: got ${s.samples}`);
  assert.ok(s.max <= 51, `ring still answers recent-window percentiles, max ${s.max}`);
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

// G32-A: overlay layout + module hygiene
case_('overlay layout: bounded chips + one more-chip, deterministic', () => {
  const { layoutOverlay } = require('../assets/office-render.js');
  const comps = [];
  for (let i = 0; i < 12; i++) comps.push({ ref: 'project-' + i, label: 'Project ' + i });
  const chips = layoutOverlay(comps, 8);
  assert.strictEqual(chips.length, 9, '8 data chips + 1 summary');
  assert.strictEqual(chips[8].text, '+4 more');
  const xs = chips.map((c) => c.x);
  assert.deepStrictEqual(
    xs,
    xs.slice().sort((a, b) => a - b),
    'left-to-right order'
  );
  const same = layoutOverlay(comps, 8);
  assert.deepStrictEqual(chips, same, 'deterministic layout');
  // aggregates (no ref) never render
  assert.strictEqual(layoutOverlay([{ type: 'summary', id: 'summary-projects-company' }]).length, 0);
  // under the cap: no filler chip
  assert.strictEqual(layoutOverlay(comps.slice(0, 3), 8).length, 3);
});

if (!process.env.PIXEL_SKIP_BOOTED) {
  process.exitCode = fails ? 1 : 0;
  console.log(`\n${fails ? 'FAILED' : 'PASSED'}: ${fails} failed`);
}
