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


def feed_id(url: str) -> str:
    m = re.search(r"naver\.com/([\w.-]+)\.xml|//([\w-]+)\.blogspot", url)
    return (m.group(1) or m.group(2)) if m else url


def clean_link(link: str) -> str:
    return link.strip().split("?fromRss")[0].split("&fromRss")[0]


def read_feed(url: str, now: dt.datetime):
    last = None
    for _ in range(2):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            channel = ET.fromstring(r.content).find("channel")
            name = (channel.findtext("title") or feed_id(url)).strip()
            posts = []
            for item in channel.findall("item"):
                pub = item.findtext("pubDate")
                if not pub:
                    continue
                when = parsedate_to_datetime(pub)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                if dt.timedelta(0) <= now - when <= WINDOW or when > now:
                    posts.append((when, name, (item.findtext("title") or "").strip(),
                                  clean_link(item.findtext("link") or "")))
            return posts
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{type(last).__name__}: {last}"[:160])


def prepare() -> Prepared:
    now = dt.datetime.now(dt.timezone.utc)
    posts, failed, errors = [], [], []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [(url, ex.submit(read_feed, url, now)) for url in FEEDS]
    for url, fut in futures:
        try:
            posts.extend(fut.result())
        except Exception as e:  # noqa: BLE001
            failed.append(feed_id(url))
            errors.append(f"{feed_id(url)} {e}")
    posts.sort(key=lambda p: p[0])
    if posts:
        lines = [f"블로그 새 글 ({len(posts)}건)"]
        for _, name, title, link in posts:
            lines += [f"{name} — {title}", link]
    else:
        lines = ["블로그 새 글 없음"]
    if failed:
        lines.append("확인 실패: " + ", ".join(failed))
    text = "\n".join(lines)
    # 텔레그램 한 메시지는 4096자까지. 넘치면 둘로 나눈다
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 4000:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    chunks.append(cur)
    return Prepared([Message("text", c, label=f"새 글 목록{i + 1 if len(chunks) > 1 else ''}")
                     for i, c in enumerate(chunks)], errors)


if __name__ == "__main__":
    sys.exit(run_standalone(sys.modules[__name__]))
