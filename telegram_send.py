"""텔레그램 전송 공용 모듈.

모듈로 쓸 때
  from telegram_send import send_text, send_photo
  mid = send_text("본문")              # 성공하면 message_id, dry-run이면 None
  mid = send_photo(path, "캡션")       # 실패하면 TelegramError
  send_text("<b>굵게</b>", html=True)  # 이미 HTML로 쓴 본문(특수문자는 esc()로 이스케이프)

전송은 항상 parse_mode=HTML, 링크 미리보기 끔. html=False(기본)면 본문 전체를 이스케이프한다.
dry-run은 텔레그램에 보일 모양(태그를 뺀 글자)을 그대로 출력한다.

명령줄로 쓸 때
  python telegram_send.py check
  python telegram_send.py text "본문"          (여러 줄: printf '%s' "본문" | python telegram_send.py text -)
  python telegram_send.py photo 파일.png "캡션"
  성공하면 "ok message_id=N", 실패하면 "실패: 사유" 를 출력한다.

환경변수
  TELEGRAM_BOT_TOKEN   봇 토큰 (값은 출력하지 않는다)
  TELEGRAM_CHAT_ID     채팅 ID (없거나 "chat not found"면 기본값 7164046356)
"""

from __future__ import annotations

import html as _html
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

DEFAULT_CHAT_ID = "7164046356"
TIMEOUT = 60


class TelegramError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTML 도우미
# ---------------------------------------------------------------------------

ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
                "tg-spoiler", "blockquote", "span"}


def esc(text: str) -> str:
    """텔레그램 HTML 모드용 이스케이프(&, <, >)."""
    return _html.escape(str(text), quote=False)


def link(url: str, text: str) -> str:
    return f'<a href="{_html.escape(url, quote=True)}">{esc(text)}</a>'


def visible(html_text: str) -> str:
    """텔레그램에 보이는 글자(태그 제거, 엔티티 복원)."""
    return _html.unescape(re.sub(r"<[^>]+>", "", html_text))


class _TagCheck(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"허용되지 않는 태그 <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"짝이 맞지 않는 </{tag}>")


def check_html(html_text: str) -> list[str]:
    """태그 짝과 허용 태그를 검사해 문제 목록을 돌려준다."""
    if re.search(r"&(?![a-zA-Z]+;|#\d+;|#x[0-9a-fA-F]+;)", html_text):
        return ["이스케이프되지 않은 &"]
    c = _TagCheck()
    c.feed(html_text)
    c.close()
    return c.errors + [f"닫히지 않은 <{t}>" for t in c.stack]


def _token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def enabled() -> bool:
    """토큰이 있으면 실제 전송, 없으면 dry-run."""
    return _token() is not None


def _chat_ids() -> list[str]:
    env = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    return list(dict.fromkeys([c for c in (env, DEFAULT_CHAT_ID) if c]))


def _call(method: str, data: dict, files_path: Path | None = None) -> dict:
    """채팅 ID 후보를 차례로 쓰고, 일시 오류는 한 번 더 시도한다. 성공 시 result 객체.

    HTML 해석 오류("can't parse entities")면 태그를 뺀 글자로 한 번 더 보낸다.
    """
    token = _token()
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN 없음")
    url = f"https://api.telegram.org/bot{token}/{method}"
    last = "알 수 없는 오류"
    for chat_id in _chat_ids():
        payload = {**data, "chat_id": chat_id}
        for attempt in range(3):
            try:
                if files_path is not None:
                    with open(files_path, "rb") as fh:
                        r = requests.post(url, data=payload, timeout=TIMEOUT,
                                          files={"photo": (files_path.name, fh, "image/png")})
                else:
                    r = requests.post(url, data=payload, timeout=TIMEOUT)
                j = r.json()
            except (requests.RequestException, ValueError) as e:
                # 예외 메시지에 토큰이 든 URL이 섞일 수 있어 종류만 남긴다
                last = f"{type(e).__name__}"
                time.sleep(2)
                continue
            if j.get("ok"):
                return j["result"]
            last = str(j.get("description") or f"HTTP {r.status_code}")
            if "chat not found" in last.lower():
                break  # 다음 채팅 ID 후보로
            if "can't parse entities" in last.lower() and payload.get("parse_mode"):
                print(f"[telegram] HTML 해석 실패, 일반 텍스트로 재전송: {last}", file=sys.stderr)
                payload.pop("parse_mode")
                for key in ("text", "caption"):
                    if key in payload:
                        payload[key] = visible(payload[key])
                continue
            if r.status_code == 429:
                time.sleep(int(j.get("parameters", {}).get("retry_after", 3)))
            elif attempt == 0:
                time.sleep(2)
            else:
                break
    raise TelegramError(last)


def _body(text: str, html: bool) -> str:
    return text if html else esc(text)


def _dry_run_print(kind: str, body: str) -> None:
    problems = check_html(body)
    print(f"[telegram dry-run] {kind}")
    print("┌" + "─" * 40)
    for line in visible(body).split("\n"):
        print(f"│ {line}")
    print("└" + "─" * 40)
    if problems:
        print(f"[telegram dry-run] HTML 경고: {', '.join(problems)}")


def send_text(text: str, *, html: bool = False, dry_run: bool = False) -> int | None:
    body = _body(text, html)
    if dry_run or not enabled():
        _dry_run_print("텍스트", body)
        return None
    res = _call("sendMessage", {"text": body, "parse_mode": "HTML", "disable_web_page_preview": "true",
                                "link_preview_options": '{"is_disabled": true}'})
    mid = res.get("message_id")
    print(f"[telegram] ok message_id={mid} (텍스트)")
    return mid


def send_photo(path: str | Path, caption: str = "", *, html: bool = False, dry_run: bool = False) -> int | None:
    path = Path(path)
    body = _body(caption, html)
    if dry_run or not enabled():
        _dry_run_print(f"사진 {path.name}", body)
        return None
    res = _call("sendPhoto", {"caption": body, "parse_mode": "HTML"}, files_path=path)
    mid = res.get("message_id")
    print(f"[telegram] ok message_id={mid} (사진 {path.name})")
    return mid


def check() -> str:
    """봇 토큰과 채팅 접근을 확인하고 채팅 이름을 돌려준다."""
    res = _call("getChat", {})
    return str(res.get("title") or res.get("first_name") or res.get("id"))


def _main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("check", "text", "photo"):
        print(__doc__)
        return 2
    try:
        if argv[0] == "check":
            check()
            print("ok 봇·채팅 접근 확인")
            return 0
        if not enabled():
            raise TelegramError("TELEGRAM_BOT_TOKEN 없음")
        if argv[0] == "text":
            text = sys.stdin.read() if len(argv) < 2 or argv[1] == "-" else argv[1]
            mid = send_text(text)
        else:
            mid = send_photo(argv[1], argv[2] if len(argv) > 2 else "")
        print(f"ok message_id={mid}")
        return 0
    except (TelegramError, OSError, IndexError) as e:
        print(f"실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
