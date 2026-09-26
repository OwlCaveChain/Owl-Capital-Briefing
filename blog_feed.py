"""메시지 2: 블로그 새 글(최근 24시간).

단독 실행: python blog_feed.py [--dry-run]
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

import requests

from briefing_common import UA, Message, Prepared, run_standalone
from telegram_send import esc, link

NAME = "블로그 새 글"
FAIL_TEXT = "블로그 새 글 항목 확인 실패"
FEEDS = [
    "https://blog.rss.naver.com/pillion21.xml",
    "https://blog.rss.naver.com/tosoha1.xml",
    "https://blog.rss.naver.com/ranto28.xml",
    "https://blog.rss.naver.com/kk_kontemp.xml",
    "https://blog.rss.naver.com/crush212121.xml",
    "https://blog.rss.naver.com/circleofcompetence.xml",
    "https://blog.rss.naver.com/survivaldopb.xml",
    "https://blog.rss.naver.com/jeunkim.xml",
    "https://blog.rss.naver.com/thingschange_.xml",
    "https://rafikiresearch.blogspot.com/feeds/posts/default?alt=rss",
]
WINDOW = dt.timedelta(hours=24)
MAX_LEN = 4000  # 텔레그램 한 메시지 4096자 한도에 여유를 둔다
MAX_PER_BLOG = 5  # 블로그당 표시할 최대 글 수(최신순). 나머지는 "· 외 n건" 링크
# RSS를 못 읽었을 때 "확인 실패"에 쓸 이름(채널 제목)
KNOWN_NAMES = {
    "pillion21": "알바트로스의 파생 이야기", "tosoha1": "이것 또한 지나가리라", "ranto28": "메르의 블로그",
    "kk_kontemp": "KK Kontemporaries", "crush212121": "적절한 지식과 검증된 판단",
    "circleofcompetence": "진리 그리고 투자", "survivaldopb": "도피비의 생존투자",
    "jeunkim": "피우스의 책도둑 & 매거진", "thingschange_": "\"퀄리티\"를 찾아서",
    "rafikiresearch": "Rafiki Research",
}


def feed_id(url: str) -> str:
    m = re.search(r"naver\.com/([\w.-]+)\.xml|//([\w-]+)\.blogspot", url)
    return (m.group(1) or m.group(2)) if m else url


def clean_title(title: str) -> str:
    """연속 공백·줄바꿈을 한 칸으로."""
    return " ".join(title.split())


def list_url(feed_url: str, channel_link: str) -> str:
    """블로그 글 목록 페이지. 네이버는 PostList, 그 외는 채널 링크(블로그 첫 화면)."""
    if "blog.rss.naver.com" in feed_url:
        return f"https://blog.naver.com/PostList.naver?blogId={feed_id(feed_url)}"
    return clean_link(channel_link) or feed_url


def clean_link(link: str) -> str:
    return link.strip().split("?fromRss")[0].split("&fromRss")[0]


def read_feed(url: str, now: dt.datetime):
    last = None
    for _ in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            channel = ET.fromstring(r.content).find("channel")
            name = (channel.findtext("title") or KNOWN_NAMES.get(feed_id(url), feed_id(url))).strip()
            posts = []
            for item in channel.findall("item"):
                pub = item.findtext("pubDate")
                if not pub:
                    continue
                when = parsedate_to_datetime(pub)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                if dt.timedelta(0) <= now - when <= WINDOW or when > now:
                    posts.append((when, clean_title(item.findtext("title") or ""),
                                  clean_link(item.findtext("link") or "")))
            return name, list_url(url, channel.findtext("link") or ""), sorted(posts, reverse=True)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{type(last).__name__}: {last}"[:160])


def group_block(name: str, posts, more_url: str = "") -> list[str]:
    """머리줄 + 최신 글 최대 MAX_PER_BLOG건, 초과분은 '· 외 n건'(글 목록 링크)."""
    lines = [f"🦉 <b>{esc(name)}</b> ({len(posts)}건)"]
    for _, title, url in posts[:MAX_PER_BLOG]:
        lines.append("· " + (link(url, title) if url else esc(title)))
    rest = len(posts) - MAX_PER_BLOG
    if rest > 0:
        more = f"외 {rest}건"
        lines.append("· " + (link(more_url, more) if more_url else esc(more)))
    return lines


def split_messages(header: str, blocks: list[list[str]], footer: str) -> list[str]:
    """블로그 묶음 단위로 MAX_LEN을 넘지 않게 나눈다(묶음 하나가 넘치면 줄 단위로)."""
    chunks: list[str] = []
    cur = header
    for block in blocks:
        text = "\n".join(block)
        if len(cur) + 2 + len(text) <= MAX_LEN:
            cur = f"{cur}\n\n{text}" if cur else text
            continue
        if cur:
            chunks.append(cur)
        cur = ""
        for line in block:  # 묶음 하나가 한도를 넘는 드문 경우
            if cur and len(cur) + 1 + len(line) > MAX_LEN:
                chunks.append(cur)
                cur = ""
            cur = f"{cur}\n{line}" if cur else line
    if footer:
        if cur and len(cur) + 2 + len(footer) > MAX_LEN:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{footer}" if cur else footer
    if cur:
        chunks.append(cur)
    return chunks


def prepare() -> Prepared:
    now = dt.datetime.now(dt.timezone.utc)
    blocks, failed, errors, total = [], [], [], 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [(url, ex.submit(read_feed, url, now)) for url in FEEDS]
    for url, fut in futures:  # FEEDS 순서대로
        try:
            name, more_url, posts = fut.result()
        except Exception as e:  # noqa: BLE001
            failed.append(KNOWN_NAMES.get(feed_id(url), feed_id(url)))
            errors.append(f"{feed_id(url)} {e}")
            continue
        if posts:
            blocks.append(group_block(name, posts, more_url))
            total += len(posts)
    header = f"🌞 블로그 새 글 {total}건" if total else "🌞 블로그 새 글 없음"
    footer = "확인 실패: " + esc(", ".join(failed)) if failed else ""
    chunks = split_messages(header, blocks, footer)
    return Prepared([Message("text", c, label=f"새 글 목록{i + 1 if len(chunks) > 1 else ''}", html=True)
                     for i, c in enumerate(chunks)], errors)


if __name__ == "__main__":
    sys.exit(run_standalone(sys.modules[__name__]))
