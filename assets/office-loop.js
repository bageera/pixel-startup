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
    },
    opts
  );

  const times = new Float64Array(S);
  let ti = 0;
  let count = 0; // ring-buffer occupancy (capped at S)
  let total = 0; // lifetime ticks — the honest loop-ownership probe
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
          total++;
        }
      }
      lastNow = nowMs;
    },
    /* Decide whether this tick draws the scene; returns {draw, mode}.
     * Degradation (pixel-lite / reduced-motion) = static: zero idle frames,
     * presentation sacrificed before information — only dirty redraws. */
    decide(nowMs) {
      const animating = typeof o.animating === 'function' ? !!o.animating() : false;
      const lite = typeof o.lite === 'function' ? !!o.lite() : false;
      if (lite) {
        const draw = dirty;
        if (draw) {
          dirty = false;
          lastDraw = nowMs;
        }
        return { draw, mode: 'static' };
      }
      const interval = animating ? o.activeMs : o.ambientMs;
      const draw = dirty || nowMs >= lastDraw + interval;
      if (draw) {
        dirty = false;
        lastDraw = nowMs;
      }
      return { draw, mode: animating ? 'active' : 'ambient' };
    },
    /* Frame-gap percentiles over the ring buffer; the loop measures itself.
     * `samples` is the LIFETIME tick count (ring never saturates it). */
    stats() {
      const out = { samples: total };
      if (!count) return out;
      const arr = Array.from(times.slice(0, count)).sort((a, b) => a - b);
      const q = (p) => arr[Math.min(arr.length - 1, Math.floor(p * arr.length))];
      out.p50 = q(0.5);
      out.p95 = q(0.95);
      out.max = arr[arr.length - 1];
      return out;
    },
  };
}

if (typeof module !== 'undefined' && module.exports) module.exports = { createFramePolicy };
/* Browser side: same factory, one global. */
if (typeof window !== 'undefined') window.PixelLoop = { createFramePolicy };
