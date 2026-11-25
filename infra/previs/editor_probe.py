"""Open the motion editor in headless Chromium, log console errors / failed requests / WebGL,
and screenshot it. Usage: probe.py <url> <out.jpg> [--gl] [--wait N]"""
import sys
import time
from playwright.sync_api import sync_playwright

url, out = sys.argv[1], sys.argv[2]
gl = "--gl" in sys.argv
wait = int(sys.argv[sys.argv.index("--wait") + 1]) if "--wait" in sys.argv else 25
args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] if gl else []

with sync_playwright() as p:
    b = p.chromium.launch(args=args)
    pg = b.new_page(viewport={"width": int(sys.argv[sys.argv.index("--size")+1].split("x")[0]) if "--size" in sys.argv else 1600, "height": int(sys.argv[sys.argv.index("--size")+1].split("x")[1]) if "--size" in sys.argv else 1000})
    pg.on("console", lambda m: m.type in ("error", "warning") and print(f"[console.{m.type}] {m.text[:400]}"))
    pg.on("pageerror", lambda e: print(f"[pageerror] {str(e)[:400]}"))
    pg.on("requestfailed", lambda r: print(f"[reqfailed] {r.url} {r.failure}"))
    pg.on("response", lambda r: r.status >= 400 and print(f"[http {r.status}] {r.url}"))
    pg.on("response", lambda r: (".glb" in r.url) and print(f"[glb {r.status}] {r.url} {r.headers.get('content-type')}"))
    pg.goto(url, wait_until="domcontentloaded")
    print("webgl:", pg.evaluate("""() => { const c = document.createElement('canvas');
        const g = c.getContext('webgl2') || c.getContext('webgl');
        if (!g) return null; const e = g.getExtension('WEBGL_debug_renderer_info');
        return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'yes'; }"""))
    t0 = time.time()
    time.sleep(wait)
    print("canvases:", pg.evaluate("() => [...document.querySelectorAll('canvas')].map(c => c.width + 'x' + c.height)"))
    pg.screenshot(path=out, type="jpeg", quality=80)
    print("shot", out)
    if "--click" in sys.argv:  # x,y on the timeline ruler, then a second screenshot
        x, y = map(int, sys.argv[sys.argv.index("--click") + 1].split(","))
        pg.mouse.click(x, y)
        time.sleep(wait)
        pg.screenshot(path=out.replace(".jpg", "-seek.jpg"), type="jpeg", quality=80)
        print("shot", out.replace(".jpg", "-seek.jpg"))
    b.close()
