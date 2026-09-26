"""메시지 4: 미 천연가스(헨리허브 선물 근월물 NG=F) 차트, 2020년 12월 이후.

단독 실행: python natgas.py [--dry-run]
"""

from __future__ import annotations

import sys

from briefing_common import OUT_DIR, Message, Prepared, date_suffix, run_standalone, setup_korean_font

NAME = "미 천연가스"
FAIL_TEXT = "미 천연가스 차트 확인 실패"
TICKER = "NG=F"
START = "2020-12-01"


def fetch():
    import pandas as pd
    import yfinance as yf

    last = None
    for _ in range(2):
        try:
            df = yf.download(TICKER, start=START, progress=False, auto_adjust=False, threads=False)
            close = df["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            s = close.dropna().astype(float)
            if len(s) < 2:
                raise ValueError("빈 데이터")
            s.index = pd.to_datetime(s.index).tz_localize(None)
            return s
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"Yahoo {TICKER} 실패: {type(last).__name__}: {last}"[:200])


def draw(s):
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure

    fig = Figure(figsize=(10, 5.5), facecolor="white")
    ax = fig.add_subplot()
    ax.plot(s.index, s.values, color="black", lw=3.0, label="미 천연가스(헨리허브 선물)")
    ax.legend(loc="upper center", frameon=False, fontsize=12)
    ax.grid(False)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
    ax.text(0, 1.02, "(달러/MMBtu)", transform=ax.transAxes, fontsize=10)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "m4_natgas.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    return path


def prepare() -> Prepared:
    s = fetch()
    last, prev, d = s.iloc[-1], s.iloc[-2], s.index[-1]
    chg = last - prev
    extra = f"{chg:+.3f}, {chg / prev * 100:+.1f}%"
    when = date_suffix(d)
    if when:
        extra += f", {when}"
    cap = f"미 천연가스\n${last:.3f}/MMBtu ({extra})"
    return Prepared([Message("photo", cap, draw(s), "차트")])


if __name__ == "__main__":
    setup_korean_font()
    sys.exit(run_standalone(sys.modules[__name__]))
