"""브리핑 스크립트 공용 도구: 한국시간 날짜, 한글 폰트, 메시지 형식, 항목 실행."""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def today_kst() -> dt.date:
    return now_kst().date()


def md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def previous_business_day(today: dt.date | None = None) -> dt.date:
    """오늘(한국시간) 직전 평일. 토·일 새벽이면 금요일, 월요일 새벽이면 금요일."""
    d = (today or today_kst()) - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def is_recent(d) -> bool:
    """데이터 날짜가 오늘 또는 직전 영업일이면 True(캡션에 날짜를 쓰지 않는다)."""
    d = d.date() if hasattr(d, "date") and callable(d.date) else d
    return d >= previous_business_day()


def date_suffix(d, monthly: bool = False) -> str:
    """최근 1영업일 이내면 빈 문자열, 그보다 오래됐으면 'M/D'(월평균은 'M월')."""
    if monthly:
        return f"{d.month}월"
    return "" if is_recent(d) else md(d)


# ---------------------------------------------------------------------------
# 한글 폰트
# ---------------------------------------------------------------------------

KOREAN_FONTS = ["NanumGothic", "NanumBarunGothic", "Noto Sans CJK KR", "Noto Sans KR", "UnDotum"]
NANUM_PATH = Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")


def setup_korean_font() -> str | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    def find() -> str | None:
        names = {f.name for f in font_manager.fontManager.ttflist}
        return next((n for n in KOREAN_FONTS if n in names), None)

    # 굵은 글씨(NanumGothicBold 등)도 쓰도록 같은 폴더의 나눔 글꼴을 모두 등록
    for path in sorted(NANUM_PATH.parent.glob("NanumGothic*.ttf")) if NANUM_PATH.parent.exists() else []:
        font_manager.fontManager.addfont(str(path))
    name = find()
    if name is None:
        # 폰트가 없으면 설치 후 캐시 재구성
        cmd = ("apt-get install -y -q fonts-nanum >/dev/null 2>&1 || (apt-get update -q >/dev/null 2>&1 "
               "&& apt-get install -y -q fonts-nanum >/dev/null 2>&1)")
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
# 메시지와 항목 실행
# ---------------------------------------------------------------------------


@dataclass
class Message:
    kind: str  # "text" | "photo"
    text: str  # 텍스트 본문 또는 사진 캡션
    path: Path | None = None
    label: str = ""  # 로그용 이름
    html: bool = False  # True면 text가 이미 텔레그램 HTML(이스케이프 완료)


@dataclass
class Prepared:
    messages: list[Message]
    errors: list[str] = field(default_factory=list)  # 부분 실패 사유(로그용)


def prepare_safely(module) -> Prepared:
    """항목 모듈의 prepare()를 실행한다. 예외가 나면 실패 알림 한 줄로 바꾼다."""
    try:
        return module.prepare()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        reason = f"{type(e).__name__}: {e}"[:200]
        return Prepared([Message("text", module.FAIL_TEXT, label=f"{module.NAME} 실패 알림")], [reason])


def send_messages(messages: list[Message], dry_run: bool = False) -> list[tuple[Message, str]]:
    """메시지를 순서대로 보내고 (메시지, 'message_id=N' 또는 '실패: 사유') 목록을 돌려준다."""
    from telegram_send import TelegramError, send_photo, send_text

    results = []
    for m in messages:
        try:
            if m.kind == "photo":
                mid = send_photo(m.path, m.text, html=m.html, dry_run=dry_run)
            else:
                mid = send_text(m.text, html=m.html, dry_run=dry_run)
            results.append((m, "dry-run" if mid is None else f"message_id={mid}"))
        except (TelegramError, OSError) as e:
            print(f"[telegram] 실패: {e} ({m.label})", file=sys.stderr)
            results.append((m, f"실패: {e}"))
    return results


def run_standalone(module) -> int:
    """각 항목 스크립트를 단독 실행할 때 쓰는 진입점. --dry-run 지원."""
    dry_run = "--dry-run" in sys.argv[1:]
    prepared = prepare_safely(module)
    for err in prepared.errors:
        print(f"[{module.NAME}] {err}", file=sys.stderr)
    results = send_messages(prepared.messages, dry_run)
    for m, res in results:
        print(f"[{module.NAME}] {m.label}: {res}")
    return 1 if prepared.errors or any(r.startswith("실패") for _, r in results) else 0
