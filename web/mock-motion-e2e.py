"""Dev-only headless check of the motion editor UI against mock-motion.mjs (not shipped).

    node mock-motion.mjs 8794 &   then   python mock-motion-e2e.py <outdir>
"""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8794"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
errors = []


def posts():
    return json.load(urllib.request.urlopen(f"{BASE}/api/_mock/posts"))


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        errors.append(what)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    console = []
    pg.on("console", lambda m: m.type == "error" and console.append(m.text))
    pg.on("pageerror", lambda e: console.append(str(e)))

    # 1. home form sends renderer=remotion
    pg.goto(BASE + "/")
    pg.get_by_label("渲染方式").select_option("remotion")
    pg.locator("textarea").fill("西湖为什么被称为人间天堂")
    pg.screenshot(path=f"{OUT}/1-home.png")
    pg.get_by_role("button", name="开始制作").click()
    pg.wait_for_url("**/jobs/rm1")
    check(posts()["jobs"][-1].get("renderer") == "remotion", "POST /api/jobs carries renderer=remotion")

    # 2. job page: badge, chips, button, versions
    pg.wait_for_selector(".mv-card")
    check(pg.locator(".mv-renderer").inner_text() == "Remotion 动效卡", "renderer badge")
    n = pg.locator(".mv-card").count()
    check(n == len(pg.locator(".scenes li").all()) and n > 0, f"one shot-card chip per scene ({n})")
    check(pg.locator(".mv-card").first.get_attribute("href").startswith("https://vincentwei1021.github.io/video-shotcraft/"), "chip links to gallery")
    check(pg.get_by_text("标题卡（回退）").count() > 0, "fallback card labelled")
    check(pg.get_by_role("link", name="在动效编辑器中打开").count() == 1, "editor button")
    pg.wait_for_selector(".mv-item video")
    check(pg.locator(".mv-item").count() == 1, "versions list shows v1")
    pg.screenshot(path=f"{OUT}/2-job.png", full_page=True)

    # 3. edit page: waiting state, then iframe; save posts the project and shows the warning
    pg.get_by_role("link", name="在动效编辑器中打开").click()
    pg.wait_for_url("**/jobs/rm1/edit")
    pg.wait_for_selector("iframe.mv-frame", timeout=20000)
    pg.frame_locator("iframe.mv-frame").get_by_text("project written").wait_for()
    check(pg.get_by_text("配音轨已锁定").count() == 1, "voice-lock note")
    pg.screenshot(path=f"{OUT}/3-edit.png")
    pg.get_by_role("button", name="保存为新版本").click()
    pg.wait_for_selector(".mv-status")
    vp = posts()["versions"]
    check(len(vp) == 1 and vp[0].get("project", {}).get("mock") is True, "POST versions body = {project} from iframe localStorage")
    pg.wait_for_selector(".mv-status.done", timeout=20000)
    check(pg.locator(".mv-status .mv-warn li").count() == 1, "warning from mock shown")
    pg.screenshot(path=f"{OUT}/4-saved.png")

    # 4. job page now lists v2 with its warning
    pg.get_by_role("link", name="返回作品").click()
    pg.wait_for_selector(".mv-item:nth-child(2)")
    check(pg.locator(".mv-item").count() == 2 and pg.locator(".mv-versions .mv-warn").count() == 1, "job page lists v2 + warning")
    pg.locator(".mv-versions").scroll_into_view_if_needed()
    pg.screenshot(path=f"{OUT}/5-versions.png", full_page=True)

    # 5. 409 for an html job
    pg.goto(BASE + "/jobs/html1")
    pg.wait_for_selector(".mv-renderer")
    check(pg.get_by_role("link", name="在动效编辑器中打开").count() == 0, "no editor button for html job")
    check(pg.locator(".mv-card").count() == 0 and pg.locator(".mv-versions").count() == 0, "no chips/versions for html job")
    pg.goto(BASE + "/jobs/html1/edit")
    pg.get_by_text("只有已完成的 Remotion 作品").wait_for()
    pg.screenshot(path=f"{OUT}/6-conflict.png")
    check(True, "409 message shown")

    # the mock answers /motion/ with 502 twice on purpose (editor warming up); the readiness
    # probe's network log for those is expected
    console = [c for c in console if "502 (Bad Gateway)" not in c]
    check(not console, f"no console errors {console}")
    b.close()

print("FAILED" if errors else "ALL PASS")
sys.exit(1 if errors else 0)
