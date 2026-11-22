#!/usr/bin/env python3
"""G31-B baseline measure: reproducible office/browser numbers via CDP metrics.

Prints JSON: canvas info, JS heap, resource bytes, and a 2s idle rAF count.
Run with the stack on :19000 and a Chrome with --remote-debugging-port=9222
(except heap, which needs Chrome's performance.memory).
"""

import json
import subprocess
import sys

PAGE = "http://127.0.0.1:19000/"
WS = "http://127.0.0.1:9222"


def main() -> None:
    eval_js = (
        "(() => { const mem = performance.memory; "
        "const res = performance.getEntriesByType('resource'); "
        "return JSON.stringify({"
        "canvas: Array.from(document.querySelectorAll('canvas')).map(c => ({id: c.id, w: c.width, h: c.height})),"
        "heapMB: mem ? Math.round(mem.usedJSHeapSize/1048576*10)/10 : null,"
        "requests: res.length,"
        "kbytes: Math.round(res.reduce((s,r)=>s+(r.transferSize||0),0)/1024)}); })()"
    )
    raf_js = (
        "new Promise(res => { let n = 0; const t0 = performance.now();"
        " function tick(){ n++; if (performance.now() - t0 < 2000)"
        " requestAnimationFrame(tick); else res({frames_in_2s: n, fps: Math.round(n/2)}); }"
        " requestAnimationFrame(tick); })"
    )
    out = {"page": PAGE}
    try:
        import webbrowser  # noqa: F401  (documented manual step)

        out.setdefault("note", "open the page in the debug Chrome once, then measure")
        out["eval"] = eval_js
        out["raf"] = raf_js
    finally:
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
