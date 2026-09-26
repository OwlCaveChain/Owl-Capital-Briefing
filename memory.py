"""메시지 6: 메모리 — DRAM ETF·SK하이닉스 2배(7709)·삼성전자 2배(7747) 가격과 좌수, DRAM 현물가(정품 3개 품목).

단독 실행: python memory.py [--dry-run]   (--dry-run 이 아니면 data/ 를 커밋·푸시)

| 항목 | 1순위 | 대체 |
| DRAM ETF(Roundhill) | roundhillinvestments.com 일별 CSV: 종가(Market Price)·NAV·좌수·순자산 | Yahoo DRAM(가격만) |
| 7709·7747 가격 | Yahoo 7709.HK·7747.HK 종가(HKD) | CSOP 웹 API closePrice |
| 7709·7747 NAV·좌수·순자산 | CSOP 웹 API(NAV·순자산은 저장만, 좌수는 메시지에 표시) | 없음 |
| DRAM 현물가 | DRAMeXchange 첫 화면 세션 평균·변화율 | TrendForce 현물가 페이지 |

좌수·순자산·NAV·현물가는 출처가 과거 값을 주지 않으므로 data/ 에 매일 한 줄씩 쌓는다.
좌수 변화는 저장된 직전 값과 비교한다(첫 기록이면 변화 없이 값만).
ETF 발행 단위는 "좌"로 쓴다(예: 좌수 4억 3,433만좌).

좌수(현물가는 3개 품목 모두)가 CHART_MIN_DAYS 거래일 이상 쌓이면 그 항목은 텍스트 대신 차트로 보낸다.
좌수·가격 차트는 좌축 좌수(빨간 선)·우축 가격(회색 선, 로그 눈금), 현물가는 3개 품목을 로그 눈금 한 차트에.
제목은 그림 안에, 캡션에는 숫자만. 차트를 못 그리면 그 항목은 텍스트로 보낸다.
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests

from briefing_common import (OUT_DIR, UA, Message, Prepared, add_title, date_suffix, log_price_axis, pct_change,
                             run_standalone)

NAME = "메모리"
FAIL_TEXT = "메모리 항목 확인 실패"
TIMEOUT = (10, 25)

ROUNDHILL_CSV = "https://www.roundhillinvestments.com/assets/data/FilepointRoundhill.40RU.RU_DailyNAV.csv"
ROUNDHILL_HIST = "https://www.roundhillinvestments.com/assets/data/FilepointRoundhill.40RU.RU_DRAM_Daily.csv"
CSOP_API = "https://website-api.csopasset.com/cmsApi/NAV/product"
CHART_MIN_DAYS = 20  # 좌수가 이만큼(거래일) 쌓이면 차트로 보낸다


@dataclass(frozen=True)
class CsopProduct:
    code: str  # 홍콩 종목코드(저장 키에도 쓴다)
    label: str  # 메시지 표시명
    name: str  # CSOP API productName
    start: dt.date  # 상장일(Yahoo 가격 조회 시작)


CSOP_PRODUCTS = [
    CsopProduct("7709", "SK하이닉스 2배(7709)", "CSOP SK Hynix Daily Max (2x) Leveraged Product", dt.date(2025, 10, 16)),
    CsopProduct("7747", "삼성전자 2배(7747)", "CSOP Samsung Electronics Daily Max (2x) Leveraged Product",
                dt.date(2025, 5, 28)),
]
SPOT_URLS = [("DRAMeXchange", "https://www.dramexchange.com/"),
             ("TrendForce", "https://www.trendforce.com/price/dram/dram_spot")]
# 정품(eTT 제외) 3개 품목: (사이트 품목명, 메시지 표시명, 저장 키)
SPOT_ITEMS = [
    ("DDR5 16Gb (2Gx8) 4800/5600", "DDR5 16Gb", "dramx_DDR5_16Gb_4800_5600"),
    ("DDR4 16Gb (2Gx8) 3200", "DDR4 16Gb", "dramx_DDR4_16Gb_3200"),
    ("DDR4 8Gb (1Gx8) 3200", "DDR4 8Gb", "dramx_DDR4_8Gb_3200"),
]


def _store():
    """charts.py 의 data/ CSV 도구(무거운 import 라 필요할 때만)."""
    import charts

    return charts


def _get(url: str, method: str = "GET", headers: dict | None = None, **kw) -> requests.Response:
    """요청 2회(재시도 1회). 실패하면 짧은 사유의 RuntimeError."""
    last = None
    for _ in range(2):
        try:
            r = requests.request(method, url, headers={**UA, **(headers or {})}, timeout=TIMEOUT, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
    raise RuntimeError(_why(last))


def _why(e: Exception | None) -> str:
    if isinstance(e, requests.exceptions.ProxyError):
        return "네트워크 정책 차단(프록시 403)"
    if isinstance(e, requests.Timeout):
        return "응답 시간 초과"
    return f"{type(e).__name__}: {e}"[:160]


def _ts(d: dt.date):
    import pandas as pd

    return pd.Timestamp(d)


def _series(pairs):
    import pandas as pd

    pairs = sorted(pairs)
    return pd.Series([v for _, v in pairs], index=pd.DatetimeIndex([_ts(d) for d, _ in pairs]), dtype=float)


def _record(key: str, d: dt.date, value: float) -> float | None:
    """하루 값을 저장하고, 그 날짜보다 앞선 직전 저장값을 돌려준다."""
    st = _store()
    prev = st.load_series(key)
    if not prev.empty:
        prev = prev[prev.index < _ts(d)]
    st.append_rows(key, _series([(d, value)]), float_format="%.15g")
    return float(prev.iloc[-1]) if not prev.empty else None


@dataclass
class Section:
    """메시지 한 덩어리. chart 가 있으면 사진(캡션 caption), 없으면 lines 를 텍스트로."""

    lines: list[str]
    chart: Path | None = None
    caption: list[str] = field(default_factory=list)


def shares_text(n: float) -> str:
    """434,330,000 → '4억 3,433만', 8,420,000 → '842만'."""
    man = round(n / 1e4)
    eok, rest = divmod(abs(man), 10000)
    sign = "-" if man < 0 else ""
    if eok and rest:
        return f"{sign}{eok}억 {rest:,}만"
    if eok:
        return f"{sign}{eok}억"
    return f"{sign}{rest:,}만"


def shares_line(label: str, n: float, prev: float | None, d: dt.date) -> str:
    """'좌수 4억 3,433만좌 (+1,250만좌)'."""
    extra = []
    if prev is not None:
        diff = n - prev
        extra.append("변화 없음" if round(diff / 1e4) == 0 else ("+" if diff > 0 else "") + shares_text(diff) + "좌")
    when = date_suffix(d)
    if when:
        extra.append(when)
    return f"{label} {shares_text(n)}좌" + (f" ({', '.join(extra)})" if extra else "")


def price_line(label: str, unit: str, v: float, prev: float | None, d: dt.date) -> str:
    extra = [pct_change(v, prev)] if prev else []
    when = date_suffix(d)
    if when:
        extra.append(when)
    return f"{label} {unit}{v:,.2f}" + (f" ({', '.join(extra)})" if extra else "")


# ---------------------------------------------------------------------------
# 차트 (참고 채널 형식: 제목은 그림 안, 캡션은 숫자만)
# ---------------------------------------------------------------------------

UNITS_COLOR = "#D62728"  # 좌수: 빨간 선
PRICE_COLOR = "#8C8C8C"  # 가격: 회색 선
SPOT_COLORS = ["#D62728", "#111111", "#FF8C00"]
# 범례 뒤를 흰색으로 덮어 선과 겹쳐도 읽히게(테두리 없음)
LEGEND = dict(loc="upper left", fontsize=12, handlelength=2.2, frameon=True, facecolor="white", edgecolor="none",
              framealpha=1.0)


def _figure():
    """pyplot 을 거치지 않는 Figure(briefing.py 가 항목을 병렬로 준비하므로)."""
    from matplotlib.figure import Figure

    st = _store()
    fig = Figure(figsize=(12, 7), dpi=100)
    ax = fig.add_subplot()
    fig.subplots_adjust(left=0.08, right=0.92, top=0.86, bottom=0.08)
    fig.patch.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#888888")
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors="#333333", labelsize=11, length=4, width=0.8)
    return fig, ax, st.LINE_WIDTH


def _date_axis(ax, start, end) -> None:
    import matplotlib.dates as mdates
    import pandas as pd

    days = (end - start).days
    loc = mdates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d" if days <= 400 else "%y/%m"))
    ax.set_xlim(start, end + pd.Timedelta(days=max(1, days // 40)))


def _save(fig, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.png"
    fig.savefig(path, facecolor="white", dpi=100)
    return path


def units_chart(name: str, title: str, units, price, price_unit: str) -> Path:
    """좌축 좌수(빨간 선), 우축 가격(회색 선, 로그 눈금). 기간은 좌수 첫 기록부터."""
    import math

    from matplotlib.ticker import FuncFormatter, MaxNLocator

    fig, ax, lw = _figure()
    price = price[price.index >= units.index[0]].dropna()
    div, unit = (1e8, "억좌") if units.max() >= 1e8 else (1e4, "만좌")
    h1, = ax.plot(units.index, units.values, color=UNITS_COLOR, lw=lw, label="좌수(좌)")
    loc = MaxNLocator(nbins=6, steps=[1, 2, 5, 10])
    ax.yaxis.set_major_locator(loc)
    ticks = loc.tick_values(*ax.get_ylim())
    step = (ticks[1] - ticks[0]) / div if len(ticks) > 1 else 1
    dec = max(0, -math.floor(math.log10(step) + 1e-9)) if step > 0 else 0
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / div:,.{dec}f}"))
    ax.text(0.0, 1.02, f"({unit})", transform=ax.transAxes, ha="left", va="bottom", fontsize=10, color="#555555")
    handles = [h1]
    end = units.index[-1]
    if not price.empty:
        ax2 = ax.twinx()
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_color("#888888")
        ax2.spines["right"].set_linewidth(0.8)
        ax2.tick_params(colors="#333333", labelsize=11, length=4, width=0.8)
        h2, = ax2.plot(price.index, price.values, color=PRICE_COLOR, lw=lw, label="가격(우)")
        log_price_axis(ax2, price.values)
        ax2.text(1.0, 1.02, f"({price_unit})", transform=ax2.transAxes, ha="right", va="bottom", fontsize=10,
                 color="#555555")
        ax.set_zorder(ax2.get_zorder() + 1)  # 좌수 선을 가격 선 위에
        ax.patch.set_visible(False)
        handles.append(h2)
        end = max(end, price.index[-1])
    _date_axis(ax, units.index[0], end)
    ax.legend(handles=handles, labels=[h.get_label() for h in handles], **LEGEND)
    add_title(fig, title)
    return _save(fig, name)


def spot_chart(series: dict) -> Path:
    """DRAM 현물가 3개 품목, 로그 눈금 한 차트."""
    import pandas as pd

    fig, ax, lw = _figure()
    for (label, s), color in zip(series.items(), SPOT_COLORS):
        ax.plot(s.index, s.values, color=color, lw=lw, label=label)
    allv = pd.concat(list(series.values()))
    log_price_axis(ax, allv.values)
    ax.text(0.0, 1.02, "(달러)", transform=ax.transAxes, ha="left", va="bottom", fontsize=10, color="#555555")
    _date_axis(ax, allv.index.min(), allv.index.max())
    ax.legend(**LEGEND)
    add_title(fig, "DRAM 현물가(세션 평균)")
    return _save(fig, "6_dram_spot")


def with_chart(sec: Section, draw) -> Section:
    """차트를 그려 붙인다. 실패하면 경고만 남기고 텍스트 그대로."""
    try:
        sec.chart = draw()
    except Exception as e:  # noqa: BLE001
        print(f"[경고] {sec.lines[0].split(' ')[0]} 차트 실패, 텍스트로 보냄: {type(e).__name__}: {e}", file=sys.stderr)
    return sec


# ---------------------------------------------------------------------------
# DRAM ETF (Roundhill)
# ---------------------------------------------------------------------------


def _mdy(s: str) -> dt.date:
    return dt.datetime.strptime(s.strip(), "%m/%d/%Y").date()


def roundhill() -> tuple[Section, list[str]]:
    r = _get(ROUNDHILL_CSV)
    row = next((x for x in csv.DictReader(io.StringIO(r.text)) if x.get("Fund Ticker", "").strip() == "DRAM"), None)
    if row is None:
        raise RuntimeError("Roundhill CSV에 DRAM 없음")
    d = _mdy(row["Rate Date"])
    price, nav = float(row["Market Price"]), float(row["NAV"])
    shares, aum = float(row["Shares Outstanding"]), float(row["Net Assets"])
    prev_price = price - float(row["Market Price Change Dollars"])

    # 가격·NAV 과거분(상장 이후 전체)도 함께 쌓는다. 실패해도 오늘 값은 쓴다.
    st = _store()
    try:
        hist = list(csv.DictReader(io.StringIO(_get(ROUNDHILL_HIST).text)))
        st.append_rows("roundhill_DRAM_price", _series([(_mdy(h["Rate Date"]), float(h["Market Price"]))
                                                        for h in hist]))
        st.append_rows("roundhill_DRAM_nav", _series([(_mdy(h["Rate Date"]), float(h["NAV"])) for h in hist]))
    except Exception as e:  # noqa: BLE001
        print(f"[경고] Roundhill DRAM 과거 가격 실패: {_why(e)}", file=sys.stderr)
    st.append_rows("roundhill_DRAM_price", _series([(d, price)]))
    st.append_rows("roundhill_DRAM_nav", _series([(d, nav)]))
    _record("roundhill_DRAM_aum", d, aum)
    prev_shares = _record("roundhill_DRAM_shares", d, shares)

    sec = Section([price_line("DRAM ETF", "$", price, prev_price, d),
                   "· " + shares_line("좌수", shares, prev_shares, d)],
                  caption=[price_line("가격", "$", price, prev_price, d), shares_line("좌수", shares, prev_shares, d)])
    units = st.load_series("roundhill_DRAM_shares")
    if len(units) >= CHART_MIN_DAYS:
        with_chart(sec, lambda: units_chart("6_dram_etf", "DRAM ETF 좌수 · 가격", units,
                                            st.load_series("roundhill_DRAM_price"), "달러"))
    return sec, ["Roundhill"]


def roundhill_fallback(reason: str) -> tuple[Section, list[str]]:
    st = _store()
    s = st.with_retry(st.fetch_yahoo, "DRAM", dt.date.today() - dt.timedelta(days=30))
    print(f"[경고] DRAM ETF: Roundhill {reason} → Yahoo DRAM 사용(좌수 없음)", file=sys.stderr)
    d = s.index[-1].date()
    return Section([price_line("DRAM ETF", "$", float(s.iloc[-1]), float(s.iloc[-2]) if len(s) > 1 else None, d),
                    "· 좌수 확인 실패"]), ["Yahoo DRAM"]


# ---------------------------------------------------------------------------
# SK하이닉스 2배 (CSOP 7709.HK)
# ---------------------------------------------------------------------------


def csop_api(product: CsopProduct) -> dict:
    r = _get(CSOP_API, method="POST", json={"productName": product.name},
             headers={"Origin": "https://www.csopasset.com", "Referer": "https://www.csopasset.com/"})
    hkd = next((x for x in r.json() if x.get("Currency") == "HKD"), None)
    if not hkd:
        raise RuntimeError("CSOP API에 HKD 자료 없음")
    return hkd


def csop(product: CsopProduct) -> tuple[Section, list[str], list[str]]:
    """(섹션, 출처, 경고). 가격은 Yahoo 종가(HKD), NAV·순자산은 저장만."""
    code, label = product.code, product.label
    sources, errors = [], []
    info, info_err = None, ""
    try:
        info = csop_api(product)
    except Exception as e:  # noqa: BLE001
        info_err = str(e)

    # 가격: Yahoo 종가(가장 빠름) → CSOP closePrice
    st = _store()
    price = None
    try:
        s = st.store_series(f"yahoo_{code}_HK", st.with_retry(st.fetch_yahoo, f"{code}.HK", product.start))
        price = (float(s.iloc[-1]), float(s.iloc[-2]) if len(s) > 1 else None, s.index[-1].date())
        sources.append(f"Yahoo {code}.HK")
    except Exception as e:  # noqa: BLE001
        yerr = str(e)
        if info and info.get("closePrice"):
            close = float(info["closePrice"])
            diff = float(info.get("closePriceDifference") or 0)
            price = (close, close - diff if diff else None, dt.date.fromisoformat(info["HstDateFormat"]))
            sources.append("CSOP closePrice")
            print(f"[경고] {code} 가격: Yahoo {yerr} → CSOP closePrice 사용", file=sys.stderr)
        else:
            errors.append(f"{code} 가격: Yahoo {yerr}; CSOP {info_err or 'closePrice 없음'}")
    lines = [price_line(label, "HK$", *price) if price else f"{label} 가격 확인 실패"]
    caption = [price_line("가격", "HK$", *price) if price else "가격 확인 실패"]

    units = None
    if info:
        d = dt.date.fromisoformat(info["HstDateFormat"])
        if info.get("closePrice"):
            st.append_rows(f"csop_{code}_close", _series([(d, float(info["closePrice"]))]))
        st.append_rows(f"csop_{code}_nav", _series([(d, float(info["NAV"]))]), float_format="%.15g")
        if info.get("AUM"):
            _record(f"csop_{code}_aum", d, float(info["AUM"]))
        prev_units = _record(f"csop_{code}_units", d, float(info["Shares"]))
        line = shares_line("좌수", float(info["Shares"]), prev_units, d)
        lines.append("· " + line)
        caption.append(line)
        sources.append("CSOP API")
        units = st.load_series(f"csop_{code}_units")
    else:
        lines.append("· 좌수 확인 실패")
        errors.append(f"{code} NAV·좌수: CSOP {info_err}")

    sec = Section(lines, caption=caption)
    if units is not None and len(units) >= CHART_MIN_DAYS:
        with_chart(sec, lambda: units_chart(f"6_csop_{code}", f"{label} 좌수 · 가격", units,
                                            st.load_series(f"yahoo_{code}_HK"), "홍콩달러"))
    return sec, sources, errors


# ---------------------------------------------------------------------------
# DRAM 현물가
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], 1)}


def _plain(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _update_date(text: str) -> dt.date | None:
    """'Last Update: Sep.24 2026 18:10' 또는 'Last Update 2026-09-24 18:10'."""
    m = re.search(r"Last Update:?\s*([A-Za-z]{3})\.?\s*(\d{1,2}),?\s+(\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        return dt.date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    m = re.search(r"Last Update:?\s*(\d{4})-(\d{2})-(\d{2})", text)
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_spot(page: str) -> tuple[dt.date, dict[str, tuple[float, float]]]:
    """DRAM 현물가 표에서 {품목: (세션 평균, 세션 변화율 %)} 와 갱신일."""
    text = _plain(page)
    start = text.find(SPOT_ITEMS[0][0])
    if start < 0:
        raise ValueError("현물가 표를 찾지 못함")
    d = None
    for m in re.finditer(r"Last Update", text[:start]):
        d = _update_date(text[m.start():start]) or d
    if d is None:
        raise ValueError("현물가 갱신일을 찾지 못함")
    section = text[start:start + 2000]
    out = {}
    num = r"(-?[\d,.]+)"
    for item, _, _ in SPOT_ITEMS:
        # 품목 High Low SessionHigh SessionLow SessionAverage [▲▼] Change %
        m = re.search(re.escape(item) + r"\s+" + r"\s+".join([num] * 5) + r"\s*[▲▼]?\s*" + num + r"\s*%", section)
        if not m:
            raise ValueError(f"'{item}' 행을 찾지 못함")
        out[item] = (float(m.group(5).replace(",", "")), float(m.group(6)))
    return d, out


def spot() -> tuple[Section, list[str]]:
    errors = []
    for source, url in SPOT_URLS:
        try:
            d, rows = parse_spot(_get(url).text)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source} {e}")
            continue
        if errors:
            print(f"[경고] DRAM 현물가: {'; '.join(errors)} → {source} 사용", file=sys.stderr)
        st = _store()
        when = date_suffix(d)
        lines = ["DRAM 현물가(세션 평균)" + (f" {when}" if when else "")]
        caption, history = [], {}
        for item, label, key in SPOT_ITEMS:
            avg, chg = rows[item]
            st.append_rows(key, _series([(d, avg)]))
            lines.append(f"{label} ${avg:,.3f} ({chg:+.2f}%)")
            caption.append(f"{label} ${avg:,.3f} ({chg:+.2f}%" + (f", {when})" if when else ")"))
            history[label] = st.load_series(key)
        sec = Section(lines, caption=caption)
        if all(len(s) >= CHART_MIN_DAYS for s in history.values()):
            with_chart(sec, lambda: spot_chart(history))
        return sec, [source]
    raise RuntimeError("; ".join(errors))


# ---------------------------------------------------------------------------


def to_messages(sections: list[Section]) -> list[Message]:
    """차트가 있는 섹션은 사진으로, 나머지는 이어진 것끼리 텍스트 한 통으로(순서 유지)."""
    messages, pending = [], []

    def flush():
        if pending:
            messages.append(Message("text", "\n\n".join("\n".join(x.lines) for x in pending), label="텍스트"))
            pending.clear()

    for sec in sections:
        if sec.chart:
            flush()
            messages.append(Message("photo", "\n".join(sec.caption), path=sec.chart, label=sec.chart.stem))
        else:
            pending.append(sec)
    flush()
    return messages


def prepare() -> Prepared:
    sections, sources, errors = [], [], []

    try:
        sec, src = roundhill()
    except Exception as e:  # noqa: BLE001
        reason = str(e)
        try:
            sec, src = roundhill_fallback(reason)
        except Exception as e2:  # noqa: BLE001
            sec, src = Section(["DRAM ETF 확인 실패"]), []
            errors.append(f"DRAM ETF: Roundhill {reason}; Yahoo {e2}")
    sections.append(sec)
    sources += src

    for product in CSOP_PRODUCTS:
        try:
            sec, src, errs = csop(product)
        except Exception as e:  # noqa: BLE001
            sec, src, errs = (Section([f"{product.label} 확인 실패"]), [],
                              [f"{product.code}: {type(e).__name__}: {e}"])
        sections.append(sec)
        sources += src
        errors += errs

    try:
        sec, src = spot()
    except Exception as e:  # noqa: BLE001
        sec, src = Section(["DRAM 현물가 확인 실패"]), []
        errors.append(f"DRAM 현물가: {e}")
    sections.append(sec)
    sources += src

    if sources:
        print(f"[출처] {NAME}: {', '.join(dict.fromkeys(sources))}")
    if not sources:
        return Prepared([Message("text", FAIL_TEXT, label="실패 알림")], errors)
    return Prepared(to_messages(sections), errors)


if __name__ == "__main__":
    from briefing_common import setup_korean_font

    setup_korean_font()
    dry = "--dry-run" in sys.argv[1:]
    if not dry:
        import data_merge

        data_merge.merge_remote_data()
    rc = run_standalone(sys.modules[__name__])
    if not dry:
        _store().commit_data()
    sys.exit(rc)
