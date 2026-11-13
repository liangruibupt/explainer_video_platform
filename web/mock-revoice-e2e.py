"""Dev-only headless check of the 配音 panel against mock-revoice.mjs (not shipped).

    node mock-revoice.mjs 8813 &   then   python mock-revoice-e2e.py <outdir>
"""
import json
import sys
import urllib.request

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8813"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
KEY = "shotcraft-workbench-project-v1"
SAMPLE = "/home/ubuntu/workplace/explainer_video_platform/data/jobs/20261111-075413-d48e69"
SCENES = json.load(open(f"{SAMPLE}/scenes.json"))
ORIG = {s["id"]: s["narration"] for s in SCENES}
ORDER = [s["id"] for s in SCENES]
NEW = {
    "token_engine": "Token是模型服务的计费单位。关键是提升推理调用量，让企业接入后天天用起来，预估贡献约百分之三十。",
    "migration_engine": "迁移帮助客户把现有系统和数据搬上平台。核心增长点是存量大客户的整体上云与模型替换，预估贡献约百分之十五。",
    "recap": "算力打底，Token放量，Agent拉动场景，迁移带来存量。三五、三十、二十、十五，合力实现三年三倍。",
}
errors = []


def get(path):
    return json.load(urllib.request.urlopen(f"{BASE}{path}"))


def mode(**kw):
    req = urllib.request.Request(f"{BASE}/api/_mock/mode", data=json.dumps(kw).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


def posts():
    return get("/api/_mock/posts")


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


def clip(p, cid):
    return next(c for t in p["tracks"] for c in t["clips"] if c["id"] == cid)


def area(pg, sid):
    return pg.get_by_label(f"S{ORDER.index(sid) + 1} 旁白")


def texts(pg):
    return {sid: area(pg, sid).input_value() for sid in ORDER}


def voice(pg):
    return pg.locator(".vo-pane")


def go(pg):
    return voice(pg).locator(".vo-go")


def err(pg):
    return voice(pg).locator(".mc-error")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    console = []
    pg.on("console", lambda m: m.type == "error" and console.append(m.text))
    pg.on("pageerror", lambda e: console.append(str(e)))

    pg.goto(BASE + "/jobs/rm1/edit")
    pg.wait_for_selector("iframe.mv-frame")
    pg.frame_locator("iframe.mv-frame").get_by_text("Mock Motion Workbench").wait_for()
    check(pg.get_by_text("配音轨已锁定，改旁白请用『配音』面板重新配音").count() == 1, "voice-lock note updated")
    check(pg.get_by_role("tab").all_inner_texts() == ["Opus 助手", "配音"], "tabs Opus 助手 / 配音")
    check(pg.locator(".mc-pane").is_visible() and not voice(pg).is_visible(), "chat tab is the default")

    # 1. narrations loaded from the job
    pg.get_by_role("tab", name="配音").click()
    area(pg, "recap").wait_for()
    check(texts(pg) == ORIG, "7 textareas hold job.scenes[i].narration")
    check(voice(pg).locator(".vo-scene.changed").count() == 0 and go(pg).is_disabled()
          and "（0 段）" in go(pg).inner_text(), "nothing changed -> revoice disabled (0 段)")
    shot(pg, "1-voice-tab")

    # 2. consistency check fills the 3 narrations, lists issues
    voice(pg).get_by_role("button", name="检查一致性").click()
    voice(pg).locator(".vo-result").wait_for()
    c = posts()["consistency"][-1]
    check("tracks" in c.get("project", {}) and "instruction" not in c, "POST /consistency carries the current project")
    t = texts(pg)
    check(all(t[s] == NEW[s] for s in NEW) and all(t[s] == ORIG[s] for s in ORIG if s not in NEW),
          "consistency filled exactly the 3 expected narrations")
    check(voice(pg).locator(".vo-scene.changed").count() == 3, "3 scenes highlighted")
    check(voice(pg).locator(".vo-issues li").count() == 4, "4 issues listed")
    check(voice(pg).locator(".mc-rejected li").count() == 1, "rejected listed")
    check("（3 段）" in go(pg).inner_text() and go(pg).is_enabled(), "revoice button (3 段)")
    shot(pg, "2-consistency")

    # 3. props from the check -> writeProject -> undo
    hits = posts()["motion"]
    voice(pg).get_by_role("button", name="应用参数修改（1 处）").click()
    voice(pg).get_by_text("参数修改已应用到编辑器").wait_for()
    pg.frame_locator("iframe.mv-frame").get_by_text("clip-c029 三五三十二十十五").wait_for()
    check(posts()["motion"] == hits + 1, "props apply reloaded the iframe once")
    check(clip(project(pg), "clip-c029")["props"]["text"] == "三五三十二十十五", "caption text set in stored project")
    voice(pg).get_by_role("button", name="撤销参数修改").click()
    voice(pg).get_by_text("已撤销").wait_for()
    pg.wait_for_timeout(300)
    check(clip(project(pg), "clip-c029")["props"]["text"] == "三五二五二十二十", "props undo restores the caption")

    # 4. revoice only the changed scenes -> running -> ready
    go(pg).click()
    voice(pg).locator(".vo-take .mc-progress").wait_for()
    r = posts()["revoice"][-1]
    check(r == {"narrations": NEW}, f"POST /revoice body has only the 3 changed scenes ({list(r.get('narrations', {}))})")
    voice(pg).get_by_text("正在重新配音").wait_for(timeout=5000)
    pg.wait_for_timeout(1200)
    check("已用" in voice(pg).locator(".vo-take .mc-progress").inner_text(), "progress shows elapsed time")
    check(area(pg, "recap").is_disabled(), "textareas locked while re-voicing")
    shot(pg, "3-running")
    voice(pg).get_by_role("button", name="应用到编辑器").wait_for(timeout=10000)
    diff = voice(pg).locator(".vo-diff li")
    check(diff.count() == 3, "ready view lists 3 scenes")
    check(voice(pg).get_by_text("+0.43 秒").count() == 1, "S7 shows +0.43 秒")
    check(voice(pg).get_by_text("长度不变").count() == 2, "S4/S6 show 长度不变")
    check(voice(pg).locator(".vo-play").count() == 3, "3 play buttons")
    check(voice(pg).locator(".vo-drift").count() == 0, "no drift warning")
    check("全片 73.00 秒 → 73.43 秒" in voice(pg).locator(".vo-total").inner_text(), "total length line")
    voice(pg).locator(".vo-play").last.click()
    pg.wait_for_timeout(600)
    playing = voice(pg).get_by_text("■ 停止").count() == 1
    failed = err(pg).count() == 1 and "试听失败" in err(pg).inner_text()
    check(playing or failed, f"play button plays the preview ({'playing' if playing else 'codec missing'})")
    if playing:
        voice(pg).locator(".vo-play").last.click()
    if failed:
        err(pg).locator(".mv-x").click()
    shot(pg, "4-ready")

    # 5. apply -> returned project written, iframe reloaded once
    before = project(pg)
    hits = posts()["motion"]
    voice(pg).get_by_role("button", name="应用到编辑器").click()
    voice(pg).get_by_text("新配音已放进编辑器").wait_for()
    pg.frame_locator("iframe.mv-frame").get_by_text("voice dur=2203").wait_for()
    check(posts()["motion"] == hits + 1, "apply reloaded the iframe once")
    a = posts()["apply"][-1]
    check(a["project"]["tracks"][0]["clips"] == before["tracks"][0]["clips"], "POST apply carries the current project")
    p = project(pg)
    check(clip(p, "clip-recap")["duration"] == 362 and p["source"].endswith("@2203f#20261111-075938-e39cc2"),
          "stored project = returned project (S7 349 -> 362, new source)")
    check(texts(pg) == {**ORIG, **NEW} and voice(pg).locator(".vo-scene.changed").count() == 0
          and "（0 段）" in go(pg).inner_text(), "applied narrations become the new baseline")
    shot(pg, "5-applied")

    # 6. undo
    hits = posts()["motion"]
    voice(pg).get_by_role("button", name="撤销配音").click()
    voice(pg).get_by_text("已恢复原来的配音").wait_for()
    pg.frame_locator("iframe.mv-frame").get_by_text("voice dur=2190").wait_for()
    check(posts()["motion"] == hits + 1 and "tracks" in posts()["undo"][-1]["project"], "undo posts project, reloads once")
    check(clip(project(pg), "clip-recap")["duration"] == 349, "undo restores S7 length")
    check(texts(pg) == ORIG, "undo restores the original narrations")

    # 7. drift variant -> warning + 整片重新配音 (all=true)
    mode(drift=True)
    area(pg, "recap").fill(NEW["recap"])
    check(voice(pg).locator(".vo-scene.changed").count() == 1 and "（1 段）" in go(pg).inner_text(), "manual edit highlighted (1 段)")
    go(pg).click()
    voice(pg).locator(".vo-drift").wait_for(timeout=10000)
    shot(pg, "6-drift")
    voice(pg).get_by_role("button", name="整片重新配音").click()
    voice(pg).locator(".vo-take .mc-progress").wait_for()
    r = posts()["revoice"][-1]
    check(r.get("all") is True and r.get("narrations") == {"recap": NEW["recap"]}, "整片重新配音 posts all=true")
    voice(pg).get_by_role("button", name="应用到编辑器").wait_for(timeout=10000)
    check(voice(pg).locator(".vo-drift").count() == 0 and voice(pg).locator(".vo-diff li").count() == 7,
          "full re-voice: 7 scenes, no drift")
    mode(drift=False)

    # 8. Chinese errors
    mode(fail={"path": "revoice", "code": 409})
    go(pg).click()
    err(pg).wait_for()
    check("没能开始重新配音：另一次重新配音正在进行" in err(pg).inner_text(), f"409 -> Chinese ({err(pg).inner_text()!r})")
    err(pg).locator(".mv-x").click()
    mode(fail={"path": "revoice", "code": 500})
    go(pg).click()
    err(pg).wait_for()
    check("服务出错（500）" in err(pg).inner_text(), "revoice 500 -> 服务出错（500）")
    err(pg).locator(".mv-x").click()
    mode(fail=None)
    voice(pg).get_by_label("一致性检查说明").fill("测试 500")
    voice(pg).get_by_role("button", name="检查一致性").click()
    err(pg).wait_for()
    check("一致性检查失败：服务出错（500）" in err(pg).inner_text(), "consistency 500 -> Chinese")
    check(posts()["consistency"][-1].get("instruction") == "测试 500", "instruction sent")
    shot(pg, "7-error")
    err(pg).locator(".mv-x").click()
    voice(pg).get_by_label("一致性检查说明").fill("测试 409")
    voice(pg).get_by_role("button", name="检查一致性").click()
    err(pg).wait_for()
    check("一致性检查失败：编辑器没有连到这个作品" in err(pg).inner_text(), "consistency 409 -> Chinese detail")
    err(pg).locator(".mv-x").click()

    # 9. chat: refusal -> link to the voice tab; param edit still works
    pg.get_by_role("tab", name="Opus 助手").click()
    pg.get_by_label("给 Opus 的消息").fill("把旁白改得活泼一点")
    pg.get_by_role("button", name="发送", exact=True).click()
    link = pg.locator(".mc-turn.assistant").last.get_by_role("button", name="去配音面板修改")
    link.wait_for()
    shot(pg, "8-chat-link")
    link.click()
    check(voice(pg).is_visible() and not pg.locator(".mc-pane").is_visible(), "link switches to the 配音 tab")
    check(area(pg, "recap").input_value() == NEW["recap"], "voice tab state kept across tab switches")
    pg.get_by_role("tab", name="Opus 助手").click()
    pg.get_by_label("给 Opus 的消息").fill("改第二段标题")
    pg.get_by_role("button", name="发送", exact=True).click()
    last = pg.locator(".mc-turn.assistant").last
    last.get_by_role("button", name="应用修改").click()
    last.get_by_text("已应用到编辑器").wait_for()
    check(clip(project(pg), "clip-growth_math")["props"]["title"] == "三年三倍的含义", "chat param edit still applies")
    check(last.get_by_role("button", name="去配音面板修改").count() == 0, "no voice link on a normal reply")

    # 10. save-as-version still works; collapse keeps state
    pg.get_by_role("button", name="保存为新版本").click()
    pg.wait_for_selector(".mv-status.done", timeout=15000)
    check(clip(posts()["versions"][-1]["project"], "clip-growth_math")["props"]["title"] == "三年三倍的含义",
          "save-as-version posts the current project")
    pg.get_by_role("button", name="收起助手").click()
    check(not pg.locator(".mc-panel").is_visible(), "panel collapses")
    pg.get_by_role("button", name="Opus 助手 / 配音").click()
    check(pg.locator(".mc-panel").is_visible(), "panel reopens")
    shot(pg, "9-final")

    console = [c for c in console if "status of 409" not in c and "status of 500" not in c]
    check(not console, f"no console errors {console}")
    b.close()

print("FAILED" if errors else "ALL PASS")
sys.exit(1 if errors else 0)
