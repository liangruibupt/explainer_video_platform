"""Dev-only headless check of the Opus chat panel against mock-assist.mjs (not shipped).

    node mock-assist.mjs 8803 &   then   python mock-assist-e2e.py <outdir>
"""
import json
import sys
import urllib.request

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
KEY = "shotcraft-workbench-project-v1"
SID = "growth_math"
OLD = {"title": "三倍意味着什么", "accent": "#4c9aff", "show": True}
NEW = {"title": "折线图版本", "accent": "#ff8800", "show": False, "lineWidth": 6}
errors = []


def posts():
    return json.load(urllib.request.urlopen(f"{BASE}/api/_mock/posts"))


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        errors.append(what)


def shot(pg, name):
    full = f"{OUT}/{name}.png"
    pg.screenshot(path=full)
    im = Image.open(full)
    im.thumbnail((960, 960))
    im.save(f"{OUT}/{name}-small.png")


def project(pg):
    return json.loads(pg.evaluate(f"localStorage.getItem('{KEY}')"))


def clip(p, sid=SID):
    return next(c for t in p["tracks"] for c in t["clips"] if c["cardId"] == f"proj:shot:scene-{sid}")


def frame_text(pg):
    return pg.frame_locator("iframe.mv-frame").locator("#proj").inner_text()


def send(pg, text):
    pg.get_by_label("给 Opus 的消息").fill(text)
    pg.get_by_role("button", name="发送", exact=True).click()


def last(pg):
    return pg.locator(".mc-turn.assistant").last


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    console = []
    pg.on("console", lambda m: m.type == "error" and console.append(m.text))
    pg.on("pageerror", lambda e: console.append(str(e)))

    pg.goto(BASE + "/jobs/rm1/edit")
    pg.wait_for_selector("iframe.mv-frame")
    pg.frame_locator("iframe.mv-frame").get_by_text("Mock Motion Workbench").wait_for()
    check(pg.locator(".mc-panel").count() == 1, "chat panel open by default")
    check(pg.get_by_text("旁白和配音不能在这里改").count() == 1, "narration note shown once")
    opts = pg.locator(".mc-scene option").all_inner_texts()
    check(opts[0] == "整片" and len(opts) == 8, f"scene selector 整片 + 7 scenes ({len(opts)})")
    base = project(pg)
    c0 = clip(base)

    # 1. parameter edit -> apply -> iframe shows it -> undo
    pg.get_by_label("修改范围").select_option(SID)
    send(pg, "把第二段标题改成三年三倍的含义")
    last(pg).locator(".mc-changes").wait_for()
    a = posts()["assist"][-1]
    check(a.get("scene_id") == SID and a["project"]["tracks"][0]["clips"][0]["id"] == "clip-title_card",
          "POST /assist carries scene_id + project read from the iframe")
    check(last(pg).locator(".mc-rejected li").count() == 1, "rejected op listed")
    shot(pg, "1-param-reply")
    hits = posts()["motion"]
    last(pg).get_by_role("button", name="应用修改").click()
    last(pg).get_by_text("已应用到编辑器").wait_for()
    pg.frame_locator("iframe.mv-frame").get_by_text("三年三倍的含义").wait_for()
    check(posts()["motion"] == hits + 1, "iframe reloaded once")
    check(clip(project(pg))["props"]["title"] == "三年三倍的含义", "stored project has the new title after reload")
    shot(pg, "2-param-applied")
    last(pg).get_by_role("button", name="撤销这次修改").click()
    last(pg).get_by_text("已撤销").wait_for()
    pg.wait_for_timeout(300)
    check("三年三倍的含义" not in frame_text(pg) and clip(project(pg))["props"] == c0["props"], "undo restores the old props")

    # 2. scene edit -> stills -> apply -> props = defaults, timing kept -> undo
    send(pg, "柱状图换成折线图")
    last(pg).get_by_role("button", name="让 Opus 改这段画面").wait_for()
    shot(pg, "3-scene-edit-offer")
    last(pg).get_by_role("button", name="让 Opus 改这段画面").click()
    last(pg).locator(".mc-progress").wait_for()
    check(posts()["edits"][-1] == {"scene_id": SID, "instruction": "把柱状图换成折线图，保留年份标签"}, "POST scene-edits body = scene_edit")
    last(pg).get_by_text("Opus 正在改写画面").wait_for(timeout=8000)
    shot(pg, "4-scene-edit-running")
    last(pg).locator(".mc-still").nth(2).wait_for(timeout=15000)
    check(last(pg).locator(".mc-still img").count() == 3, "3 stills shown")
    check(last(pg).get_by_text("版面检查发现 1 个问题").count() == 1, "layout issues shown")
    pg.wait_for_timeout(400)
    shot(pg, "5-scene-edit-ready")
    last(pg).locator(".mc-still").first.click()
    pg.locator(".mc-zoom img").wait_for()
    shot(pg, "6-still-zoom")
    pg.keyboard.press("Escape")
    check(pg.locator(".mc-zoom").count() == 0, "Esc closes the enlarged still")
    last(pg).get_by_role("button", name="应用", exact=True).click()
    last(pg).get_by_text("新画面已放进编辑器").wait_for()
    pg.frame_locator("iframe.mv-frame").get_by_text("折线图版本").wait_for()
    p = project(pg)
    c = clip(p)
    check(c["props"] == NEW, f"scene clip props == defaults ({c['props']})")
    check(c["start"] == c0["start"] and c["duration"] == c0["duration"], "start/duration unchanged")
    check(clip(p, "title_card")["props"] == clip(base, "title_card")["props"], "other scenes untouched")
    shot(pg, "7-scene-applied")
    last(pg).get_by_role("button", name="撤销", exact=True).click()
    last(pg).get_by_text("已撤销").wait_for()
    pg.wait_for_timeout(300)
    check(clip(project(pg))["props"] == OLD, "scene undo writes the old defaults")
    check("折线图版本" not in frame_text(pg), "iframe shows the old props after undo")

    # 3. refusal
    send(pg, "把旁白改得活泼一点")
    last(pg).get_by_text("改不了").wait_for()
    check(last(pg).locator("button").count() == 0, "refusal has no apply buttons")

    # 4. second scene edit while one runs -> 409; discard
    send(pg, "画面加一个箭头")
    last(pg).get_by_role("button", name="让 Opus 改这段画面").click()
    last(pg).locator(".mc-progress").wait_for()
    first_edit = last(pg)
    send(pg, "画面再加一个圆")
    last(pg).get_by_role("button", name="让 Opus 改这段画面").click()
    pg.locator(".mc-error").get_by_text("还有一个画面修改在进行").wait_for()
    check(True, "409 on a concurrent scene edit shows Chinese message")
    pg.locator(".mc-error .mv-x").click()
    pg.locator(".mc-turn.assistant").nth(-2).get_by_role("button", name="放弃").wait_for(timeout=15000)
    pg.locator(".mc-turn.assistant").nth(-2).get_by_role("button", name="放弃").click()
    pg.locator(".mc-turn.assistant").nth(-2).get_by_text("已放弃").wait_for()
    check(True, "discard -> 已放弃")

    # 5. history survives a reload
    n_turns = pg.locator(".mc-turn").count()
    pg.reload()
    pg.wait_for_selector("iframe.mv-frame")
    pg.locator(".mc-turn").nth(n_turns - 1).wait_for()
    check(pg.locator(".mc-turn").count() == n_turns, f"history restored after reload ({n_turns} turns)")
    check(pg.locator(".mc-turn.assistant").first.get_by_text("刷新页面后").count() == 1, "old param edit marked not re-appliable")
    hist = json.loads(pg.evaluate("localStorage.getItem('evp-motion-chat-rm1')"))
    check(len(hist) <= 20 and all("project" not in t for t in hist), "history <= 20 turns, no projects stored")
    shot(pg, "8-after-reload")

    # 6. error states
    send(pg, "测试 409")
    pg.locator(".mc-error").wait_for()
    t409 = pg.locator(".mc-error").inner_text()
    check("只有已完成的 Remotion 作品" in t409 and pg.get_by_label("给 Opus 的消息").input_value() == "测试 409",
          f"409 message + input restored ({t409!r})")
    send(pg, "测试 500")
    pg.locator(".mc-error").get_by_text("服务出错（500）").wait_for()
    check(True, "500 shows 服务出错（500）")
    shot(pg, "9-error-500")
    check(pg.locator(".mc-turn").count() == n_turns, "failed sends leave no turns")

    # 7. save-as-version still works; collapse / expand
    pg.locator(".mc-error .mv-x").click()
    pg.get_by_role("button", name="保存为新版本").click()
    pg.wait_for_selector(".mv-status.done", timeout=15000)
    check(clip(posts()["versions"][-1]["project"])["props"] == OLD, "save-as-version posts the current project")
    pg.get_by_role("button", name="收起助手").click()
    check(pg.locator(".mc-panel").count() == 0, "panel collapses")
    shot(pg, "10-collapsed")
    pg.get_by_role("button", name="Opus 助手").click()
    check(pg.locator(".mc-panel").count() == 1, "panel reopens")

    # 409/500 responses are logged by the browser as failed resource loads; those are expected
    console = [c for c in console if "status of 409" not in c and "status of 500" not in c]
    check(not console, f"no console errors {console}")
    b.close()

print("FAILED" if errors else "ALL PASS")
sys.exit(1 if errors else 0)
