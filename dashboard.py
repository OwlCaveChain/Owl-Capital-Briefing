"""메시지 0: 아침 대시보드 + 섹터 전체표(메시지 1 Fear & Greed 앞에 보낸다).

1) Morning Dashboard: Overnight / Liquidity & credit / Sector leadership(상위 5·하위 3) / Leaders(목록이 정해지기 전까지 숨김)
2) Sector leadership — full: 22개 ETF를 SPY 대비 1개월 상대강도 순위순으로(매일 같은 1440px 표).
3) 월요일에만 주간판: 같은 순위순으로 1주·연초 대비·200일선·52주 고점. 사이트에는 매일 모든 열이 든 표.

이미지는 가로 1440px, 세로 최대 1800px(4:5), 서체 Pretendard(tabular figures). 넘치면 글씨를 줄이지 않고
두 장으로 나눠 한 장씩 낱장으로 보낸다(대시보드: "Overnight + Liquidity" / "Sectors", 전체표: 1–11위 / 12–22위).
캡션은 없다.

출처: 가격 yfinance, 금리·스프레드 FRED, CP 91일 ECOS(817Y002/010503000). 실패한 지표는 "–"로 두고
나머지는 그대로 그린다. 로그에 [출처]·[경고]를 남긴다. 섹터 순위는 data/sector_rank.csv 에 매일 쌓는다.

단독 실행: python dashboard.py [--dry-run] [--monday | --weekday]
  --monday/--weekday  요일과 관계없이 주간판을 보낸다/안 보낸다(기본은 한국시간 오늘이 월요일인지).
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from briefing_common import OUT_DIR, PRETENDARD, ROOT, Message, Prepared, now_kst, pretendard_ok, run_standalone

NAME = "아침 대시보드"
FAIL_TEXT = "아침 대시보드 확인 실패"
DATA_DIR = ROOT / "data"
RANK_CSV = DATA_DIR / "sector_rank.csv"

# (표시 이름, yfinance 티커, 소수 자릿수)
OVERNIGHT = [
    ("S&P500", "^GSPC", 0), ("나스닥", "^IXIC", 0),
    ("러셀2000", "^RUT", 0), ("VIX", "^VIX", 2),
    ("금", "GC=F", 0), ("비트코인", "BTC-USD", 0),
    ("EWY", "EWY", 2), ("원달러", "KRW=X", 1),
]

# (키, 표시 이름, 종류) 종류 rate: 금리·스프레드(%p), index: 지수(%)
LIQUIDITY = [
    ("DGS2", "미 2년물", "rate"),
    ("DGS10", "미 10년물", "rate"),
    ("DFII10", "10년 실질금리", "rate"),
    ("DXY", "달러인덱스", "index"),
    ("BAMLH0A0HYM2", "하이일드 스프레드", "rate"),
    ("CP91", "한국 CP 91일", "rate"),
]
ECOS_CP91 = ("817Y002", "010503000")  # 1.3.2.1. 시장금리(일별) / CP(91일), 연%
TREND_FLAT = {"rate": 0.05, "index": 0.5}  # 1개월 변화가 이보다 작으면 →(금리 %p, 지수 %)

GROUPS = {"map": ("전체 지도", "#9AA0A6"), "theme": ("관심 테마", "#12A4A4"), "cycle": ("경기 신호", "#E3A21A")}
SECTORS = [
    ("XLK", "기술", "map"), ("XLC", "통신서비스", "map"), ("XLY", "경기소비재", "map"),
    ("XLF", "금융", "map"), ("XLI", "산업재", "map"), ("XLE", "에너지", "map"), ("XLB", "소재", "map"),
    ("XLV", "헬스케어", "map"), ("XLP", "필수소비재", "map"), ("XLU", "유틸리티", "map"),
    ("XLRE", "부동산", "map"),
    ("SMH", "반도체", "theme"), ("DTCR", "데이터센터", "theme"), ("GRID", "전력망", "theme"),
    ("URA", "우라늄", "theme"), ("IGV", "소프트웨어", "theme"), ("XBI", "바이오", "theme"),
    ("KRE", "지역은행", "cycle"), ("ITB", "주택건설", "cycle"), ("IYT", "운송", "cycle"),
    ("XRT", "소매", "cycle"), ("GDX", "금광", "cycle"),
]
BENCH = "SPY"
LOOKBACK_1M = 21  # 거래일
LOOKBACK_1W = 5
RANK_BACKFILL = 10  # 순위 CSV가 비었을 때 가격으로 거슬러 채우는 거래일 수

# 주도주 신호: 종목 목록이 정해지기 전까지 비워 둔다(비어 있으면 대시보드에서 구역을 숨긴다)
LEADERS: list[str] = []


def is_monday_layout() -> bool:
    mode = os.environ.get("DASHBOARD_LAYOUT", "").strip().lower()
    if mode in ("monday", "weekday"):
        return mode == "monday"
    return now_kst().weekday() == 0


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------


@dataclass
class Row:
    """표 한 줄의 원자료. 값이 없으면 None(그림에는 "–")."""
    name: str
    value: float | None = None
    change: float | None = None  # overnight·index: %, rate: %p
    trend: str | None = None  # ↑ → ↓
    decimals: int = 2
    kind: str = "index"


@dataclass
class SectorRow:
    ticker: str
    name: str
    group: str
    rank: int | None = None
    rs: float | None = None
    d1: float | None = None
    w1: float | None = None
    m1: float | None = None
    ytd: float | None = None
    above50: bool | None = None
    above200: bool | None = None
    from_high: float | None = None
    rank_change: int | None = None  # 전주 대비(+면 순위 상승)


@dataclass
class Collected:
    overnight: list[Row] = field(default_factory=list)
    liquidity: list[Row] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sectors: list[SectorRow] = field(default_factory=list)  # 순위순
    sector_date: object = None
    errors: list[str] = field(default_factory=list)


def _yahoo_closes(tickers: list[str], start: dt.date, adjust: bool):
    """yfinance 종가 표(열=티커). 한 번에 받고, 빠진 티커는 하나씩 한 번 더 받는다."""
    import pandas as pd
    import yfinance as yf

    def download(tks):
        df = yf.download(tks, start=start.isoformat(), progress=False, auto_adjust=adjust, threads=False,
                         group_by="column")
        if df is None or df.empty:
            return pd.DataFrame()
        close = df["Close"]
        if isinstance(close, pd.Series):
            close = close.to_frame(tks[0])
        close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
        return close

    out = pd.DataFrame()
    try:
        out = download(tickers)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] yfinance 일괄 받기 실패: {type(e).__name__}: {e}"[:200], file=sys.stderr)
    for t in tickers:
        if t in out.columns and out[t].dropna().size >= 2:
            continue
        try:
            one = download([t])
            if t in one.columns:
                out = out.join(one[[t]], how="outer") if t not in out.columns else out.combine_first(one[[t]])
        except Exception as e:  # noqa: BLE001
            print(f"[경고] yfinance {t} 실패: {type(e).__name__}: {e}"[:200], file=sys.stderr)
    return out.sort_index()


def value_before(s, when):
    """when 이전(같은 날 포함) 마지막 값. 없으면 None."""
    s = s[s.index <= when]
    return float(s.iloc[-1]) if len(s) else None


def trend_arrow(change: float | None, kind: str) -> str | None:
    if change is None:
        return None
    if abs(change) < TREND_FLAT[kind]:
        return "→"
    return "↑" if change > 0 else "↓"


def collect_overnight(c: Collected) -> None:
    start = now_kst().date() - dt.timedelta(days=15)
    closes = _yahoo_closes([t for _, t, _ in OVERNIGHT], start, adjust=False)
    print("[출처] Overnight: yfinance " + " ".join(t for _, t, _ in OVERNIGHT))
    for name, ticker, dec in OVERNIGHT:
        row = Row(name, decimals=dec)
        s = closes[ticker].dropna() if ticker in closes.columns else None
        if s is None or len(s) < 2:
            c.errors.append(f"Overnight {name}({ticker}) 데이터 없음")
            print(f"[경고] Overnight {name}({ticker}) 데이터 없음 → –", file=sys.stderr)
        else:
            row.value = float(s.iloc[-1])
            row.change = (s.iloc[-1] / s.iloc[-2] - 1) * 100
        c.overnight.append(row)


def _fred(series_id: str):
    import charts

    return charts.fetch_chain(series_id, [(f"fred_{series_id}", "FRED", lambda: charts.fetch_fred(series_id))]).series


def _cp91():
    import charts

    stat, item = ECOS_CP91
    key = f"ecos_{stat}_{item}"
    start = now_kst().date() - dt.timedelta(days=120)
    return charts.fetch_chain("한국 CP 91일", [(key, "ECOS", lambda: charts.fetch_ecos(stat, item, key, start))]).series


def _dxy():
    s = _yahoo_closes(["DX-Y.NYB"], now_kst().date() - dt.timedelta(days=60), adjust=False)
    if "DX-Y.NYB" not in s.columns or s["DX-Y.NYB"].dropna().size < 2:
        raise ValueError("yfinance DX-Y.NYB 빈 데이터")
    return s["DX-Y.NYB"].dropna()


SOURCES = {"DGS2": ("FRED DGS2", lambda: _fred("DGS2")), "DGS10": ("FRED DGS10", lambda: _fred("DGS10")),
           "DFII10": ("FRED DFII10", lambda: _fred("DFII10")), "DXY": ("yfinance DX-Y.NYB", _dxy),
           "BAMLH0A0HYM2": ("FRED BAMLH0A0HYM2", lambda: _fred("BAMLH0A0HYM2")),
           "CP91": ("ECOS 817Y002/010503000", _cp91)}


def collect_liquidity(c: Collected) -> None:
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(LIQUIDITY)) as ex:
        futures = {key: ex.submit(SOURCES[key][1]) for key, _, _ in LIQUIDITY}
    series = {}
    for key, name, kind in LIQUIDITY:
        src = SOURCES[key][0]
        row = Row(name, decimals=2, kind=kind)
        try:
            s = futures[key].result().dropna()
            if len(s) < 2:
                raise ValueError("값이 2개 미만")
            series[key] = s
            last, prev = float(s.iloc[-1]), float(s.iloc[-2])
            month_ago = value_before(s, s.index[-1] - pd.Timedelta(days=30))
            row.value = last
            if kind == "rate":
                row.change = last - prev
                m = None if month_ago is None else last - month_ago
            else:
                row.change = (last / prev - 1) * 100
                m = None if month_ago is None else (last / month_ago - 1) * 100
            row.trend = trend_arrow(m, kind)
            print(f"[출처] {name}: {src} ({s.index[-1]:%Y-%m-%d})")
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"[:160]
            c.errors.append(f"{name}({src}) {msg}")
            print(f"[경고] {name}: {src} 실패 → – ({msg})", file=sys.stderr)
        c.liquidity.append(row)

    # 경고: 하이일드 스프레드 4주 연속 확대, 2년물 1개월 새 ±0.25%p 이상
    hy = series.get("BAMLH0A0HYM2")
    if hy is not None:
        weekly = hy.resample("W-FRI").last().dropna().tail(5)
        if len(weekly) == 5 and (weekly.diff().dropna() > 0).all():
            c.warnings.append(f"하이일드 스프레드 4주 연속 확대 ({weekly.iloc[0]:.2f} → {weekly.iloc[-1]:.2f}%)")
    two = series.get("DGS2")
    if two is not None:
        ago = value_before(two, two.index[-1] - pd.Timedelta(days=30))
        if ago is not None and abs(two.iloc[-1] - ago) >= 0.25 - 1e-9:
            c.warnings.append(f"미 2년물 1개월 {fmt_signed(two.iloc[-1] - ago, 2)}%p")


def sector_metrics(closes, i: int) -> dict[str, dict]:
    """closes(열=티커, SPY 포함, SPY 거래일 기준) 의 i번째 날 기준 1개월 상대강도와 순위."""
    spy = closes[BENCH]
    out = {}
    if i - LOOKBACK_1M < 0:
        return out
    spy_m = spy.iloc[i] / spy.iloc[i - LOOKBACK_1M] - 1
    for t, _, _ in SECTORS:
        if t not in closes.columns:
            continue
        now, ago = closes[t].iloc[i], closes[t].iloc[i - LOOKBACK_1M]
        if now == now and ago == ago and ago:
            out[t] = {"rs": (now / ago) / (1 + spy_m) - 1}
    for rank, t in enumerate(sorted(out, key=lambda t: -out[t]["rs"]), 1):
        out[t]["rank"] = rank
    return out


def load_ranks():
    import pandas as pd

    if not RANK_CSV.exists():
        return pd.DataFrame(columns=[t for t, _, _ in SECTORS])
    df = pd.read_csv(RANK_CSV, parse_dates=["date"]).set_index("date")
    return df


def save_ranks(df) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = df.sort_index()
    out = df.reindex(columns=[t for t, _, _ in SECTORS]).astype("Int64")
    out.index = out.index.strftime("%Y-%m-%d")
    out.index.name = "date"
    out.to_csv(RANK_CSV)


def collect_sectors(c: Collected) -> None:
    import pandas as pd

    tickers = [BENCH] + [t for t, _, _ in SECTORS]
    start = now_kst().date() - dt.timedelta(days=420)  # 연초 대비·200일선·52주 고점
    raw = _yahoo_closes(tickers, start, adjust=True)
    print(f"[출처] Sector: yfinance(수정주가) {BENCH} + {len(SECTORS)}개 ETF")
    rows = {t: SectorRow(t, n, g) for t, n, g in SECTORS}
    if BENCH not in raw.columns or raw[BENCH].dropna().size <= LOOKBACK_1M:
        c.errors.append(f"섹터: {BENCH} 데이터 없음(순위 계산 불가)")
        print(f"[경고] 섹터: {BENCH} 데이터 없음 → 순위 –", file=sys.stderr)
        c.sectors = list(rows.values())
        return
    spy = raw[BENCH].dropna()
    closes = raw.reindex(spy.index).ffill(limit=3)
    missing = [t for t, _, _ in SECTORS if t not in raw.columns or raw[t].dropna().size <= LOOKBACK_1M]
    for t in missing:
        c.errors.append(f"섹터 {t} 데이터 없음")
        print(f"[경고] 섹터 {t} 데이터 없음 → –", file=sys.stderr)

    last = len(closes) - 1
    day = closes.index[last]
    c.sector_date = day
    cur = sector_metrics(closes, last)

    # 순위 누적: 오늘 순위를 쓰고, 기록이 없는 최근 거래일은 가격으로 거슬러 채운다
    ranks = load_ranks()
    for i in range(max(LOOKBACK_1M, last - RANK_BACKFILL), last + 1):
        d = closes.index[i]
        if d in ranks.index and i != last:
            continue
        m = cur if i == last else sector_metrics(closes, i)
        ranks.loc[d] = pd.Series({t: v["rank"] for t, v in m.items()}, dtype="float")
    save_ranks(ranks)
    week_ago = ranks[ranks.index <= day - pd.Timedelta(days=7)]
    prev = week_ago.iloc[-1] if len(week_ago) else None

    for t, r in rows.items():
        if t in missing:
            continue
        s = closes[t]
        v = s.iloc[last]
        r.rank, r.rs = cur.get(t, {}).get("rank"), cur.get(t, {}).get("rs")
        r.d1 = (v / s.iloc[last - 1] - 1) * 100
        r.w1 = (v / s.iloc[last - LOOKBACK_1W] - 1) * 100
        r.m1 = (v / s.iloc[last - LOOKBACK_1M] - 1) * 100
        prev_year = s[s.index.year < day.year].dropna()
        r.ytd = (v / prev_year.iloc[-1] - 1) * 100 if len(prev_year) else None
        own = raw[t].dropna()
        own = own[own.index <= day]
        if len(own) >= 50:
            r.above50 = bool(own.iloc[-1] > own.tail(50).mean())
        if len(own) >= 200:
            r.above200 = bool(own.iloc[-1] > own.tail(200).mean())
        if len(own) >= 200:
            r.from_high = (own.iloc[-1] / own.tail(252).max() - 1) * 100
        if prev is not None and r.rank is not None and prev.get(t) == prev.get(t) and prev.get(t) is not None:
            r.rank_change = int(prev[t]) - r.rank
    c.sectors = sorted(rows.values(), key=lambda r: (r.rank is None, r.rank or 0))


def collect() -> Collected:
    from concurrent.futures import ThreadPoolExecutor

    c = Collected()
    jobs = [("Overnight", collect_overnight), ("Liquidity", collect_liquidity), ("Sector", collect_sectors)]
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futures = [(name, ex.submit(fn, c)) for name, fn in jobs]
    for name, f in futures:
        try:
            f.result()
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc(file=sys.stderr)
            c.errors.append(f"{name} {type(e).__name__}: {e}"[:200])
    if not c.overnight:
        c.overnight = [Row(n, decimals=d) for n, _, d in OVERNIGHT]
    if not c.liquidity:
        c.liquidity = [Row(n, decimals=2, kind=k) for _, n, k in LIQUIDITY]
    if not c.sectors:
        c.sectors = [SectorRow(t, n, g) for t, n, g in SECTORS]
    return c


# ---------------------------------------------------------------------------
# 그림(Pillow): 가로 1440px, 세로 최대 1800px(4:5). 글씨는 1080px 기준 크기의 4/3 배
#   (제목 70, 구역 제목 54, 핵심 숫자·티커 54, 본문 48 = 1080px 기준 52·40·40·36)
# 서체는 저장소 fonts/ 의 Pretendard(숫자는 tabular figures). 없으면 나눔고딕
# ---------------------------------------------------------------------------

WIDTH = 1440
MAX_H = 1800
PAD = 24  # 좌우 여백(내용이 폭을 최대한 쓰도록 작게)
TOP, BOTTOM = 28, 24
UP, DOWN, FLAT = "#D12A22", "#1A56D6", "#8A9099"
INK, SUB, RULE, STRIPE, WARN = "#111418", "#5E6673", "#D3D8DF", "#EAEEF3", "#B4442F"
STRONG_RULE = "#4B5360"  # 대시보드 섹터 구역 상위 5 / 하위 3 사이 굵은 선
SIZE_TITLE, SIZE_HEAD, SIZE_BODY, SIZE_NUM = 70, 54, 48, 54
ROW_H = 80  # 표 한 줄 높이(54px 숫자 기준)
COL_GAP = 52
FULL_GAP = 34  # 섹터 전체표 열 간격(평일 6개 열이 1440px에 들어가도록)

# 굵기 r|m|b → fonts/ 의 Pretendard 파일, 없으면 나눔고딕(briefing_common.pretendard_ok 가 [경고])
WEIGHT_FILES = {"r": "Regular", "m": "Medium", "b": "Bold"}
NANUM = {"r": "NanumGothic.ttf", "m": "NanumGothic.ttf", "b": "NanumGothicBold.ttf"}

_fonts: dict = {}


def font(weight: str, size: int):
    from PIL import ImageFont

    key = (weight, size)
    if key not in _fonts:
        path = PRETENDARD[WEIGHT_FILES[weight]] if pretendard_ok() else \
            Path("/usr/share/fonts/truetype/nanum") / NANUM[weight]
        _fonts[key] = ImageFont.truetype(str(path), size)
    return _fonts[key]


def _features():
    from PIL import features

    return ["tnum"] if features.check("raqm") else None  # 숫자 폭을 같게(tabular figures)


FEATURES = None


def fmt_signed(v: float, dec: int) -> str:
    """+1.2 / −1.2 / 0.0 (반올림 뒤 0이면 부호 없음)."""
    s = f"{abs(v):,.{dec}f}"
    if float(s.replace(",", "")) == 0:
        return s
    return ("+" if v > 0 else "−") + s


def sign_color(v: float | None, dec: int) -> str:
    if v is None or float(f"{abs(v):.{dec}f}") == 0:
        return FLAT
    return UP if v > 0 else DOWN


# 글자 조각: (문자열, 굵기 r|m|b, 크기, 색)
Run = tuple


def txt(text: str, size: int = SIZE_BODY, color: str = INK, weight: str = "m") -> list[Run]:
    return [(text, weight, size, color)]


def num(text: str, color: str = INK, size: int = SIZE_NUM, weight: str = "b") -> list[Run]:
    return [(text, weight, size, color)]


def _len(t: str, w: str, s: int) -> float:
    global FEATURES
    if FEATURES is None:
        FEATURES = _features() or []
    return font(w, s).getlength(t, features=FEATURES or None)


def runs_width(runs: list[Run]) -> float:
    return sum(_len(t, w, s) for t, w, s, _ in runs)


class Canvas:
    def __init__(self, width: int = WIDTH, height: int = 4000):
        from PIL import Image, ImageDraw

        self.img = Image.new("RGB", (width, height), "white")
        self.d = ImageDraw.Draw(self.img)
        self.w = width
        self.y = TOP

    def draw_runs(self, x: float, y_mid: float, runs: list[Run], align: str = "left") -> None:
        if align == "right":
            x -= runs_width(runs)
        elif align == "center":
            x -= runs_width(runs) / 2
        for t, w, s, color in runs:
            self.d.text((x, y_mid), t, font=font(w, s), fill=color, anchor="lm", features=FEATURES or None)
            x += _len(t, w, s)

    def title(self, left: str, right: str = "") -> None:
        h = 96
        self.draw_runs(PAD, self.y + h / 2, txt(left, SIZE_TITLE, INK, "b"))
        if right:
            self.draw_runs(self.w - PAD, self.y + h / 2 + 5, txt(right, SIZE_BODY, SUB), "right")
        self.y += h + 14

    def rule(self, gap: int = 30) -> None:
        self.y += gap
        self.d.line([(PAD, self.y), (self.w - PAD, self.y)], fill=RULE, width=3)
        self.y += gap

    def heading(self, text: str, note: str = "") -> None:
        h = 74
        self.draw_runs(PAD, self.y + h / 2, txt(text, SIZE_HEAD, INK, "b"))
        if note:
            self.draw_runs(self.w - PAD, self.y + h / 2, txt(note, SIZE_BODY, SUB), "right")
        self.y += h + 8

    def line(self, runs: list[Run], h: int = 70) -> None:
        self.draw_runs(PAD, self.y + h / 2, runs)
        self.y += h

    def stripe(self, h: int = ROW_H) -> None:
        self.d.rectangle([PAD - 10, self.y, self.w - PAD + 10, self.y + h], fill=STRIPE)

    def table(self, header: list[str], aligns: list[str], rows: list[list[list[Run]]],
              bands: list[str | None] | None = None, stripe: bool = True, gap_after: set[int] = frozenset(),
              col_gap: int = COL_GAP, spare_after: int = 1) -> None:
        """열 너비는 내용에 맞추고, 남는 폭은 이름 열(spare_after) 뒤 간격으로 돌린다. 숫자 열은 오른쪽 정렬.

        gap_after 의 줄 앞에는 굵은 구분선을 긋는다(대시보드 섹터 상위 5 / 하위 3).
        """
        cells = [[txt(h, SIZE_BODY, SUB) for h in header]] + rows
        widths = [max(runs_width(r[i]) for r in cells) for i in range(len(header))]
        band_w = 18 if bands else 0
        inner = self.w - 2 * PAD - band_w
        spare = inner - sum(widths) - col_gap * (len(widths) - 1)
        gaps = [col_gap] * (len(widths) - 1)
        if gaps and spare > 0:
            gaps[min(spare_after, len(gaps) - 1)] += spare  # 이름 열 뒤를 넓혀 숫자 열을 오른쪽에 모은다
        xs, x = [], PAD + band_w
        for i, w in enumerate(widths):
            xs.append(x)
            x += w + (gaps[i] if i < len(gaps) else 0)
        hh = 66
        for i, h in enumerate(cells[0]):
            self.draw_runs(xs[i] + (widths[i] if aligns[i] == "right" else 0), self.y + hh / 2, h,
                           "right" if aligns[i] == "right" else "left")
        self.y += hh
        self.d.line([(PAD, self.y), (self.w - PAD, self.y)], fill=RULE, width=3)
        self.y += 5
        for n, row in enumerate(rows):
            if n in gap_after:
                self.y += 8
                self.d.line([(PAD - 10, self.y), (self.w - PAD + 10, self.y)], fill=STRONG_RULE, width=6)
                self.y += 11
            if stripe and n % 2 == 1:
                self.stripe()
            if bands and bands[n]:
                self.d.rectangle([PAD, self.y + 13, PAD + 9, self.y + ROW_H - 13], fill=bands[n])
            for i, c in enumerate(row):
                self.draw_runs(xs[i] + (widths[i] if aligns[i] == "right" else 0), self.y + ROW_H / 2, c,
                               "right" if aligns[i] == "right" else "left")
            self.y += ROW_H

    @staticmethod
    def needed_width(header, rows, bands=False, col_gap: int = COL_GAP) -> int:
        cells = [[txt(h, SIZE_BODY, SUB) for h in header]] + rows
        widths = [max(runs_width(r[i]) for r in cells) for i in range(len(header))]
        return int(sum(widths) + col_gap * (len(widths) - 1) + 2 * PAD + (18 if bands else 0)) + 1

    def finish(self, path: Path) -> Path:
        self.img.crop((0, 0, self.w, self.y + BOTTOM)).save(path, optimize=True)
        return path


DASH = "–"


def cell_pct(v: float | None, dec: int = 1) -> list[Run]:
    if v is None:
        return num(DASH, FLAT)
    return num(fmt_signed(v, dec) + "%", sign_color(v, dec))


def cell_rate_change(v: float | None) -> list[Run]:
    if v is None:
        return num(DASH, FLAT)
    return num(fmt_signed(v, 2) + "%p", sign_color(v, 2))


def cell_value(v: float | None, dec: int, suffix: str = "", secondary: bool = False) -> list[Run]:
    """현재값. secondary 면 한 단계 작은 회색(보조 숫자)."""
    if v is None:
        return num(DASH, FLAT, SIZE_BODY if secondary else SIZE_NUM)
    if secondary:
        return num(f"{v:,.{dec}f}{suffix}", SUB, SIZE_BODY, "m")
    return num(f"{v:,.{dec}f}{suffix}", INK, SIZE_NUM, "b")


def cell_trend(arrow: str | None) -> list[Run]:
    if arrow is None:
        return num(DASH, FLAT)
    return num(arrow, {"↑": UP, "↓": DOWN}.get(arrow, FLAT))


def cell_rank_change(v: int | None) -> list[Run]:
    if v is None:
        return num(DASH, FLAT)
    if v == 0:
        return num("0", FLAT)
    return num(("▲" if v > 0 else "▼") + str(abs(v)), UP if v > 0 else DOWN)


def cell_above(v: bool | None) -> list[Run]:
    if v is None:
        return num(DASH, FLAT)
    return txt("위", SIZE_BODY, UP, "b") if v else txt("아래", SIZE_BODY, DOWN, "b")


def cell_etf(r: SectorRow) -> list[Run]:
    """티커는 굵고 진하게, 한글명은 옆에 한 단계 작은 회색."""
    return num(r.ticker, INK, SIZE_NUM, "b") + txt("  " + r.name, SIZE_BODY, SUB, "m")


def cell_rank(r: SectorRow) -> list[Run]:
    return num(DASH if r.rank is None else str(r.rank), SUB, SIZE_BODY, "m")


def date_label(d) -> str:
    return f"{d.year}.{d.month}.{d.day} ({'월화수목금토일'[d.weekday()]})"


def draw_overnight(cv: Canvas, rows: list[Row]) -> None:
    """2열. 이름은 굵게, 현재값은 보조 숫자(작은 회색), 등락률은 굵고 진하게."""
    cv.heading("Overnight")
    mid_gap = 56
    col_w = (cv.w - 2 * PAD - mid_gap) / 2
    pct_w = max(runs_width(cell_pct(r.change)) for r in rows) + 22
    h = ROW_H + 6
    for n in range(0, len(rows), 2):
        if (n // 2) % 2 == 1:
            cv.stripe(h)
        for k, r in enumerate(rows[n:n + 2]):
            x0 = PAD + k * (col_w + mid_gap)
            ym = cv.y + h / 2
            cv.draw_runs(x0, ym, txt(r.name, SIZE_BODY + 2, INK, "b"))
            cv.draw_runs(x0 + col_w, ym, cell_pct(r.change), "right")
            cv.draw_runs(x0 + col_w - pct_w, ym, cell_value(r.value, r.decimals, secondary=True), "right")
        cv.y += h


def draw_liquidity(cv: Canvas, rows: list[Row], warnings: list[str]) -> None:
    cv.heading("Liquidity & credit")
    body = []
    for r in rows:
        suffix = "%" if r.kind == "rate" else ""
        change = cell_rate_change(r.change) if r.kind == "rate" else cell_pct(r.change)
        body.append([txt(r.name, SIZE_BODY, INK, "m"), cell_value(r.value, 2, suffix), change,
                     cell_trend(r.trend)])
    cv.table(["", "현재", "전일 대비", "1개월"], ["left", "right", "right", "right"], body, spare_after=0)
    for w in warnings:
        cv.y += 8
        cv.line(txt("⚠ " + w, SIZE_BODY, WARN, "b"))


def sector_short_rows(sectors: list[SectorRow]) -> tuple[list[SectorRow], int]:
    ranked = [s for s in sectors if s.rank is not None]
    if len(ranked) <= 8:
        return ranked or sectors[:8], len(ranked)
    return ranked[:5] + ranked[-3:], 5


def draw_sector_short(cv: Canvas, sectors: list[SectorRow]) -> None:
    cv.heading("Sector leadership")
    cv.line(txt("SPY 대비 1개월 상대강도 · 전주: 순위 변화", SIZE_BODY, SUB), 64)
    cv.y += 6
    rows, cut = sector_short_rows(sectors)
    body = [[cell_rank(r), cell_etf(r), cell_pct(r.d1), cell_pct(r.m1), cell_rank_change(r.rank_change)]
            for r in rows]
    cv.table(["순위", "ETF", "1일", "1개월", "전주"], ["right", "left", "right", "right", "right"], body,
             bands=[GROUPS[r.group][1] for r in rows], gap_after={cut} if 0 < cut < len(rows) else set())
    draw_legend(cv)


def render_dashboard(c: Collected, day: dt.date) -> list[Path]:
    """한 장에 들어가면 1장, 넘치면 "Overnight + Liquidity"와 "Sectors" 2장."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUT_DIR.glob("m0_dashboard*.png"):
        f.unlink()
    right = date_label(day)

    one = Canvas()
    one.title("Morning Dashboard", right)
    draw_overnight(one, c.overnight)
    one.rule()
    draw_liquidity(one, c.liquidity, c.warnings)
    one.rule()
    draw_sector_short(one, c.sectors)
    # ④ Leaders: LEADERS 가 비어 있으면 숨김
    if one.y + BOTTOM <= MAX_H:
        return [one.finish(OUT_DIR / "m0_dashboard.png")]

    a = Canvas()
    a.title("Morning Dashboard", right)
    draw_overnight(a, c.overnight)
    a.rule()
    draw_liquidity(a, c.liquidity, c.warnings)
    b = Canvas()
    b.title("Morning Dashboard", right)
    draw_sector_short(b, c.sectors)
    return [a.finish(OUT_DIR / "m0_dashboard_1.png"), b.finish(OUT_DIR / "m0_dashboard_2.png")]


# 섹터 표 종류: 평일·월요일 텔레그램 표(daily), 월요일에만 따로 보내는 주간판(weekly), 사이트용 전체 열(all)
DAILY_EXTRA = ("1주", "연초 대비", "200일선", "52주 고점")  # 주간판 열(daily 에는 없음)


def full_columns(kind: str):
    """kind: "daily"(순위·ETF·1일·1개월·50일선·전주), "weekly"(순위·ETF·1주·연초 대비·200일선·52주 고점),
    "all"(모두, 사이트용)."""
    cols = [("순위", "right", cell_rank), ("ETF", "left", cell_etf),
            ("1일", "right", lambda r: cell_pct(r.d1)),
            ("1주", "right", lambda r: cell_pct(r.w1)),
            ("1개월", "right", lambda r: cell_pct(r.m1)),
            ("연초 대비", "right", lambda r: cell_pct(r.ytd)),
            ("50일선", "right", lambda r: cell_above(r.above50)),
            ("200일선", "right", lambda r: cell_above(r.above200)),
            ("52주 고점", "right", lambda r: cell_pct(r.from_high)),
            ("전주", "right", lambda r: cell_rank_change(r.rank_change))]
    if kind == "all":
        return cols
    keep = {"순위", "ETF"} | (set(DAILY_EXTRA) if kind == "weekly" else {"1일", "1개월", "50일선", "전주"})
    return [c for c in cols if c[0] in keep]


def draw_legend(cv: Canvas) -> None:
    cv.y += 22
    x, ym = PAD, cv.y + 34
    for name, color in GROUPS.values():
        cv.d.rectangle([x, ym - 22, x + 11, ym + 22], fill=color)
        x += 28
        runs = txt(name, SIZE_BODY, SUB)
        cv.draw_runs(x, ym, runs)
        x += runs_width(runs) + 56
    cv.y += 68


FULL_KINDS = {  # kind: (파일 이름, 제목, 부제 앞부분)
    "daily": ("m0_sectors", "Sector leadership — full", "SPY 대비 1개월 상대강도 순위"),
    "weekly": ("m0_weekly", "Sector leadership — weekly", "주간판 · SPY 대비 1개월 상대강도 순위순"),
    "all": ("m0_site_sectors", "Sector leadership — full", "SPY 대비 1개월 상대강도 순위"),
}


def render_full(c: Collected, day: dt.date, kind: str = "daily") -> list[Path]:
    """22개 전체를 순위순으로. daily·weekly 는 1440px, 4:5 에 안 들어가면 1–11위 / 12–22위 2장.
    all(사이트용)은 열이 많아 더 넓고, 나누지 않고 한 장."""
    stem, title, sub = FULL_KINDS[kind]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in [OUT_DIR / f"{stem}.png", *OUT_DIR.glob(f"{stem}_*.png")]:
        f.unlink(missing_ok=True)
    cols = full_columns(kind)
    header, aligns = [h for h, _, _ in cols], [a for _, a, _ in cols]

    def rows_of(items):
        return [[fn(r) for _, _, fn in cols] for r in items]

    gap = 28 if kind == "weekly" else FULL_GAP  # 주간판은 "52주 고점"·"연초 대비" 머리글이 길어 간격을 줄인다
    width = max(WIDTH, Canvas.needed_width(header, rows_of(c.sectors), bands=True, col_gap=gap))
    if kind != "all" and width > WIDTH:
        print(f"[경고] 섹터 표({kind}) 폭 {width}px > {WIDTH}px", file=sys.stderr)
    right = date_label(day)
    note = "" if kind == "weekly" else " · 전주: 순위 변화"

    def draw(items, part: str, name: str) -> tuple[Canvas, Path]:
        cv = Canvas(width)
        cv.title(title, right)
        cv.line(txt(sub + (f" · {part}" if part else "") + note, SIZE_BODY, SUB), 64)
        cv.y += 6
        cv.table(header, aligns, rows_of(items), bands=[GROUPS[r.group][1] for r in items], col_gap=gap)
        draw_legend(cv)
        return cv, OUT_DIR / name

    cv, path = draw(c.sectors, "", f"{stem}.png")
    if kind == "all" or cv.y + BOTTOM <= MAX_H:
        return [cv.finish(path)]
    half = (len(c.sectors) + 1) // 2
    first, second = c.sectors[:half], c.sectors[half:]
    lo = lambda items: items[0].rank or "–"  # noqa: E731
    hi = lambda items: items[-1].rank or "–"  # noqa: E731
    out = []
    for i, items in enumerate((first, second), 1):
        cv, path = draw(items, f"{lo(items)}–{hi(items)}위", f"{stem}_{i}.png")
        out.append(cv.finish(path))
    return out


def as_messages(paths: list[Path], label: str) -> list[Message]:
    """한 장씩 낱장으로 보낸다(앨범은 격자로 줄어 작게 보인다). 캡션 없음."""
    if len(paths) == 1:
        return [Message("photo", "", paths[0], label)]
    return [Message("photo", "", p, f"{label} {i}") for i, p in enumerate(paths, 1)]


def prepare() -> Prepared:
    """텔레그램: 대시보드 → 섹터 전체표(평일과 같은 1440px) → 월요일만 주간판.
    사이트용(data["site"]): 대시보드 → 모든 열이 들어간 섹터 전체표(매일)."""
    c = collect()
    monday = is_monday_layout()
    day = c.sector_date.date() if c.sector_date is not None else now_kst().date()
    today = now_kst().date()
    msgs, site, errors = [], [], list(c.errors)

    def render(label, fn) -> list[Message]:
        try:
            return as_messages(fn(), label)
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc(file=sys.stderr)
            errors.append(f"{label} 그리기 실패: {type(e).__name__}: {e}"[:200])
            return [Message("text", f"{label} 확인 실패", label=f"{label} 실패 알림")]

    dash = render("대시보드", lambda: render_dashboard(c, today))
    msgs += dash + render("섹터 전체표", lambda: render_full(c, today, "daily"))
    if monday:
        msgs += render("주간판", lambda: render_full(c, today, "weekly"))
    site += dash + render("섹터 전체표", lambda: render_full(c, today, "all"))
    print(f"[대시보드] {'월요일(주간판 포함)' if monday else '평일'}, 섹터 기준일 {day}, "
          + ", ".join(m.label for m in msgs))
    return Prepared(msgs, errors, {"site": site})


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--monday" in args:
        os.environ["DASHBOARD_LAYOUT"] = "monday"
    elif "--weekday" in args:
        os.environ["DASHBOARD_LAYOUT"] = "weekday"
    sys.exit(run_standalone(sys.modules[__name__]))
