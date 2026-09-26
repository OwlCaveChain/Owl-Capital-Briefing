"""메시지 1: Fear & Greed 지수 게이지 + 1년 타임라인.

데이터: CNN graphdata(fear_and_greed, fear_and_greed_historical). 값은 CNN 페이지처럼 소수점 이하를 버린다.
단독 실행: python fear_greed.py [--dry-run]
"""

from __future__ import annotations

import math
import sys
from zoneinfo import ZoneInfo

import requests

from briefing_common import OUT_DIR, UA, Message, Prepared, add_title, run_standalone, setup_korean_font

NAME = "Fear & Greed"
FAIL_TEXT = "Fear & Greed 항목 확인 실패"
URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
HEADERS = {**UA, "Referer": "https://www.cnn.com/markets/fear-and-greed", "Accept": "application/json"}
ET = ZoneInfo("America/New_York")

# (시작, 끝, 이름) — 게이지 경계. 정수 구간은 0-24 / 25-44 / 45-55 / 56-75 / 76-100
BANDS = [(0, 25, "Extreme Fear"), (25, 45, "Fear"), (45, 55, "Neutral"), (55, 75, "Greed"), (75, 100, "Extreme Greed")]
HISTORY = [("previous_close", "전일"), ("previous_1_week", "1주 전"),
           ("previous_1_month", "1개월 전"), ("previous_1_year", "1년 전")]

BASE = "#E9E9E9"
STYLE = {  # 구간 계열별 (채우기, 테두리)
    "fear": ("#FDE2CC", "#F08A24"),
    "neutral": ("#DCDCDC", "#8C8C8C"),
    "greed": ("#CDEFDF", "#23A566"),
}
LINE_BLUE = "#1F5FD1"
BOLD = 600  # NanumGothicBold.ttf 는 weight 600 으로 등록된다


def heavy(color: str, width: float = 0.9):
    """나눔고딕 Bold가 가늘어 보여 같은 색 윤곽선으로 굵기를 더한다."""
    from matplotlib import patheffects

    return [patheffects.withStroke(linewidth=width, foreground=color)]


def cnn_int(x: float) -> int:
    """CNN 페이지 표기와 같게 소수점 이하 버림(59.6 → 59)."""
    return int(math.floor(float(x) + 1e-9))


def fetch() -> dict:
    last = None
    for _ in range(2):
        try:
            r = requests.get(URL, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"CNN 응답 실패: {type(last).__name__}")


def zone(v: int) -> str:
    if v < 25:
        return "Extreme Fear"
    if v < 45:
        return "Fear"
    if v <= 55:
        return "Neutral"
    if v <= 75:
        return "Greed"
    return "Extreme Greed"


def family(name: str) -> str:
    return "fear" if "Fear" in name else "greed" if "Greed" in name else "neutral"


def gauge_caption(v: dict[str, int]) -> str:
    """게이지 캡션: '전일 → 현재'."""
    return f"{v['previous_close']} → {v['score']}"


def timeline_caption(v: dict[str, int]) -> str:
    """타임라인 캡션: '1주 전 N / 1개월 전 N / 1년 전 N'."""
    return " / ".join(f"{label} {v[key]}" for key, label in HISTORY[1:])


# ---------------------------------------------------------------------------
# 게이지
# ---------------------------------------------------------------------------


def draw_gauge(v: dict[str, int]):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Circle, Polygon, Wedge

    s, z = v["score"], zone(v["score"])
    # 텔레그램 대화창에서 잘리지 않도록 가로형(약 1.4:1). 제목은 반원 왼쪽 위 빈 공간에 둔다.
    xlim, ylim = (-1.2, 1.2), (-0.64, 1.05)
    width_in = 8.0
    fig = Figure(figsize=(width_in, width_in * (ylim[1] - ylim[0]) / (xlim[1] - xlim[0])), facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")

    def ang(x: float) -> float:
        return 180 - 1.8 * x

    def polar(r: float, x: float):
        t = math.radians(ang(x))
        return r * math.cos(t), r * math.sin(t)

    # 부채꼴 다섯 개, 사이 흰 간격
    r_out, r_in, gap = 1.0, 0.62, 0.9
    for lo, hi, name in BANDS:
        if name == z:
            fill, edge = STYLE[family(name)]
            lw = 2.2
        else:
            fill, edge, lw = BASE, BASE, 0
        ax.add_patch(Wedge((0, 0), r_out, ang(hi) + gap, ang(lo) - gap, width=r_out - r_in,
                           facecolor=fill, edgecolor=edge, lw=lw, joinstyle="round"))
        mid = (lo + hi) / 2
        x, y = polar((r_out + r_in) / 2, mid)
        ax.text(x, y, name.upper(), ha="center", va="center", rotation=ang(mid) - 90,
                rotation_mode="anchor", fontsize=10.5 if name != "Neutral" else 9.5,
                fontweight=BOLD, color=(label_color := "#333333" if name == z else "#7A7A7A"),
                path_effects=heavy(label_color, 0.4))

    # 안쪽 눈금: 점과 숫자
    for t in range(0, 101, 5):
        if t % 25:
            x, y = polar(0.575, t)
            ax.add_patch(Circle((x, y), 0.008, color="#B5B5B5"))
    for t in (0, 25, 50, 75, 100):
        x, y = polar(0.52, t)
        ax.text(x, y, str(t), ha="center", va="center", fontsize=11, color="#666666")

    # 바늘과 중앙 원
    t = math.radians(ang(s))
    tip = (0.78 * math.cos(t), 0.78 * math.sin(t))
    nx, ny = -math.sin(t) * 0.035, math.cos(t) * 0.035
    ax.add_patch(Polygon([tip, (nx, ny), (-nx, -ny)], closed=True, color="black", zorder=5))
    ax.add_patch(Circle((0, 0), 0.165, facecolor="white", edgecolor="black", lw=2.5, zorder=6))
    ax.text(0, -0.005, str(s), ha="center", va="center", fontsize=34, fontweight=BOLD, zorder=7,
            path_effects=heavy("black", 0.9))

    # 2x2 표(한 칸 한 줄): 라벨 · 구간 이름 ····· (값)
    renderer = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    cells = [(-1.13, -0.33), (0.09, -0.33), (-1.13, -0.54), (0.09, -0.54)]
    width, badge_r, name_dx = 1.04, 0.075, 0.27
    for (key, label), (x0, y0) in zip(HISTORY, cells):
        val = v[key]
        name = zone(val)
        fill, edge = STYLE[family(name)]
        ax.text(x0, y0, label, ha="left", va="center", fontsize=12, color="#777777")
        name_t = ax.text(x0 + name_dx, y0, name, ha="left", va="center", fontsize=15, fontweight=BOLD,
                         color="#222222", path_effects=heavy("#222222", 0.45))
        bx = x0 + width - badge_r
        ax.add_patch(Circle((bx, y0), badge_r, facecolor=fill, edgecolor=edge, lw=2))
        ax.text(bx, y0 - 0.005, str(val), ha="center", va="center", fontsize=13, fontweight=BOLD,
                color="#222222", path_effects=heavy("#222222", 0.35))
        text_end = inv.transform(name_t.get_window_extent(renderer))[1][0]
        if bx - badge_r - text_end > 0.12:
            ax.plot([text_end + 0.04, bx - badge_r - 0.04], [y0] * 2, color="#BBBBBB", lw=1.4,
                    linestyle=(0, (1, 3)), dash_capstyle="round")
        if y0 == cells[0][1]:  # 두 줄 사이 구분선
            ax.plot([x0, x0 + width], [y0 - 0.105] * 2, color="#EEEEEE", lw=1)

    add_title(fig, "Fear & Greed Index", fontsize=19)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "m1_fear_greed.png"
    fig.savefig(path, dpi=180, facecolor="white")
    return path


# ---------------------------------------------------------------------------
# 1년 타임라인
# ---------------------------------------------------------------------------


def history_series(data: dict):
    import pandas as pd

    pts = data["fear_and_greed_historical"]["data"]
    idx = pd.to_datetime([p["x"] for p in pts], unit="ms", utc=True).tz_convert(ET).tz_localize(None).normalize()
    s = pd.Series([float(p["y"]) for p in pts], index=idx)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[s.index >= s.index[-1] - pd.Timedelta(days=365)]


def draw_timeline(s):
    import matplotlib.dates as mdates
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FixedLocator

    fig = Figure(figsize=(10, 5.4), facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.03, 0.1, 0.9, 0.78])
    add_title(fig, "Fear & Greed 1년 추이")
    ax.set_ylim(0, 100)
    ax.set_xlim(s.index[0], s.index[-1])
    for level in (25, 50, 75):
        ax.axhline(level, color="#BDBDBD", lw=1, linestyle=(0, (4, 4)), zorder=1)
    ax.plot(s.index, s.values, color=LINE_BLUE, lw=3.0, zorder=3)

    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(FixedLocator([0, 25, 50, 75, 100]))
    ax.tick_params(axis="y", length=0, labelsize=11, labelcolor="#666666", pad=8)

    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
    ticks = [mdates.num2date(t) for t in ax.get_xticks()]
    labels = [f"{t:%b} {t.year}" if (i == 0 or t.month == 1) else f"{t:%b}" for i, t in enumerate(ticks)]
    ax.set_xticks(ax.get_xticks(), labels)
    ax.tick_params(axis="x", length=0, labelsize=11, labelcolor="#666666", pad=8)
    ax.grid(axis="x", color="#E3E3E3", lw=1)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#CCCCCC")

    ax.text(0.01, 77, "^ Extreme Greed", transform=ax.get_yaxis_transform(), fontsize=10, color="#8A8A8A",
            va="bottom", zorder=2)
    ax.text(0.01, 23, "v Extreme Fear", transform=ax.get_yaxis_transform(), fontsize=10, color="#8A8A8A",
            va="top", zorder=2)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "m1_fear_greed_timeline.png"
    fig.savefig(path, dpi=150, facecolor="white")
    return path


# ---------------------------------------------------------------------------


def prepare() -> Prepared:
    data = fetch()
    fg = data["fear_and_greed"]
    v = {"score": cnn_int(fg["score"]), **{k: cnn_int(fg[k]) for k, _ in HISTORY}}

    messages, errors = [], []
    try:
        messages.append(Message("photo", gauge_caption(v), draw_gauge(v), "게이지"))
    except Exception as e:  # noqa: BLE001  이미지 실패 시 캡션을 텍스트로
        messages.append(Message("text", f"Fear & Greed Index\n{gauge_caption(v)}", label="게이지(텍스트 대체)"))
        errors.append(f"게이지 이미지 실패: {type(e).__name__}: {e}")
    try:
        path = draw_timeline(history_series(data))
        messages.append(Message("photo", timeline_caption(v), path, "1-2 타임라인"))
    except Exception as e:  # noqa: BLE001
        messages.append(Message("text", "Fear & Greed 타임라인 확인 실패", label="1-2 타임라인 실패 알림"))
        errors.append(f"타임라인 실패: {type(e).__name__}: {e}")
    return Prepared(messages, errors)


if __name__ == "__main__":
    setup_korean_font()
    sys.exit(run_standalone(sys.modules[__name__]))
