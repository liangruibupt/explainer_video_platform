"""Dev-only: headless screenshots of the previs UI against the mocks (PR images).

Usage (from web/, vite running with the mocks):
  $PY src/pages/previs/mocks/shoot_ui.py http://127.0.0.1:5199 <out_dir>
Writes JPEGs (< 250 KB each) to out_dir.
"""
import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5199"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "shots_ui")
OUT.mkdir(parents=True, exist_ok=True)


def save(page, name, full=False, clip=None):
    png = page.screenshot(full_page=full, clip=clip)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.width > 1440:
        im = im.resize((1440, round(im.height * 1440 / im.width)), Image.LANCZOS)
    for q in (82, 74, 66, 58, 50):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() < 250_000:
            break
    (OUT / f"{name}.jpg").write_bytes(buf.getvalue())
    print(name, im.size, buf.tell() // 1024, "KB")


with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    page = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 1. library with the model card
    page.goto(f"{BASE}/library")
    page.get_by_role("button", name="3D 模型").click()
    page.wait_for_selector(".al-model img")
    page.wait_for_timeout(800)
    save(page, "assets-model-card")

    # 2. viewer with the crank DOF slider moved
    page.locator(".al-model").first.click()
    page.wait_for_selector(".mv3-stage[data-loaded='1']", timeout=60000)
    page.locator(".pv-dlg input[type=range]").first.fill("110")
    page.wait_for_timeout(1500)
    save(page, "viewer-dof")
    page.get_by_role("button", name="关闭").click()

    # 3. new-asset dialog: 一句话 -> 写规格 -> 构建并预览; then the 图生 3D entry
    page.get_by_role("button", name="新建 3D 资产").click()
    page.locator(".pv-dlg textarea").fill("单缸四冲程发动机，缸径 8 厘米，能转曲轴看活塞和气门")
    page.get_by_role("button", name="写规格").click()
    page.get_by_role("button", name="构建并预览").click()
    page.wait_for_selector(".pv-dlg .mv3-stage[data-loaded='1']", timeout=60000)
    page.wait_for_timeout(1200)
    save(page, "new-asset-text")
    page.locator(".pv-tabs").get_by_role("button", name="图生 3D").click()
    page.locator(".pv-dlg textarea").fill("一只坐着的橘猫")
    page.wait_for_timeout(800)
    save(page, "new-asset-image3d")
    page.get_by_role("button", name="关闭").click()

    # 4. job page 镜头 tab
    page.set_viewport_size({"width": 1440, "height": 1130})
    page.goto(f"{BASE}/jobs/job_mock_previs")
    page.get_by_role("tab", name="镜头").click()
    page.wait_for_selector(".ps-stills img")
    page.wait_for_timeout(1500)
    save(page, "job-shots-tab")

    # 5. workbench renderer choice
    page.goto(f"{BASE}/")
    page.locator("select").filter(has_text="3D 白模预演").select_option("previs3d")
    page.wait_for_timeout(300)
    save(page, "workbench-renderer", clip={"x": 0, "y": 0, "width": 1440, "height": 520})

    print("page errors:", errors or "none")
    b.close()
