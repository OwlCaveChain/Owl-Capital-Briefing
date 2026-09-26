"""Owl Capital 차트 브리핑.

실행하면 6개 차트를 PNG로 그려 텔레그램 sendPhoto로 순서대로 보낸다.
받은 시계열은 data/ 폴더에 CSV로 쌓고(새 날짜만 추가), 변경분을 git에 커밋·푸시한다.

환경변수
  TELEGRAM_BOT_TOKEN   텔레그램 봇 토큰 (전송은 telegram_send.py)
  TELEGRAM_CHAT_ID     텔레그램 채팅 ID (없으면 telegram_send 기본값)
  CHARTS_DATA_BRANCH   data/ 커밋을 푸시할 브랜치 (기본: 원격 기본 브랜치)

옵션
  --dry-run    텔레그램 전송 없이 out/ 폴더에 PNG와 캡션만 저장
  --no-commit  data/ 변경분 git 커밋·푸시 생략
  --only N,N   지정한 번호의 차트만 실행 (예: --only 1,5)
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import io
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"


def _ensure_packages() -> None:
    try:
        import matplotlib, pandas, requests, yfinance  # noqa: F401
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")],
            check=False,
        )


_ensure_packages()

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

import telegram_send  # noqa: E402
from briefing_common import date_suffix  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()
YEAR_START = dt.date(TODAY.year, 1, 1)
HTTP_TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

# 선 색상: 1 빨강 실선, 2 검정, 3 주황, 4 노랑
COLORS = ["#D62728", "#111111", "#FF8C00", "#F2C200"]
LINE_WIDTH = 3.6  # 이미지 폭의 약 0.42%(1200px 기준 약 5px)


# ---------------------------------------------------------------------------
# 폰트
# ---------------------------------------------------------------------------

KOREAN_FONTS = ["NanumGothic", "NanumBarunGothic", "Noto Sans CJK KR", "Noto Sans KR", "UnDotum"]


def setup_korean_font() -> str | None:
    def find() -> str | None:
        names = {f.name for f in font_manager.fontManager.ttflist}
        return next((n for n in KOREAN_FONTS if n in names), None)

    name = find()
    if name is None:
        # 폰트가 없으면 설치 후 캐시 재구성
        cmd = "apt-get install -y -q fonts-nanum >/dev/null 2>&1 || (apt-get update -q >/dev/null 2>&1 && apt-get install -y -q fonts-nanum >/dev/null 2>&1)"
        subprocess.run(cmd, shell=True, check=False)
        for path in Path("/usr/share/fonts").rglob("Nanum*.ttf"):
            font_manager.fontManager.addfont(str(path))
        name = find()
    if name:
        plt.rcParams["font.family"] = name
    else:
        print("[경고] 한글 폰트를 찾지 못했습니다. 글자가 깨질 수 있습니다.", file=sys.stderr)
    plt.rcParams["axes.unicode_minus"] = False
    return name


# ---------------------------------------------------------------------------
# 데이터 수집 (출처별 재시도 1회)
# ---------------------------------------------------------------------------


class SourceError(Exception):
    pass


def with_retry(fn, *args, **kwargs):
    """외부 출처 호출을 최대 2회(재시도 1회) 시도한다."""
    last: Exception | None = None
    for attempt in range(2):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 0:
                time.sleep(2)
    raise SourceError(_short(last))


def _short(e: Exception | None) -> str:
    if e is None:
        return "알 수 없는 오류"
    if isinstance(e, requests.exceptions.ProxyError):
        msg = "네트워크 정책 차단(프록시 403)"
    elif isinstance(e, requests.Timeout):
        msg = "응답 시간 초과"
    elif isinstance(e, (SourceError, ConnectionError)):
        msg = str(e)
    else:
        msg = f"{type(e).__name__}: {e}"
    return msg if len(msg) <= 160 else msg[:157] + "..."


_DEAD_HOSTS: dict[str, str] = {}


def _get(url: str, **kwargs) -> requests.Response:
    host = requests.utils.urlparse(url).hostname or url
    if host in _DEAD_HOSTS:
        # 같은 실행에서 이미 두 번 연속 접속 실패한 호스트는 바로 건너뛴다
        raise ConnectionError(f"{host} 접속 불가")
    headers = kwargs.pop("headers", UA)
    try:
        r = requests.get(url, headers=headers, timeout=(10, HTTP_TIMEOUT), **kwargs)
    except (requests.ConnectionError, requests.Timeout) as e:
        _HOST_FAILS[host] = _HOST_FAILS.get(host, 0) + 1
        if _HOST_FAILS[host] >= 2:
            _DEAD_HOSTS[host] = type(e).__name__
        raise
    _HOST_FAILS.pop(host, None)
    r.raise_for_status()
    return r


_HOST_FAILS: dict[str, int] = {}


def fetch_fred(series_id: str) -> pd.Series:
    # FRED는 브라우저형 User-Agent 요청에 응답을 주지 않고 시간 초과되므로 requests 기본 UA로 요청한다
    r = _get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}, headers={})
    df = pd.read_csv(io.StringIO(r.text))
    date_col = df.columns[0]  # observation_date (구 형식: DATE)
    s = pd.to_numeric(df[series_id], errors="coerce")  # 결측치 "." 처리
    s.index = pd.to_datetime(df[date_col])
    s = s.dropna()
    if s.empty:
        raise ValueError(f"FRED {series_id} 빈 데이터")
    return s


def fetch_yahoo(ticker: str, start: dt.date) -> pd.Series:
    import yfinance as yf

    df = yf.download(ticker, start=start.isoformat(), progress=False, auto_adjust=False, threads=False)
    if df is None or df.empty:
        raise ValueError(f"Yahoo {ticker} 빈 데이터")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    s = close.dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s.astype(float)


def fetch_stooq(symbol: str, start: dt.date) -> pd.Series:
    r = _get("https://stooq.com/q/d/l/", params={"s": symbol, "i": "d", "d1": start.strftime("%Y%m%d")})
    df = pd.read_csv(io.StringIO(r.text))
    if "Close" not in df.columns or df.empty:
        raise ValueError(f"stooq {symbol} 응답 형식 오류")
    s = pd.to_numeric(df["Close"], errors="coerce")
    s.index = pd.to_datetime(df["Date"])
    return s.dropna()


MOF_BASE = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate"


def _parse_mof_csv(raw: bytes) -> pd.Series:
    lines = raw.decode("utf-8", errors="replace").splitlines()
    head = next(i for i, ln in enumerate(lines) if "10Y" in ln)
    df = pd.read_csv(io.StringIO("\n".join(lines[head:])), on_bad_lines="skip")
    s = pd.to_numeric(df["10Y"], errors="coerce")
    s.index = pd.to_datetime(df[df.columns[0]], format="%Y/%m/%d", errors="coerce")
    return s[s.index.notna()].dropna()


def fetch_mof_jgb10() -> pd.Series:
    """일본 재무성 JGB 10년물. 과거 파일(전월 말까지)에 당월 파일을 이어 붙인다."""
    hist = _parse_mof_csv(_get(f"{MOF_BASE}/historical/jgbcme_all.csv").content)
    try:
        cur = _parse_mof_csv(_get(f"{MOF_BASE}/jgbcme.csv").content)
    except Exception as e:  # noqa: BLE001  당월 파일이 없어도 과거분으로 진행
        print(f"[경고] MOF 당월 파일 실패: {_short(e)}", file=sys.stderr)
        cur = pd.Series(dtype=float)
    s = pd.concat([hist, cur])
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.empty:
        raise ValueError("MOF 10Y 빈 데이터")
    return s


ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
ECOS_KR10Y = ("817Y002", "010210000")  # 1.3.2.1. 시장금리(일별) / 국고채(10년), 연%


def fetch_ecos(stat_code: str, item_code: str, key: str, start: dt.date) -> pd.Series:
    """한국은행 ECOS 일별 통계. 저장분이 있으면 마지막 날짜 2주 전부터만 받는다(키 ECOS_API_KEY)."""
    api_key = os.environ.get("ECOS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ECOS_API_KEY 없음")
    stored = load_series(key)
    if not stored.empty:
        start = max(start, (stored.index.max() - pd.Timedelta(days=14)).date())
    url = (f"{ECOS_BASE}/{api_key}/json/kr/1/100000/{stat_code}/D/"
           f"{start:%Y%m%d}/{TODAY:%Y%m%d}/{item_code}")
    try:
        j = _get(url).json()
    except Exception as e:  # noqa: BLE001  오류 메시지의 URL에서 키를 가린다
        raise SourceError(_short(e).replace(api_key, "***")) from None
    if "StatisticSearch" not in j:
        res = j.get("RESULT", {})
        if res.get("CODE") == "INFO-200" and not stored.empty:
            return stored[stored.index >= pd.Timestamp(start)]  # 새 값 없음(휴장)
        raise ValueError(f"ECOS {res.get('CODE', '?')} {res.get('MESSAGE', '')}".strip())
    rows = j["StatisticSearch"]["row"]
    s = pd.Series(pd.to_numeric([r["DATA_VALUE"] for r in rows], errors="coerce"),
                  index=pd.to_datetime([r["TIME"] for r in rows], format="%Y%m%d")).dropna()
    if s.empty:
        raise ValueError(f"ECOS {stat_code}/{item_code} 빈 데이터")
    return s


PETRONET_COLS = {"Dubai": "Dubai", "Brent": "Brent", "WTI": "WTI"}


@functools.lru_cache(maxsize=4)
def _petronet_table(start: dt.date) -> pd.DataFrame:
    """한국석유공사 페트로넷 일일국제원유가격(엑셀 다운로드).

    열: Dubai(현물), Brent(ICE 선물 근월물), WTI(NYMEX 선물 근월물). 한 실행에서 한 번만 받는다.
    """
    session = requests.Session()
    session.headers.update(UA)
    session.get("https://www.petronet.co.kr/v4/main.jsp", timeout=(10, HTTP_TIMEOUT))  # 세션 쿠키
    end = TODAY
    params = {
        "term": "d", "by": start.year, "bq": (start.month - 1) // 3 + 1, "bm": f"{start.month:02d}", "bw": "01",
        "bd": f"{start.day:02d}", "ay": end.year, "aq": (end.month - 1) // 3 + 1, "am": f"{end.month:02d}",
        "aw": "01", "ad": f"{end.day:02d}", "ProdCDList": "001,002,003,004",
    }
    r = session.get("https://www.petronet.co.kr/v4/excel/KDFQ0100_x2.jsp", params=params,
                    timeout=(10, HTTP_TIMEOUT))
    r.raise_for_status()
    table = next(t for t in pd.read_html(io.StringIO(r.content.decode("utf-8", errors="replace")))
                 if t.shape[1] >= 3 and (t.iloc[0] == "Dubai").any())
    header = list(table.iloc[0])
    rows = table.iloc[1:]
    md_ = rows[0].astype(str).str.extract(r"(\d{1,2})월\s*(\d{1,2})일")
    ok = md_.notna().all(axis=1)
    # 조회 구간이 한 해 안이므로 연도는 시작 연도 기준(연말 넘김은 월이 줄어들면 다음 해)
    months = md_[ok][0].astype(int).tolist()
    years, y, prev = [], start.year, 0
    for m in months:
        if m < prev:
            y += 1
        years.append(y)
        prev = m
    idx = pd.DatetimeIndex(pd.to_datetime({"year": years, "month": months, "day": md_[ok][1].astype(int).tolist()}))
    out = pd.DataFrame(index=idx)
    for name, col in PETRONET_COLS.items():
        if col in header:
            out[name] = pd.to_numeric(rows[ok][header.index(col)], errors="coerce").values
    return out


def fetch_petronet(product: str, start: dt.date) -> pd.Series:
    """페트로넷 유종별 일일 가격. product: Dubai | Brent | WTI"""
    table = _petronet_table(start)
    if product not in table.columns:
        raise ValueError(f"페트로넷 {product} 열 없음")
    s = table[product].dropna()
    if s.empty:
        raise ValueError(f"페트로넷 {product} 빈 데이터")
    return s


@dataclass
class Fetched:
    series: pd.Series
    source: str  # 캡션용 출처 이름


def fetch_chain(name: str, candidates: list[tuple[str, str, callable]]) -> Fetched:
    """(저장키, 출처명, 호출함수) 목록을 차례로 시도. 성공한 결과를 data/에 누적 저장."""
    errors = []
    for key, source, call in candidates:
        try:
            s = with_retry(call)
            s = s[~s.index.duplicated(keep="last")].sort_index()
            stored = store_series(key, s)
            if errors:
                print(f"[경고] {name}: {'; '.join(errors)} → {source} 사용", file=sys.stderr)
            return Fetched(stored, source)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source} {_short(e)}")
    raise SourceError(f"{name}: " + "; ".join(errors))


# ---------------------------------------------------------------------------
# data/ CSV 누적 저장
# ---------------------------------------------------------------------------


def _csv_path(key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return DATA_DIR / f"{safe}.csv"


def load_series(key: str) -> pd.Series:
    path = _csv_path(key)
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(path, parse_dates=["date"])
    return pd.Series(df["value"].values, index=df["date"], dtype=float)


def append_rows(key: str, rows: pd.Series) -> int:
    """기존 CSV에 없는 날짜만 뒤에 추가한다. 추가한 행 수를 돌려준다.

    매일 한 줄씩 쌓는 지표(예: 좌수, DRAM 현물가)도
    append_rows("dram_spot", pd.Series([값], index=[pd.Timestamp(오늘)])) 로 쓰면 된다.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _csv_path(key)
    existing = load_series(key)
    rows = rows.dropna()
    new = rows[~rows.index.isin(existing.index)]
    if not existing.empty:
        new = new[new.index > existing.index.max()]
    if new.empty:
        return 0
    out = pd.DataFrame({"date": new.index.strftime("%Y-%m-%d"), "value": new.values})
    write_header = not path.exists()
    out.to_csv(path, mode="a", header=write_header, index=False, float_format="%.6g")
    return len(out)


def store_series(key: str, s: pd.Series) -> pd.Series:
    append_rows(key, s)
    stored = load_series(key)
    # 이번에 받은 값과 저장값을 합쳐 차트에 사용(과거분 수정치는 새로 받은 값을 우선)
    merged = pd.concat([stored, s])
    return merged[~merged.index.duplicated(keep="last")].sort_index()


# ---------------------------------------------------------------------------
# 차트 스타일
# ---------------------------------------------------------------------------


def new_figure():
    fig, ax = plt.subplots(figsize=(12, 7), dpi=100)
    fig.subplots_adjust(left=0.07, right=0.93, top=0.9, bottom=0.08)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.grid(False)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#888888")
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors="#333333", labelsize=11, length=4, width=0.8)
    return fig, ax


def unit_label(ax, text: str, right: bool = False) -> None:
    x, ha = (1.0, "right") if right else (0.0, "left")
    ax.text(x, 1.02, text, transform=ax.transAxes, ha=ha, va="bottom", fontsize=10, color="#555555")


def date_axis(ax, start: pd.Timestamp, end: pd.Timestamp) -> None:
    months = (end.year - start.year) * 12 + end.month - start.month
    interval = 3 if months <= 30 else 6
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=range(1, 13, interval), bymonthday=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
    ax.set_xlim(start, end + pd.Timedelta(days=max(3, months)))


def legend(ax, handles=None) -> None:
    kw = dict(loc="upper left", frameon=False, fontsize=12, handlelength=2.2)
    if handles:
        ax.legend(handles=handles, labels=[h.get_label() for h in handles], **kw)
    else:
        ax.legend(**kw)


def save(fig, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.png"
    fig.savefig(path, facecolor="white", dpi=100)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 캡션 도우미
# ---------------------------------------------------------------------------


def md(ts: pd.Timestamp) -> str:
    return f"{ts.month}/{ts.day}"


def period_word(s: pd.Series) -> str:
    gap = s.index.to_series().diff().dt.days.tail(10).median()
    if gap >= 25:
        return "전월"
    if gap >= 5:
        return "전주"
    return "전일"


def latest(s: pd.Series, fmt: str, unit: str = "", prefix: str = "", chg_unit: str | None = None) -> str:
    """'$92.41 (-0.56)' 형태. 최근 1영업일보다 오래된 값만 '(+0.16, 9/21)'처럼 날짜를 붙인다."""
    s = s.dropna()
    v, d = s.iloc[-1], s.index[-1]
    cu = unit if chg_unit is None else chg_unit
    extra = [f"{v - s.iloc[-2]:+{fmt}}{cu}"] if len(s) > 1 else []
    when = date_suffix(d, monthly=period_word(s) == "전월")
    if when:
        extra.append(when)
    return f"{prefix}{v:{fmt}}{unit}" + (f" ({', '.join(extra)})" if extra else "")


def caption(title: str, lines: list[str], sources: list[str]) -> str:
    """첫 줄 제목, 시리즈마다 한 줄. 출처는 캡션에 넣지 않고 실행 로그에만 남긴다(대체 출처 확인용)."""
    if sources:
        print(f"[출처] {title}: {', '.join(dict.fromkeys(sources))}")
    return "\n".join([title, *lines])


def plot_x(s: pd.Series) -> pd.DatetimeIndex:
    """월평균 시계열은 해당 월 가운데(15일)에 찍는다."""
    return s.index + pd.Timedelta(days=14) if period_word(s) == "전월" else s.index


def since(s: pd.Series, start: dt.date) -> pd.Series:
    return s[s.index >= pd.Timestamp(start)]


# ---------------------------------------------------------------------------
# 차트 정의
# ---------------------------------------------------------------------------


@dataclass
class ChartResult:
    path: Path
    caption: str


def chart_spread() -> ChartResult:
    start = dt.date(2021, 1, 1)

    def yahoo_spread() -> pd.Series:
        ten = fetch_yahoo("^TNX", start)
        two = fetch_yahoo("2YY=F", start)
        return (ten - two).dropna()

    f = fetch_chain(
        "10Y-2Y 스프레드",
        [
            ("fred_T10Y2Y", "FRED", lambda: fetch_fred("T10Y2Y")),
            ("yahoo_TNX_minus_2YY", "Yahoo(근사)", yahoo_spread),
        ],
    )
    s = since(f.series, start)
    fig, ax = new_figure()
    ax.plot(s.index, s.values, color=COLORS[0], lw=LINE_WIDTH, label="미 10Y-2Y 스프레드")
    ax.axhline(0, color="#999999", lw=0.8)
    unit_label(ax, "(%p)")
    date_axis(ax, s.index[0], s.index[-1])
    legend(ax)
    cap = caption("미 10Y-2Y 스프레드", [latest(s, '.2f', '%p')], [f.source])
    return ChartResult(save(fig, "1_spread"), cap)


def chart_10y() -> ChartResult:
    start = dt.date(2020, 1, 1)
    countries = [
        ("미국", [("fred_DGS10", "FRED", lambda: fetch_fred("DGS10")),
                  ("yahoo_TNX", "Yahoo", lambda: fetch_yahoo("^TNX", start))]),
        ("한국", [("ecos_817Y002_010210000", "ECOS 일별",
                   lambda: fetch_ecos(*ECOS_KR10Y, "ecos_817Y002_010210000", start)),
                  ("fred_IRLTLT01KRM156N", "FRED 월평균(ECOS 실패 대체)",
                   lambda: fetch_fred("IRLTLT01KRM156N"))]),
        ("중국", []),  # 안정적인 무료 출처 없음
        ("일본", [("mof_JGB10Y", "재무성", fetch_mof_jgb10),
                  ("fred_IRLTLT01JPM156N", "FRED", lambda: fetch_fred("IRLTLT01JPM156N"))]),
    ]
    fig, ax = new_figure()
    lines, sources = {}, []
    last = pd.Timestamp(start)
    for i, (country, chain) in enumerate(countries):
        if not chain:  # 값이 없는 나라는 그래프·범례·캡션 모두에서 뺀다
            continue
        try:
            f = fetch_chain(f"{country} 10년", chain)
        except SourceError as e:
            print(f"[경고] {e}", file=sys.stderr)
            continue
        s = since(f.series, start)
        ax.plot(plot_x(s), s.values, color=COLORS[i], lw=LINE_WIDTH, label=country)
        lines[country] = f"{country} {latest(s, '.2f', '%', chg_unit='%p')}"
        sources.append(f"{country} {f.source}")
        last = max(last, s.index[-1])
    if not sources:
        plt.close(fig)
        raise SourceError("모든 국가 데이터 수집 실패")
    unit_label(ax, "(%)")
    date_axis(ax, pd.Timestamp(start), last)
    legend(ax)
    order = ["미국", "한국", "일본", "중국"]
    cap = caption("주요국 10년 국채금리", [lines[c] for c in order if c in lines], sources)
    return ChartResult(save(fig, "2_10y"), cap)


def brent_spot_premium(futures: pd.Series) -> str | None:
    """브렌트 현물(FRED Dated Brent) − 브렌트 ICE 선물(페트로넷), 두 값이 모두 있는 최근 날짜 기준."""
    try:
        spot = fetch_chain("브렌트 현물", [("fred_DCOILBRENTEU", "FRED",
                                          lambda: fetch_fred("DCOILBRENTEU"))]).series
    except SourceError as e:
        print(f"[경고] {e}", file=sys.stderr)
        return None
    both = pd.concat([spot.rename("spot"), futures.rename("fut")], axis=1, sort=True).dropna()
    if both.empty:
        return None
    d = both.index[-1]
    prem = both["spot"].iloc[-1] - both["fut"].iloc[-1]
    when = date_suffix(d)
    return f"브렌트 현물 프리미엄 {'+' if prem >= 0 else '-'}${abs(prem):.2f}" + (f" ({when})" if when else "")


def chart_oil() -> ChartResult:
    """페트로넷 한 곳에서 두바이 현물·브렌트 ICE 선물·WTI NYMEX 선물을 받는다."""
    start = YEAR_START
    oils = [
        ("WTI", "WTI(NYMEX 선물)",
         [("petronet_WTI", "Petronet", lambda: fetch_petronet("WTI", start)),
          ("yahoo_CL=F", "Yahoo", lambda: fetch_yahoo("CL=F", start))]),
        ("브렌트", "브렌트(ICE 선물)",
         [("petronet_Brent", "Petronet", lambda: fetch_petronet("Brent", start)),
          ("yahoo_BZ=F", "Yahoo", lambda: fetch_yahoo("BZ=F", start))]),
        ("두바이", "두바이(현물)",
         [("petronet_Dubai", "Petronet", lambda: fetch_petronet("Dubai", start)),
          ("fred_POILDUBUSDM", "FRED", lambda: fetch_fred("POILDUBUSDM"))]),
    ]
    fig, ax = new_figure()
    parts, sources, missing = [], [], []
    brent = None
    last = pd.Timestamp(start)
    for i, (name, label, chain) in enumerate(oils):
        try:
            f = fetch_chain(name, chain)
        except SourceError as e:
            print(f"[경고] {e}", file=sys.stderr)
            missing.append(f"{name} 출처 없음")
            continue
        s = since(f.series, start)
        if s.empty:
            missing.append(f"{name} 올해 데이터 없음")
            continue
        monthly = period_word(s) == "전월"
        if monthly:
            label = f"{name}(월평균)"
        ax.plot(plot_x(s), s.values, color=COLORS[i], lw=LINE_WIDTH, label=label,
                marker="o" if monthly else None, markersize=4)
        parts.append(f"{name}(월평균) {latest(s, '.2f', prefix='$')}" if monthly
                     else f"{name} {latest(s, '.2f', prefix='$')}")
        sources.append(f.source)
        if name == "브렌트":
            brent = s
        last = max(last, s.index[-1])
    if not parts:
        plt.close(fig)
        raise SourceError("모든 유종 데이터 수집 실패")
    unit_label(ax, "(달러/배럴)")
    date_axis(ax, pd.Timestamp(start), last)
    legend(ax)
    premium = brent_spot_premium(brent) if brent is not None else None
    if premium:
        sources.append("FRED")
    cap = caption("유가", parts + missing + ([premium] if premium else []), sources)
    return ChartResult(save(fig, "3_oil"), cap)


def chart_gasoline() -> ChartResult:
    start = dt.date(2022, 1, 1)
    f = fetch_chain("가솔린", [("fred_GASREGW", "FRED", lambda: fetch_fred("GASREGW"))])
    s = since(f.series, start)
    fig, ax = new_figure()
    ax.plot(s.index, s.values, color=COLORS[0], lw=LINE_WIDTH, label="미 가솔린 소매가격")
    unit_label(ax, "(달러/갤런)")
    date_axis(ax, s.index[0], s.index[-1])
    legend(ax)
    cap = caption("미 가솔린 소매가격", [latest(s, '.2f', '/갤런', prefix='$', chg_unit='')], [f.source])
    return ChartResult(save(fig, "4_gasoline"), cap)


def _stock_chain(ticker: str, stooq_symbol: str, start: dt.date):
    key = ticker.replace("^", "")
    return [
        (f"yahoo_{key}", "Yahoo", lambda: fetch_yahoo(ticker, start)),
        (f"stooq_{key}", "stooq", lambda: fetch_stooq(stooq_symbol, start)),
    ]


def chart_dtcr_nvda() -> ChartResult:
    start = dt.date(2023, 1, 1)
    a = fetch_chain("DTCR", _stock_chain("DTCR", "dtcr.us", start))
    b = fetch_chain("NVDA", _stock_chain("NVDA", "nvda.us", start))
    sa, sb = since(a.series, start), since(b.series, start)
    fig, ax = new_figure()
    ax2 = ax.twinx()
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color("#888888")
    ax2.spines["right"].set_linewidth(0.8)
    ax2.spines["top"].set_visible(False)
    ax2.tick_params(colors="#333333", labelsize=11, length=4, width=0.8)
    h1, = ax.plot(sa.index, sa.values, color=COLORS[0], lw=LINE_WIDTH, label="데이터센터 ETF DTCR(좌)")
    h2, = ax2.plot(sb.index, sb.values, color=COLORS[1], lw=LINE_WIDTH, label="엔비디아 NVDA(우)")
    unit_label(ax, "(달러)")
    unit_label(ax2, "(달러)", right=True)
    date_axis(ax, min(sa.index[0], sb.index[0]), max(sa.index[-1], sb.index[-1]))
    legend(ax, [h1, h2])
    cap = caption("데이터센터 ETF · 엔비디아",
                  [f"DTCR {latest(sa, '.2f', prefix='$')}", f"NVDA {latest(sb, '.2f', prefix='$')}"],
                  [a.source, b.source])
    return ChartResult(save(fig, "5_dtcr_nvda"), cap)


def chart_ibb_sox() -> ChartResult:
    fetch_start = YEAR_START - dt.timedelta(days=14)
    a = fetch_chain("IBB", _stock_chain("IBB", "ibb.us", fetch_start))
    b = fetch_chain("SOX", _stock_chain("^SOX", "^sox", fetch_start))

    def rebase(s: pd.Series) -> pd.Series:
        base_part = s[s.index < pd.Timestamp(YEAR_START)]
        base = base_part.iloc[-1] if not base_part.empty else since(s, YEAR_START).iloc[0]
        return since(s, YEAR_START) / base * 100

    ra, rb = rebase(a.series), rebase(b.series)
    fig, ax = new_figure()
    ax.plot(ra.index, ra.values, color=COLORS[0], lw=LINE_WIDTH, label="나스닥 바이오테크 IBB")
    ax.plot(rb.index, rb.values, color=COLORS[1], lw=LINE_WIDTH, label="필라델피아 반도체 SOX")
    ax.axhline(100, color="#999999", lw=0.8)
    unit_label(ax, f"({TODAY.year % 100}/01/01=100)")
    date_axis(ax, pd.Timestamp(YEAR_START), max(ra.index[-1], rb.index[-1]))
    legend(ax)
    cap = caption("바이오테크 · 반도체 (연초=100)",
                  [f"IBB {latest(ra, '.1f')}", f"SOX {latest(rb, '.1f')}"], [a.source, b.source])
    return ChartResult(save(fig, "6_ibb_sox"), cap)


CHARTS = [
    (1, "미 10Y-2Y 스프레드", chart_spread),
    (2, "주요국 10년 국채금리", chart_10y),
    (3, "유가", chart_oil),
    (4, "미 가솔린 소매가격", chart_gasoline),
    (5, "데이터센터 ETF·엔비디아", chart_dtcr_nvda),
    (6, "바이오테크·반도체 연초 대비", chart_ibb_sox),
]


# ---------------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------------


class Telegram:
    """telegram_send 공용 모듈을 감싼다. 토큰이 없거나 --dry-run이면 전송하지 않는다."""

    def __init__(self, dry_run: bool):
        self.dry_run = dry_run or not telegram_send.enabled()

    def photo(self, path: Path, caption: str) -> int | None:
        return telegram_send.send_photo(path, caption, dry_run=self.dry_run)

    def text(self, text: str) -> int | None:
        return telegram_send.send_text(text[:1000], dry_run=self.dry_run)


# ---------------------------------------------------------------------------
# git 커밋
# ---------------------------------------------------------------------------


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def commit_data() -> None:
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return
    git("add", "data")
    if git("diff", "--cached", "--quiet", "--", "data").returncode == 0:
        print("[git] data/ 변경 없음")
        return
    msg = f"data: 차트 데이터 갱신 {TODAY.isoformat()}"
    ident = []
    if not git("config", "user.email").stdout.strip():
        ident = ["-c", "user.name=Owl Capital Briefing", "-c", "user.email=briefing@users.noreply.github.com"]
    c = git(*ident, "commit", "-m", msg, "--", "data")
    if c.returncode != 0:
        print(f"[git] 커밋 실패: {c.stderr.strip()}", file=sys.stderr)
        return
    target = os.environ.get("CHARTS_DATA_BRANCH")
    if not target:
        head = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
        target = head.split("/", 1)[1] if "/" in head else ""
    current = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for branch in [b for b in dict.fromkeys([target, current]) if b and b != "HEAD"]:
        p = git("push", "origin", f"HEAD:refs/heads/{branch}")
        if p.returncode == 0:
            print(f"[git] data/ 커밋을 {branch} 브랜치에 푸시")
            return
        print(f"[git] {branch} 푸시 실패: {p.stderr.strip()[-300:]}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Owl Capital 차트 브리핑")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    setup_korean_font()
    if not args.dry_run and not telegram_send.enabled():
        print("[경고] TELEGRAM_BOT_TOKEN 이 없어 전송 없이 out/ 에만 저장합니다.", file=sys.stderr)
    tg = Telegram(args.dry_run)

    only = {int(x) for x in args.only.split(",") if x.strip()}
    failures = 0
    for no, name, fn in CHARTS:
        if only and no not in only:
            continue
        try:
            res = fn()
            mid = tg.photo(res.path, res.caption)
            sent = "" if mid is None else f" (message_id={mid})"
            print(f"[OK] {no}. {name}{sent}: {res.caption.replace(chr(10), ' | ')}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            reason = _short(e) if not isinstance(e, SourceError) else str(e)
            print(f"[실패] {no}. {name}: {reason}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            try:
                tg.text(f"{name} 차트 생성 실패: {reason}")
            except Exception as e2:  # noqa: BLE001
                print(f"[실패] 실패 알림 전송도 실패: {_short(e2)}", file=sys.stderr)

    if not args.no_commit:
        commit_data()
    return 1 if failures == len([c for c in CHARTS if not only or c[0] in only]) else 0


if __name__ == "__main__":
    sys.exit(main())
