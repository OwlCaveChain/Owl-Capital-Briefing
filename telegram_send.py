"""텔레그램 전송 도우미. charts.py와 루틴 프롬프트(메시지 1~4)가 함께 쓴다.

토큰은 환경변수 TELEGRAM_BOT_TOKEN에서만 읽고, 오류 메시지에서도 가린다.
채팅 ID는 TELEGRAM_CHAT_ID를 쓰되, 없거나 텔레그램이 "chat not found"를 돌려주면
DEFAULT_CHAT_ID로 다시 보낸다.

사용법
  python telegram_send.py check                    getMe·getChat 확인
  python telegram_send.py text "본문"               sendMessage
  python telegram_send.py text -                   본문을 표준입력에서 읽음
  python telegram_send.py photo 파일.png "캡션"      sendPhoto
성공하면 "ok message_id=N"을 출력하고 0으로, 실패하면 사유를 출력하고 1로 끝난다.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

DEFAULT_CHAT_ID = "7164046356"


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None, dry_run: bool = False):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        env_chat = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.chat_id = env_chat.strip() or DEFAULT_CHAT_ID
        self.dry_run = dry_run or not self.token

    def _mask(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text

    def _call(self, method: str, data: dict | None = None, files: dict | None = None) -> dict:
        last = ""
        for attempt in range(2):
            try:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}",
                                  data=data, files=files, timeout=60)
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                if r.ok and body.get("ok"):
                    return body.get("result", {})
                last = f"{method} HTTP {r.status_code}: {body.get('description') or r.text[:200]}"
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    break  # 요청 자체가 잘못된 경우 재시도해도 같다
            except requests.RequestException as e:
                last = f"{method} {type(e).__name__}: {e}"
            if attempt == 0:
                time.sleep(2)
        raise TelegramError(self._mask(last))

    def _send(self, method: str, data: dict, files: dict | None = None) -> dict:
        try:
            return self._call(method, {**data, "chat_id": self.chat_id}, files)
        except TelegramError as e:
            if "chat not found" not in str(e) or self.chat_id == DEFAULT_CHAT_ID:
                raise
            print("[경고] TELEGRAM_CHAT_ID로 보낼 수 없어(chat not found) 기본 채팅 ID로 다시 보냅니다.",
                  file=sys.stderr)
            self.chat_id = DEFAULT_CHAT_ID
            return self._call(method, {**data, "chat_id": self.chat_id}, files)

    def text(self, text: str) -> dict:
        if self.dry_run:
            print(f"[dry-run] 텍스트 | {text}")
            return {}
        return self._send("sendMessage", {"text": text[:4096], "disable_web_page_preview": "true"})

    def photo(self, path: Path | str, caption: str) -> dict:
        path = Path(path)
        if self.dry_run:
            print(f"[dry-run] 사진 {path.name} | {caption}")
            return {}
        data = path.read_bytes()
        return self._send("sendPhoto", {"caption": caption[:1024]},
                          files={"photo": (path.name, data, "image/png")})

    def check(self) -> str:
        me = self._call("getMe")
        env_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        lines = [f"봇 @{me.get('username')} 정상",
                 f"TELEGRAM_CHAT_ID {'없음' if not env_chat else ('기본값과 일치' if env_chat == DEFAULT_CHAT_ID else '기본값과 다름')}"]
        for label, cid in [("환경변수 채팅", env_chat), ("기본 채팅", DEFAULT_CHAT_ID)]:
            if not cid:
                continue
            try:
                self._call("getChat", {"chat_id": cid})
                lines.append(f"{label} 접근 가능")
            except TelegramError as e:
                lines.append(f"{label} 접근 불가: {e}")
        return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"check", "text", "photo"}:
        print(__doc__, file=sys.stderr)
        return 2
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        print("실패: TELEGRAM_BOT_TOKEN 환경변수가 없습니다.")
        return 1
    tg = Telegram()
    try:
        if argv[0] == "check":
            print(tg.check())
            return 0
        if argv[0] == "text":
            body = sys.stdin.read() if len(argv) < 2 or argv[1] == "-" else argv[1]
            res = tg.text(body)
        else:
            res = tg.photo(argv[1], argv[2] if len(argv) > 2 else "")
    except (TelegramError, OSError, IndexError) as e:
        print(f"실패: {e}")
        return 1
    print(f"ok message_id={res.get('message_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
