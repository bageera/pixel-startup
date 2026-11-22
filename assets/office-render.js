'use strict';

/* G32-A — Pixel Office renderer (extracted from index.html, visible behavior
 * unchanged). Owns: the frame loop draw paths, the cached static environment
 * layer (rebuilt only when the background sprite identity or door state
 * changes — one drawImage per frame instead of a full re-blit), and the
 * flagged manifest-driven organization overlay.
 *
 * Classic-script module: page globals (`ctx`, `canvas`, `office`,
 * `spriteManager`, `characters`, `startTime`) resolve lexically at call time.
 * No timers of its own: the single rAF chain lives in start(); repeated mode
 * switches, theme changes or overlay updates never create another loop.
 */

function createEnvLayer(canvas) {
  const layer = document.createElement('canvas');
  layer.width = canvas.width;
  layer.height = canvas.height;
  const lctx = layer.getContext('2d');
  lctx.imageSmoothingEnabled = false;
  let builtFrom = null; // background sprite identity the layer was drawn from

  return {
    /* Blit the cached layer; rebuild when the background actually changed —
     * the sprite object is reused with a new src on environment switches, so
     * track src, not object identity. */
    blit(targetCtx, spriteManager) {
      const bg = spriteManager.sprites.background;
      const id = spriteManager.imagesLoaded ? `${bg.src}|${bg.naturalWidth}` : '';
      if (id !== builtFrom) {
        lctx.clearRect(0, 0, layer.width, layer.height);
        if (spriteManager.imagesLoaded) {
          office.draw(spriteManager, lctx); // (spriteManager, target)
        }
        builtFrom = id;
      }
      targetCtx.drawImage(layer, 0, 0);
    },
  };
}

/* Pure overlay geometry: manifest components -> chip rects (one row).
 * Bounded: at most maxChips data chips + one "+N more" chip. Node-testable. */
function layoutOverlay(components, maxChips) {
  const cap = maxChips || 8;
  const comps = (components || []).filter((c) => c.ref);
  const data = comps.slice(0, cap);
  const chips = data.map((c, i) => ({
    x: 8 + i * 100,
    y: 8,
    w: 92,
    text: String(c.label || c.ref).slice(0, 14),
  }));
  if (comps.length > cap) {
    chips.push({ x: 8 + data.length * 100, y: 8, w: 60, text: `+${comps.length - cap} more` });
  }
  return chips;
}

function start(opts) {
  const policy = opts.policy;
  const started = opts.started || (() => window.simulationStarted === true);
  const envLayer = createEnvLayer(opts.canvas || canvas);
  let overlay = null; // set via setOverlay (manifest components), flag-gated

  window.__pixelFramePolicy = policy; // measurement handle (CDP)

  function drawNames(list) {
    ctx.font = '10px "Courier New", monospace';
    ctx.textAlign = 'center';
    for (const char of list) {
      const text = char.name;
      const nameWidth = ctx.measureText(text).width;
      ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
      const textY = char.y - 45;
      ctx.fillRect(char.x - nameWidth / 2 - 4, textY - 10, nameWidth + 8, 14);
      ctx.fillStyle = 'white';
      ctx.fillText(text, char.x, textY);
    }
  }

  function drawChips() {
    if (!overlay || !overlay.length) return;
    ctx.font = '8px "Courier New", monospace';
    ctx.textAlign = 'left';
    for (const chip of layoutOverlay(overlay)) {
      ctx.fillStyle = 'rgba(15, 15, 26, 0.85)';
      ctx.fillRect(chip.x - 3, chip.y - 3, chip.w, 14);
      ctx.strokeStyle = '#3b4252';
      ctx.strokeRect(chip.x - 3, chip.y - 3, chip.w, 14);
      ctx.fillStyle = '#d8dee9';
      ctx.fillText(chip.text, chip.x + 2, chip.y + 8);
    }
    ctx.textAlign = 'center'; // restore the text mode the rest of the loop uses
  }

  let errorLogged = false;

  function gameLoop(nowMs) {
    const tick = typeof nowMs === 'number' ? nowMs : performance.now();
    try {
      policy.noteFrame(tick);
      const decision = policy.decide(tick);
      // Cosmetic logic (targets, frames) keeps its original per-tick cadence;
      // only the CANVAS redraw is capped by the policy.
      if (started() && window.characters) {
        characters.forEach((c) => c.update());
      }
      if (!decision.draw) {
        requestAnimationFrame(gameLoop);
        return;
      }
      if (!started()) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        envLayer.blit(ctx, spriteManager);
        if (typeof characters !== 'undefined') {
          const pre = [...characters].sort((a, b) => a.y - b.y);
          pre.forEach((c) => c.draw(spriteManager));
          drawNames(pre);
        }
        requestAnimationFrame(gameLoop);
        return;
      }
      window.__pixelDraws = (window.__pixelDraws || 0) + 1; // G32-B evidence counter
      ctx.fillStyle = '#1e1e2e';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      envLayer.blit(ctx, spriteManager);
      const sortedCharacters = [...characters].sort((a, b) => a.y - b.y);
      sortedCharacters.forEach((c) => c.draw(spriteManager));
      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      document.getElementById('timer').textContent = elapsed;
      drawNames(sortedCharacters);
      drawChips();
    } catch (e) {
      // A renderer defect must never kill the scene loop silently: log once,
      // keep scheduling (draw retries next tick with the same policy caps).
      if (!errorLogged) {
        console.error('[office-render] frame error (loop continues):', e);
        errorLogged = true;
      }
    }
    requestAnimationFrame(gameLoop);
  }

  requestAnimationFrame(gameLoop);

  return {
    /* Manifest components (feature-flagged callers only decide WHEN). */
    setOverlay: (components) => {
      overlay = components;
    },
    markEnvDirty: () => {}, // sprite-identity rebuild makes this a no-op kept for future assets
  };
}

if (typeof window !== 'undefined') window.OfficeRenderer = { start: start, layoutOverlay: layoutOverlay, createEnvLayer: createEnvLayer };
if (typeof module !== 'undefined' && module.exports) module.exports = { start: start, layoutOverlay: layoutOverlay, createEnvLayer: createEnvLayer };
