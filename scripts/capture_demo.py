"""Record a guided walkthrough of FleetPilot: screenshots of every section + a video + an animated GIF.

Requires a running stack (``docker compose up``) and Playwright with Chromium
(``pip install playwright && python -m playwright install chromium``).

    python scripts/capture_demo.py                       # -> docs/demo/
    python scripts/capture_demo.py --base-url http://localhost:3000 --out docs/demo

Best run against a fresh database (``docker compose down -v && docker compose up -d``) so the Approval Center
starts empty. The walkthrough creates proposals and approves one of them (simulated execution).
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
W, H = 1440, 900


class Demo:
    def __init__(self, page: Page, base: str, out: Path) -> None:
        self.page, self.base, self.out, self.n = page, base.rstrip("/"), out, 0
        self.shots: list[Path] = []

    def pause(self, ms: int = 1200) -> None:
        self.page.wait_for_timeout(ms)

    def go(self, path: str) -> None:
        self.page.goto(self.base + path)
        self.page.wait_for_load_state("networkidle")
        self.pause(900)

    def shot(self, name: str, full: bool = False) -> None:
        self.n += 1
        p = self.out / f"{self.n:02d}-{name}.png"
        self.page.screenshot(path=str(p), full_page=full)
        self.shots.append(p)
        print("  saved", p.relative_to(ROOT) if p.is_relative_to(ROOT) else p)

    def ask(self, question: str) -> None:
        before = self.page.get_by_role("link", name="execution timeline").count()
        box = self.page.get_by_label("Question")
        box.click()
        box.press_sequentially(question, delay=25)
        self.page.get_by_role("button", name=re.compile(r"^\s*Ask\s*$")).click()
        self.page.get_by_role("link", name="execution timeline").nth(before).wait_for(timeout=60_000)
        self.pause(800)
        # bring the new question + answer to the top of the viewport
        self.top(self.bubble(question))
        self.pause(900)

    def bubble(self, question: str):
        """The user's chat bubble for `question` (not the matching entry in the Recent requests sidebar)."""
        return self.page.locator("div.bg-brand-600").filter(has_text=question).last

    def top(self, locator, offset: int = 90) -> None:
        locator.evaluate("(e, o) => { e.scrollIntoView({block: 'start'}); window.scrollBy(0, -o); }", offset)


def run(base: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    video_dir = out / "_video"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                  record_video_dir=str(video_dir), record_video_size={"width": W, "height": H})
        page = ctx.new_page()
        d = Demo(page, base, out)

        print("1. Sign in")
        d.go("/login")
        d.shot("login")
        page.get_by_role("button").filter(has_text="admin").first.click()  # Acme admin
        page.wait_for_url(re.compile(r"/$"))
        page.wait_for_load_state("networkidle")
        d.pause(2000)

        print("2. Overview")
        d.shot("overview")
        d.shot("overview-full", full=True)

        print("3. AI Copilot")
        d.go("/copilot")
        d.ask("What are the biggest fleet health risks this month?")
        d.shot("copilot-insights")
        d.ask("Which devices are running low on disk space?")
        page.get_by_role("button", name=re.compile("Evidence")).last.click()
        d.pause(800)
        d.top(d.bubble("Which devices are running low on disk space?"))
        d.pause(800)
        d.shot("copilot-evidence")
        d.ask("Create upgrade orders for devices with low storage")
        d.shot("copilot-action-proposals")
        d.ask("Show me all Globex devices")
        d.shot("copilot-guardrail")

        print("4. Devices")
        d.go("/devices")
        d.shot("devices")
        d.go("/devices/8NM23J95R5I6")
        d.pause(1000)
        d.shot("device-detail")
        d.shot("device-detail-full", full=True)

        print("5. Insights")
        d.go("/insights")
        d.shot("insights")

        print("6. Approval Center")
        d.go("/actions")
        page.get_by_text(re.compile("Create Upgrade Order", re.I)).first.click()
        d.pause(1200)
        d.shot("approval-center")
        page.get_by_role("button", name=re.compile("Approve")).first.click()
        page.get_by_text(re.compile("Approved")).first.wait_for(timeout=30_000)
        d.pause(800)
        d.top(page.get_by_text(re.compile("Justification", re.I)).first, 160)
        d.pause(900)
        d.shot("approval-executed")

        print("7. Agent Activity")
        d.go("/activity")
        page.get_by_text("Create upgrade orders for devices with low storage").first.click()
        page.get_by_text("Execution mode", exact=False).first.wait_for()
        d.pause(1200)
        d.shot("agent-activity")
        step = page.get_by_role("button", name=re.compile("find_low_storage_devices")).first
        step.click()
        d.pause(600)
        d.top(step, 120)
        d.pause(900)
        d.shot("agent-activity-tool-step")
        page.get_by_role("button", name="Audit log").click()
        d.pause(1500)
        d.shot("audit-log")

        print("8. Settings")
        d.go("/settings")
        d.shot("settings")
        d.pause(1500)

        video = page.video
        ctx.close()
        browser.close()
        if video:
            target = out / "fleetpilot-walkthrough.webm"
            shutil.move(video.path(), target)
            print("  saved", target.relative_to(ROOT) if target.is_relative_to(ROOT) else target)
        shutil.rmtree(video_dir, ignore_errors=True)
    make_gif(d.shots, out / "fleetpilot-tour.gif")


def make_gif(shots: list[Path], target: Path, width: int = 960) -> None:
    """Slideshow GIF of the viewport screenshots (full-page captures are skipped)."""
    from PIL import Image

    frames = []
    for p in shots:
        if p.stem.endswith("-full"):
            continue
        im = Image.open(p).convert("RGB")
        im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
        frames.append(im.convert("P", palette=Image.ADAPTIVE, colors=256))
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=2200, loop=0, optimize=True)
    print("  saved", target.relative_to(ROOT) if target.is_relative_to(ROOT) else target)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo"))
    a = ap.parse_args()
    run(a.base_url, Path(a.out).resolve())


if __name__ == "__main__":
    main()
