"""메시지 1: CNN Fear & Greed 지수 반원 게이지.

단독 실행: python fear_greed.py [--dry-run]
"""

from __future__ import annotations

import math
import sys

import requests

from briefing_common import OUT_DIR, UA, Message, Prepared, md, run_standalone, setup_korean_font, today_kst

NAME = "Fear & Greed"
FAIL_TEXT = "Fear & Greed 항목 확인 실패"
URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
HEADERS = {**UA, "Referer": "https://www.cnn.com/markets/fear-and-greed", "Accept": "application/json"}
BANDS = [(0, 25, "Extreme Fear"), (25, 45, "Fear"), (45, 55, "Neutral"), (55, 75, "Greed"), (75, 100, "Extreme Greed")]
KEYS = ["score", "previous_close", "previous_1_week", "previous_1_month", "previous_1_year"]


def fetch() -> dict[str, int]:
    last = None
    for _ in range(2):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=30)
            r.raise_for_status()
            fg = r.json()["fear_and_greed"]
            return {k: round(float(fg[k])) for k in KEYS}
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"CNN 응답 실패: {type(last).__name__}")


def zone(v: int) -> str:
    # CNN 기준: 0-24, 25-44, 45-55, 56-75, 76-100
    if v < 25:
        return "Extreme Fear"
    if v < 45:
        return "Fear"
    if v <= 55:
        return "Neutral"
    if v <= 75:
        return "Greed"
    return "Extreme Greed"


def history_line(v: dict[str, int]) -> str:
    return (f"전일 {v['previous_close']} / 1주 전 {v['previous_1_week']} / "
            f"1개월 전 {v['previous_1_month']} / 1년 전 {v['previous_1_year']}")


def draw(v: dict[str, int]):
    from matplotlib.figure import Figure
    from matplotlib.patches import Circle, Wedge

    s, z = v["score"], zone(v["score"])
    fig = Figure(figsize=(8, 5.2), facecolor="white")
    ax = fig.add_subplot()

    def ang(x: float) -> float:
        return 180 - 1.8 * x

    for lo, hi, name in BANDS:
        color = "#eeeeee"
        if name == z:
            color = "#fdd9b5" if hi <= 45 else "#c8ebc8" if lo >= 55 else "#dddddd"
        ax.add_patch(Wedge((0, 0), 1, ang(hi), ang(lo), width=0.35, facecolor=color, edgecolor="white", lw=3))
    for t in (0, 25, 50, 75, 100):
        th = math.radians(ang(t))
        ax.text(1.1 * math.cos(th), 1.1 * math.sin(th), str(t), ha="center", va="center", fontsize=11, color="#555")
    th = math.radians(ang(s))
    ax.plot([0, 0.85 * math.cos(th)], [0, 0.85 * math.sin(th)], color="black", lw=4, solid_capstyle="round")
    ax.add_patch(Circle((0, 0), 0.05, color="black"))
    ax.text(0, -0.25, str(s), ha="center", va="center", fontsize=44, fontweight="bold")
    ax.text(0, -0.5, z, ha="center", va="center", fontsize=18)
    ax.text(0, -0.72, history_line(v), ha="center", fontsize=11, color="#666")
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-0.85, 1.2)
    ax.set_aspect("equal")
    ax.axis("off")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "m1_fear_greed.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    return path


def prepare() -> Prepared:
    v = fetch()
    s = v["score"]
    cap = f"Fear & Greed 지수 ({md(today_kst())} 기준)\n현재 {s} · {zone(s)}\n{history_line(v)}"
    if s <= 25 or s >= 75:
        cap += "\n※ 극단 구간"
    try:
        return Prepared([Message("photo", cap, draw(v), "게이지")])
    except Exception as e:  # noqa: BLE001  이미지 실패 시 캡션을 텍스트로
        return Prepared([Message("text", cap, label="게이지(텍스트 대체)")], [f"이미지 생성 실패: {e}"])


if __name__ == "__main__":
    setup_korean_font()
    sys.exit(run_standalone(sys.modules[__name__]))
