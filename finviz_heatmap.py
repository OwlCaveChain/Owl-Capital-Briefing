"""메시지 3: 핀비즈 S&P 500 히트맵 3종(1일, 4주, 연초 대비).

단독 실행: python finviz_heatmap.py [--dry-run]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from briefing_common import OUT_DIR, UA, Message, Prepared, md, run_standalone, today_kst

NAME = "핀비즈 히트맵"
FAIL_TEXT = "핀비즈 히트맵 확인 실패"
MAPS = [
    ("https://finviz.com/map", "1일", "m3_finviz_1d.png"),
    ("https://finviz.com/map?t=sec&st=w4", "4주", "m3_finviz_4w.png"),
    ("https://finviz.com/map?t=sec&st=ytd", "연초 대비", "m3_finviz_ytd.png"),
]
CHROMIUM = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
PROXY_CA = Path("/root/.ccr/agent-proxy-ca.crt")
CLOSE_SELECTORS = [
    "button:has-text('Accept')", "button:has-text('AGREE')", "button:has-text('Close')",
    "[aria-label='Close']", "[aria-label='close']", ".modal-close", "button.close", "[class*='close' i]",
]
MAP_SELECTORS = ["canvas.hover-canvas", "canvas", "#map"]


def ensure_proxy_ca() -> None:
    """NSS 인증서 저장소에 프록시 CA가 없으면 certutil로 가져온다(클라우드 세션 전용)."""
    if not PROXY_CA.exists():
        return
    db = f"sql:{Path.home() / '.pki' / 'nssdb'}"
    try:
        listed = subprocess.run(["certutil", "-d", db, "-L"], capture_output=True, text=True).stdout
        if "ccr-agent-proxy" in listed:
            return
        (Path.home() / ".pki" / "nssdb").mkdir(parents=True, exist_ok=True)
        subprocess.run(["certutil", "-d", db, "-A", "-t", "C,,", "-n", "ccr-agent-proxy", "-i", str(PROXY_CA)],
                       check=False, capture_output=True)
    except FileNotFoundError:
        print("[경고] certutil 없음: 프록시 CA 가져오기 생략", file=sys.stderr)


def close_popups(page) -> None:
    for sel in CLOSE_SELECTORS:
        try:
            for el in page.query_selector_all(sel)[:3]:
                if el.is_visible():
                    el.click(timeout=1500)
                    page.wait_for_timeout(400)
        except Exception:  # noqa: BLE001
            pass
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass


def capture(page, url: str, out: Path) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("canvas", timeout=30000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(3000)
    close_popups(page)
    page.wait_for_timeout(800)
    for sel in MAP_SELECTORS:
        el = page.query_selector(sel)
        box = el.bounding_box() if el else None
        if box and box["width"] > 300 and box["height"] > 200:
            page.screenshot(path=str(out), clip=box)
            return
    raise RuntimeError("지도 영역을 찾지 못함")


def prepare() -> Prepared:
    from playwright.sync_api import sync_playwright

    ensure_proxy_ca()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date = f"{md(today_kst())} 기준"
    messages, errors, failed = [], [], []
    with sync_playwright() as pw:
        kw = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if Path(CHROMIUM).exists():
            kw["executable_path"] = CHROMIUM
        browser = pw.chromium.launch(**kw)
        try:
            ctx = browser.new_context(viewport={"width": 1400, "height": 900}, user_agent=UA["User-Agent"])
            page = ctx.new_page()
            for url, label, fname in MAPS:
                out = OUT_DIR / fname
                try:
                    capture(page, url, out)
                    messages.append(Message("photo", f"{date} S&P 500 히트맵 ({label})", out, label))
                except Exception as e:  # noqa: BLE001
                    failed.append(label)
                    errors.append(f"{label} {type(e).__name__}: {e}"[:200])
        finally:
            browser.close()
    if not messages:
        return Prepared([Message("text", FAIL_TEXT, label="실패 알림")], errors)
    if failed:
        messages.append(Message("text", f"{FAIL_TEXT} ({', '.join(failed)})", label="부분 실패 알림"))
    return Prepared(messages, errors)


if __name__ == "__main__":
    sys.exit(run_standalone(sys.modules[__name__]))
