'use strict';

/* G32-B — Pixel Office frame policy + instrumentation.
 * Pure logic (no DOM, no canvas) so the node suite can exercise every rule;
 * index.html consumes the same object as window.PixelLoop.
 *
 * Rule (G32 resource discipline): the office keeps its pixels-art charm while
 * the cost of observing stays small — a full scene redraw happens only when
 * someone is actually traveling (active cap), otherwise at the slow ambient
 * cap (cosmetic wander), and slower still on constrained devices (lite).
 * Frame gaps between rAF ticks are ring-buffered for percentile evidence.
 */

const S = 240; // ~4 s of 60 Hz samples

function createFramePolicy(opts) {
  const o = Object.assign(
    {
      ambientMs: 100, // 10 fps ambient redraw cap (cosmetic wander)
      activeMs: 33, // 30 fps while a character actually travels
      liteMs: 200, // 5 fps on pixel-lite / reduced-motion
    },
    opts
  );

  const times = new Float64Array(S);
  let ti = 0;
  let count = 0;
  let lastNow = null;
  let lastDraw = -Infinity;
  let dirty = true;

  return {
    set dirty(v) {
      dirty = v;
    },
    get dirty() {
      return dirty;
    },
    /* Call once per rAF tick with the tick timestamp (ms). */
    noteFrame(nowMs) {
      if (lastNow !== null) {
        const dt = nowMs - lastNow;
        if (dt > 0 && dt < 10000) {
          times[ti] = dt;
          ti = (ti + 1) % S;
          if (count < S) count++;
        }
      }
      lastNow = nowMs;
    },
    /* Decide whether this tick draws the scene; returns {draw, mode}. */
    decide(nowMs) {
      const animating = typeof o.animating === 'function' ? !!o.animating() : false;
      const lite = typeof o.lite === 'function' ? !!o.lite() : false;
      const interval = animating ? o.activeMs : lite ? o.liteMs : o.ambientMs;
      const draw = dirty || nowMs >= lastDraw + interval;
      if (draw) {
        dirty = false;
        lastDraw = nowMs;
      }
      return { draw, mode: animating ? 'active' : lite ? 'lite' : 'ambient' };
    },
    /* Frame-gap percentiles over the ring buffer; the loop measures itself. */
    stats() {
      if (!count) return { samples: 0 };
      const arr = Array.from(times.slice(0, count)).sort((a, b) => a - b);
      const q = (p) => arr[Math.min(arr.length - 1, Math.floor(p * arr.length))];
      return { samples: arr.length, p50: q(0.5), p95: q(0.95), max: arr[arr.length - 1] };
    },
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { createFramePolicy };
/* Browser side: same factory, one global. */
if (typeof window !== 'undefined') window.PixelLoop = { createFramePolicy };
