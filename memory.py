"""메시지 6: 메모리 — DRAM ETF·SK하이닉스 2배(7709) 가격과 좌수, DRAM 현물가(정품 3개 품목). 텍스트.

단독 실행: python memory.py [--dry-run]   (--dry-run 이 아니면 data/ 를 커밋·푸시)

| 항목 | 1순위 | 대체 |
| DRAM ETF(Roundhill) | roundhillinvestments.com 일별 CSV: 종가(Market Price)·NAV·좌수·순자산 | Yahoo DRAM(가격만) |
| 7709 가격 | Yahoo 7709.HK 종가(HKD) | CSOP 웹 API closePrice |
| 7709 NAV·좌수·순자산 | CSOP 웹 API(저장만, 좌수는 메시지에 표시) | 없음 |
| DRAM 현물가 | DRAMeXchange 첫 화면 세션 평균·변화율 | TrendForce 현물가 페이지 |

좌수·순자산·NAV·현물가는 출처가 과거 값을 주지 않으므로 data/ 에 매일 한 줄씩 쌓는다.
좌수 변화는 저장된 직전 값과 비교한다(첫 기록이면 변화 없이 값만).
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import io
import re
import sys

import requests

from briefing_common import UA, Message, Prepared, date_suffix, pct_change, run_standalone

NAME = "메모리"
FAIL_TEXT = "메모리 항목 확인 실패"
TIMEOUT = (10, 25)

ROUNDHILL_CSV = "https://www.roundhillinvestments.com/assets/data/FilepointRoundhill.40RU.RU_DailyNAV.csv"
ROUNDHILL_HIST = "https://www.roundhillinvestments.com/assets/data/FilepointRoundhill.40RU.RU_DRAM_Daily.csv"
CSOP_API = "https://website-api.csopasset.com/cmsApi/NAV/product"
CSOP_NAME = "CSOP SK Hynix Daily Max (2x) Leveraged Product"
CSOP_START = dt.date(2025, 10, 16)  # 7709 상장일
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
    extra = []
    if prev is not None:
        diff = n - prev
        extra.append("변화 없음" if round(diff / 1e4) == 0 else ("+" if diff > 0 else "") + shares_text(diff))
    when = date_suffix(d)
    if when:
        extra.append(when)
    return f"{label} {shares_text(n)}주" + (f" ({', '.join(extra)})" if extra else "")


def price_line(label: str, unit: str, v: float, prev: float | None, d: dt.date) -> str:
    extra = [pct_change(v, prev)] if prev else []
    when = date_suffix(d)
    if when:
        extra.append(when)
    return f"{label} {unit}{v:,.2f}" + (f" ({', '.join(extra)})" if extra else "")


# ---------------------------------------------------------------------------
# DRAM ETF (Roundhill)
# ---------------------------------------------------------------------------


def _mdy(s: str) -> dt.date:
    return dt.datetime.strptime(s.strip(), "%m/%d/%Y").date()


def roundhill() -> tuple[list[str], list[str]]:
    lines, sources = [], []
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

    lines.append(price_line("DRAM ETF", "$", price, prev_price, d))
    lines.append("· " + shares_line("좌수", shares, prev_shares, d))
    sources.append("Roundhill")
    return lines, sources


def roundhill_fallback(reason: str) -> tuple[list[str], list[str]]:
    st = _store()
    s = st.with_retry(st.fetch_yahoo, "DRAM", dt.date.today() - dt.timedelta(days=30))
    print(f"[경고] DRAM ETF: Roundhill {reason} → Yahoo DRAM 사용(좌수 없음)", file=sys.stderr)
    d = s.index[-1].date()
    return [price_line("DRAM ETF", "$", float(s.iloc[-1]), float(s.iloc[-2]) if len(s) > 1 else None, d),
            "· 좌수 확인 실패"], ["Yahoo DRAM"]


# ---------------------------------------------------------------------------
# SK하이닉스 2배 (CSOP 7709.HK)
# ---------------------------------------------------------------------------


def csop_api() -> dict:
    r = _get(CSOP_API, method="POST", json={"productName": CSOP_NAME},
             headers={"Origin": "https://www.csopasset.com", "Referer": "https://www.csopasset.com/"})
    hkd = next((x for x in r.json() if x.get("Currency") == "HKD"), None)
    if not hkd:
        raise RuntimeError("CSOP API에 HKD 자료 없음")
    return hkd


def csop() -> tuple[list[str], list[str], list[str]]:
    """(메시지 줄, 출처, 경고). 가격은 종가(HKD), NAV는 저장만."""
    lines, sources, errors = [], [], []
    info, info_err = None, ""
    try:
        info = csop_api()
    except Exception as e:  # noqa: BLE001
        info_err = str(e)

    # 가격: Yahoo 종가(가장 빠름) → CSOP closePrice
    st = _store()
    price = None
    try:
        s = st.store_series("yahoo_7709_HK", st.with_retry(st.fetch_yahoo, "7709.HK", CSOP_START))
        price = (float(s.iloc[-1]), float(s.iloc[-2]) if len(s) > 1 else None, s.index[-1].date())
        sources.append("Yahoo 7709.HK")
    except Exception as e:  # noqa: BLE001
        yerr = str(e)
        if info and info.get("closePrice"):
            close = float(info["closePrice"])
            diff = float(info.get("closePriceDifference") or 0)
            price = (close, close - diff if diff else None, dt.date.fromisoformat(info["HstDateFormat"]))
            sources.append("CSOP closePrice")
            print(f"[경고] 7709 가격: Yahoo {yerr} → CSOP closePrice 사용", file=sys.stderr)
        else:
            errors.append(f"7709 가격: Yahoo {yerr}; CSOP {info_err or 'closePrice 없음'}")
    lines.append(price_line("SK하이닉스 2배(7709)", "HK$", *price) if price else "SK하이닉스 2배(7709) 가격 확인 실패")

    if info:
        d = dt.date.fromisoformat(info["HstDateFormat"])
        if info.get("closePrice"):
            st.append_rows("csop_7709_close", _series([(d, float(info["closePrice"]))]))
        st.append_rows("csop_7709_nav", _series([(d, float(info["NAV"]))]), float_format="%.15g")
        if info.get("AUM"):
            _record("csop_7709_aum", d, float(info["AUM"]))
        prev_units = _record("csop_7709_units", d, float(info["Shares"]))
        lines.append("· " + shares_line("좌수", float(info["Shares"]), prev_units, d))
        sources.append("CSOP API")
    else:
        lines.append("· 좌수 확인 실패")
        errors.append(f"7709 NAV·좌수: CSOP {info_err}")
    return lines, sources, errors


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


def spot() -> tuple[list[str], list[str]]:
    errors = []
    for source, url in SPOT_URLS:
        try:
            d, rows = parse_spot(_get(url).text)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source} {e}")
            continue
        if errors:
            print(f"[경고] DRAM 현물가: {'; '.join(errors)} → {source} 사용", file=sys.stderr)
        when = date_suffix(d)
        lines = ["DRAM 현물가(세션 평균)" + (f" {when}" if when else "")]
        for item, label, key in SPOT_ITEMS:
            avg, chg = rows[item]
            _store().append_rows(key, _series([(d, avg)]))
            lines.append(f"{label} ${avg:,.3f} ({chg:+.2f}%)")
        return lines, [source]
    raise RuntimeError("; ".join(errors))


# ---------------------------------------------------------------------------


def prepare() -> Prepared:
    sections, sources, errors = [], [], []

    try:
        lines, src = roundhill()
    except Exception as e:  # noqa: BLE001
        reason = str(e)
        try:
            lines, src = roundhill_fallback(reason)
        except Exception as e2:  # noqa: BLE001
            lines, src = ["DRAM ETF 확인 실패"], []
            errors.append(f"DRAM ETF: Roundhill {reason}; Yahoo {e2}")
    sections.append(lines)
    sources += src

    try:
        lines, src, errs = csop()
    except Exception as e:  # noqa: BLE001
        lines, src, errs = ["SK하이닉스 2배(7709) 확인 실패"], [], [f"7709: {type(e).__name__}: {e}"]
    sections.append(lines)
    sources += src
    errors += errs

    try:
        lines, src = spot()
    except Exception as e:  # noqa: BLE001
        lines, src = ["DRAM 현물가 확인 실패"], []
        errors.append(f"DRAM 현물가: {e}")
    sections.append(lines)
    sources += src

    if sources:
        print(f"[출처] {NAME}: {', '.join(dict.fromkeys(sources))}")
    if len(errors) >= 3 and not sources:
        return Prepared([Message("text", FAIL_TEXT, label="실패 알림")], errors)
    text = "\n\n".join("\n".join(sec) for sec in sections)
    return Prepared([Message("text", text, label="텍스트")], errors)


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv[1:]
    if not dry:
        import data_merge

        data_merge.merge_remote_data()
    rc = run_standalone(sys.modules[__name__])
    if not dry:
        _store().commit_data()
    sys.exit(rc)
