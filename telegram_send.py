"""텔레그램 전송 공용 모듈.

모듈로 쓸 때
  from telegram_send import send_text, send_photo
  mid = send_text("본문")              # 성공하면 message_id, dry-run이면 None
  mid = send_photo(path, "캡션")       # 실패하면 TelegramError

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

import os
import sys
import time
from pathlib import Path

import requests

DEFAULT_CHAT_ID = "7164046356"
TIMEOUT = 60


class TelegramError(Exception):
    pass


def _token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def enabled() -> bool:
    """토큰이 있으면 실제 전송, 없으면 dry-run."""
    return _token() is not None


def _chat_ids() -> list[str]:
    env = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    return list(dict.fromkeys([c for c in (env, DEFAULT_CHAT_ID) if c]))


def _call(method: str, data: dict, files_path: Path | None = None) -> dict:
    """채팅 ID 후보를 차례로 쓰고, 일시 오류는 한 번 더 시도한다. 성공 시 result 객체."""
    token = _token()
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN 없음")
    url = f"https://api.telegram.org/bot{token}/{method}"
    last = "알 수 없는 오류"
    for chat_id in _chat_ids():
        for attempt in range(2):
            try:
                if files_path is not None:
                    with open(files_path, "rb") as fh:
                        r = requests.post(url, data={**data, "chat_id": chat_id}, timeout=TIMEOUT,
                                          files={"photo": (files_path.name, fh, "image/png")})
                else:
                    r = requests.post(url, data={**data, "chat_id": chat_id}, timeout=TIMEOUT)
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
            if r.status_code == 429:
                time.sleep(int(j.get("parameters", {}).get("retry_after", 3)))
            elif attempt == 0:
                time.sleep(2)
    raise TelegramError(last)


def send_text(text: str, *, dry_run: bool = False) -> int | None:
    if dry_run or not enabled():
        print(f"[telegram dry-run] 텍스트 | {text}")
        return None
    res = _call("sendMessage", {"text": text[:4096], "disable_web_page_preview": "true"})
    mid = res.get("message_id")
    print(f"[telegram] ok message_id={mid} (텍스트)")
    return mid


def send_photo(path: str | Path, caption: str = "", *, dry_run: bool = False) -> int | None:
    path = Path(path)
    if dry_run or not enabled():
        print(f"[telegram dry-run] 사진 {path.name} | {caption}")
        return None
    res = _call("sendPhoto", {"caption": caption[:1024]}, files_path=path)
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
