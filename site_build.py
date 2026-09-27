"""브리핑 사이트(docs/, GitHub Pages) 생성. 텔레그램으로는 보내지 않는다.

맨 앞에 아침 대시보드·섹터 전체표, 이어서 메시지 1~6의 이미지·캡션과 블로그 새 글 목록으로
docs/<오늘 날짜>.html 을 쓰고, index.html 을 그 페이지로 바꾸고, archive.html 에 날짜를 추가한다.
모든 HTML에 검색 제외 표시(noindex)를 넣어 검색 결과에 나오지 않게 한다(링크를 아는 사람만).
이미지는 docs/charts/<날짜>-<이름>.png 로 복사한다. 끝나면 docs/ 전체에 비밀값이 없는지 검사한다.

  python site_build.py            모든 항목을 다시 만들어 생성 + 비밀값 검사(git 커밋·푸시는 하지 않는다)
  python site_build.py --check    비밀값·noindex 검사만

briefing.py 는 텔레그램 전송을 마친 뒤 build_from_briefing() 으로 전송 때 만든 이미지를 그대로 써서
사이트를 만들고 docs/ 를 main 에 커밋·푸시한다(publish).
블로그 한 줄 요약은 사람이 쓴 것만 쓴다: 같은 날짜 페이지가 이미 있으면 같은 글 URL의 요약을 이어 쓴다.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import blog_feed
import dashboard
import fear_greed
import finviz_heatmap
import memory
import natgas
from briefing_common import OUT_DIR, ROOT, Message, Prepared, now_kst, prepare_safely, setup_korean_font

DOCS = ROOT / "docs"
PREVIEW = OUT_DIR / "site"  # dry-run 은 docs/ 대신 여기에 만든다
WEEKDAYS = "월화수목금토일"
# 검색 결과에 나오지 않게(링크를 아는 사람만). robots.txt 로 막으면 이 표시를 못 읽으니 막지 않는다.
NOINDEX = '<meta name="robots" content="noindex, nofollow, noarchive">'

# 비밀값 검사: 이름에 이런 말이 들어간 환경변수 값이 docs/ 에 있으면 실패
SECRET_ENV = re.compile(r"TOKEN|KEY|SECRET|PASS|AUTH|CREDENTIAL|CHAT_ID", re.I)
SECRET_PATTERNS = [
    (r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", "텔레그램 봇 토큰 형식"),
    (r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b", "GitHub 토큰 형식"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS 액세스 키 형식"),
    (r"sk-ant-[A-Za-z0-9_-]{20,}", "Anthropic 키 형식"),
    (r"api\.telegram\.org/bot", "텔레그램 API 주소"),
    (r"(?i)(api[_-]?key|access[_-]?token|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}", "키=값 형태"),
]

STYLE = """
  :root{
    --ink:#1F2A44; --sub:#56617A; --bg:#EEF1F4; --paper:#FFFFFF; --line:#D5DBE3; --bad:#B4442F;
    box-sizing:border-box;
    padding-top:env(safe-area-inset-top,0px); padding-bottom:env(safe-area-inset-bottom,0px);
  }
  @media (prefers-color-scheme: dark){
    :root{ --ink:#E6EAF2; --sub:#A3ADBF; --bg:#141A26; --paper:#1D2433; --line:#2E3747; --bad:#E07A66; }
  }
  *,*::before,*::after{box-sizing:inherit}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:18px/1.7 "Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
  main{max-width:720px;margin:0 auto;padding:28px 16px 48px}
  header{margin-bottom:28px}
  h1{font:700 30px/1.3 "Gowun Batang",serif;margin:0}
  .date{color:var(--sub);margin-top:4px}
  section{background:var(--paper);border:1px solid var(--line);border-radius:14px;
    padding:22px 20px;margin-bottom:18px}
  h2{font:700 21px/1.4 "Gowun Batang",serif;margin:0 0 12px}
  h3{font-size:17px;margin:20px 0 8px}
  h2 .no{color:var(--sub);font-weight:400;margin-right:6px}
  figure{margin:0 0 18px}
  figure:last-child{margin-bottom:0}
  img{max-width:100%;height:auto;display:block;border-radius:8px;background:#fff}
  figcaption,.text{white-space:pre-line;font-size:16px;margin-top:8px;font-variant-numeric:tabular-nums}
  .fail{color:var(--bad)}
  ul{margin:0;padding-left:1.1em}
  li{margin-bottom:10px}
  .blog{font-weight:700;margin:18px 0 6px}
  .blog:first-of-type{margin-top:0}
  a{color:var(--ink);text-underline-offset:3px}
  .who{color:var(--sub);font-size:15px}
  footer{color:var(--sub);font-size:15px;text-align:center;margin-top:28px}
  footer a{color:var(--sub)}
"""

HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{noindex}
<title>{title}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🦉</text></svg>">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@700&family=Noto+Sans+KR:wght@400;700&display=swap">
<style>{style}</style>
</head>
<body>
<main>
"""

FOOT = """  <footer>{footer}</footer>
</main>
</body>
</html>
"""


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------


def collect_messages() -> dict[int, list[Message]]:
    """대시보드(0)·메시지 1·3·4·6 준비(전송 없음). 2(블로그)는 collect_blogs 에서 따로."""
    items = [(0, dashboard), (1, fear_greed), (3, finviz_heatmap), (4, natgas), (6, memory)]
    with ThreadPoolExecutor(max_workers=len(items)) as ex:
        futures = {no: ex.submit(prepare_safely, mod) for no, mod in items}
    out = {}
    for no, mod in items:
        p = futures[no].result()
        for err in p.errors:
            print(f"[{mod.NAME}] {err}", file=sys.stderr)
        out[no] = p.messages
    return out


def collect_charts() -> list[tuple[str, Message]]:
    """메시지 5: charts.py 차트 6장. 실패한 차트는 텍스트 메시지로."""
    import charts

    out = []
    for no, name, fn in charts.CHARTS:
        try:
            res = fn()
            out.append((name, Message("photo", res.caption, res.path, name)))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            out.append((name, Message("text", f"{name} 차트 생성 실패: {charts._short(e)}", label=name)))
    return out


def charts_from_manifest(path: Path = OUT_DIR / "charts.json") -> list[tuple[str, Message]]:
    """charts.py 가 이번 실행에서 남긴 out/charts.json 의 차트(텔레그램으로 보낸 것과 같은 파일)."""
    import json

    out = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        if item["kind"] == "photo":
            out.append((item["name"], Message("photo", item["text"], Path(item["path"]), item["name"])))
        else:
            out.append((item["name"], Message("text", item["text"], label=item["name"])))
    return out


def collect_blogs() -> tuple[list[tuple[str, str, list]], list[str]]:
    """[(블로그 이름, 글 목록 URL, [(시각, 제목, URL)])], 확인 실패한 블로그 이름."""
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [(url, ex.submit(blog_feed.read_feed, url, now)) for url in blog_feed.FEEDS]
    blogs, failed = [], []
    for url, fut in futures:
        try:
            name, more, posts = fut.result()
        except Exception as e:  # noqa: BLE001
            fid = blog_feed.feed_id(url)
            failed.append(blog_feed.KNOWN_NAMES.get(fid, fid))
            print(f"[블로그] {fid} {e}", file=sys.stderr)
            continue
        if posts:
            blogs.append((name, more, posts))
    return blogs, failed


def old_summaries(page: Path) -> dict[str, str]:
    """기존 페이지의 {글 URL: 한 줄 요약(HTML 이스케이프된 그대로)}."""
    if not page.exists():
        return {}
    text = page.read_text(encoding="utf-8")
    # 예전 형식 '<span class="who">블로그</span> — 요약' 과 이 스크립트 형식 '<span class="who">요약</span>'
    pat = r'<a href="([^"]+)">[^<]*</a><br>\s*<span class="who">([^<]*)</span>(?: — ([^<]*))?</li>'
    return {html.unescape(u): (b or a).strip() for u, a, b in re.findall(pat, text)}


# ---------------------------------------------------------------------------
# 페이지
# ---------------------------------------------------------------------------


class Page:
    def __init__(self, date_key: str, chart_dir: Path):
        self.date_key = date_key
        self.chart_dir = chart_dir
        self.used: set[str] = set()

    def image(self, path: Path, slug: str) -> str:
        """이미지를 charts/ 로 복사하고 페이지 기준 상대 경로를 돌려준다."""
        name = f"{self.date_key}-{slug}.png"
        self.chart_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, self.chart_dir / name)
        self.used.add(name)
        return f"charts/{name}"

    def message(self, m: Message, slug: str, alt: str) -> str:
        if m.kind == "album" and m.paths and all(Path(p).exists() for p in m.paths):
            return "".join(f'    <figure>\n      <img src="{self.image(Path(p), f"{slug}-{i}")}" alt="{esc(alt)}" '
                           f'loading="lazy">\n    </figure>\n' for i, p in enumerate(m.paths, 1))
        if m.kind == "photo" and m.path and Path(m.path).exists():
            src = self.image(Path(m.path), slug)
            cap = f"\n      <figcaption>{esc(m.text)}</figcaption>" if m.text.strip() else ""
            return f'    <figure>\n      <img src="{src}" alt="{esc(alt)}" loading="lazy">{cap}\n    </figure>\n'
        text = m.text
        if m.html:
            from telegram_send import visible

            text = visible(text)
        cls = "text fail" if "실패" in text else "text"
        return f'    <div class="{cls}">{esc(text)}</div>\n'


def section(no: int | str, title: str, body: str) -> str:
    """no 가 정수면 제목 앞에 메시지 번호를 붙인다(대시보드처럼 번호 없는 구역은 문자열 id)."""
    num = f'<span class="no">{no}</span>' if isinstance(no, int) else ""
    return f'  <section id="m{no}">\n    <h2>{num}{esc(title)}</h2>\n{body}  </section>\n\n'


def build_page(date_key: str, date_label: str, stamp: str, msgs: dict[int, list[Message]],
               chart_msgs: list[tuple[str, Message]], blogs, failed_blogs, summaries: dict[str, str],
               chart_dir: Path) -> tuple[str, set]:
    page = Page(date_key, chart_dir)
    parts = [HEAD.format(title=f"Owl Capital 아침 브리핑 · {date_label}", style=STYLE, noindex=NOINDEX),
             f'  <header>\n    <h1>🦉 Owl Capital 아침 브리핑</h1>\n'
             f'    <div class="date">{esc(date_label)} · {esc(stamp)} 생성</div>\n  </header>\n\n']

    # 맨 앞: 아침 대시보드, 섹터 전체표(텔레그램과 같은 순서)
    for i, (label, slug) in enumerate((("대시보드", "dashboard"), ("섹터 전체표", "sectors"))):
        ms = [m for m in msgs.get(0, []) if m.label.startswith(label)]  # 낱장 여러 장("대시보드 1", "대시보드 2")
        if not ms and i == 0 and msgs.get(0):
            ms = msgs[0][:1]  # 대시보드 전체 실패 알림
        if ms:
            title = "아침 대시보드" if slug == "dashboard" else "섹터 전체표"
            parts.append(section(slug, title, "".join(page.message(m, f"{slug}-{k}", title)
                                                      for k, m in enumerate(ms, 1))))

    # 1 Fear & Greed
    body = "".join(page.message(m, f"fg-{i + 1}", "Fear & Greed 지수") for i, m in enumerate(msgs[1]))
    parts.append(section(1, "Fear & Greed", body))

    # 2 블로그
    total = sum(len(p) for _, _, p in blogs)
    body = f'    <div class="text">{"블로그 새 글 " + str(total) + "건" if total else "블로그 새 글 없음"}</div>\n'
    for name, more, posts in blogs:
        body += f'    <div class="blog">{esc(name)} ({len(posts)}건)</div>\n    <ul>\n'
        for _, title, url in posts:
            summary = summaries.get(url)
            head = f'<a href="{esc(url)}">{esc(title)}</a>' if url else esc(title)
            body += f"      <li>{head}" + (f'<br>\n        <span class="who">{summary}</span>' if summary else "") \
                    + "</li>\n"
        body += "    </ul>\n"
    if failed_blogs:
        body += f'    <div class="text fail">확인 실패: {esc(", ".join(failed_blogs))}</div>\n'
    parts.append(section(2, "블로그 새 글 (최근 24시간)", body))

    # 3 히트맵
    body = "".join(page.message(m, f"heatmap-{i + 1}", f"핀비즈 S&P 500 히트맵 {m.label}")
                   for i, m in enumerate(msgs[3]))
    parts.append(section(3, "핀비즈 S&P 500 히트맵", body))

    # 4 천연가스
    body = "".join(page.message(m, "natgas", "헨리허브 천연가스 선물 차트") for m in msgs[4])
    parts.append(section(4, "미 천연가스", body))

    # 5 차트 6장
    body = ""
    for i, (name, m) in enumerate(chart_msgs, 1):
        body += f"    <h3>{esc(name)}</h3>\n" + page.message(m, f"chart-{i}", name)
    parts.append(section(5, "차트", body))

    # 6 메모리
    body = "".join(page.message(m, f"memory-{i + 1}", m.label) for i, m in enumerate(msgs[6]))
    parts.append(section(6, "메모리", body))

    parts.append(FOOT.format(footer='<a href="archive.html">지난 브리핑 보기</a>'))
    return "".join(parts), page.used


def ensure_noindex(path: Path) -> bool:
    """HTML <head> 에 검색 제외 표시가 없으면 넣는다. 넣었으면 True."""
    text = path.read_text(encoding="utf-8")
    if 'name="robots"' in text:
        return False
    head = re.search(r'<meta charset="[^"]*">\n', text) or re.search(r"<head>\n", text)
    if not head:
        raise ValueError(f"{path.name}: <head> 를 찾지 못함")
    path.write_text(text[:head.end()] + NOINDEX + "\n" + text[head.end():], encoding="utf-8")
    return True


def missing_noindex(root: Path = DOCS) -> list[str]:
    return [str(p.relative_to(ROOT)) for p in sorted(root.rglob("*.html"))
            if NOINDEX not in p.read_text(encoding="utf-8")]


def update_archive(date_key: str, docs: Path = DOCS) -> None:
    path = docs / "archive.html"
    text = path.read_text(encoding="utf-8")
    if f'href="{date_key}.html"' in text:
        return
    item = f'      <li><a href="{date_key}.html">{date_key}</a></li>\n'
    text = text.replace("    <ul>\n", "    <ul>\n" + item, 1)  # 최신 날짜를 맨 위에
    path.write_text(text, encoding="utf-8")


def remove_stale_images(date_key: str, used: set[str], chart_dir: Path = DOCS / "charts") -> None:
    """같은 날짜로 예전에 만든 이미지 중 이번 페이지가 쓰지 않는 것은 지운다."""
    for f in chart_dir.glob(f"{date_key}-*.png"):
        if f.name not in used:
            f.unlink()


# ---------------------------------------------------------------------------
# 비밀값 검사
# ---------------------------------------------------------------------------


def secret_values() -> list[tuple[str, str]]:
    return [(k, v) for k, v in os.environ.items() if SECRET_ENV.search(k) and len(v.strip()) >= 6]


def check_secrets(root: Path = DOCS) -> list[str]:
    """docs/ 모든 파일에서 비밀값을 찾는다. 찾은 것은 (값 없이) '파일: 무엇' 으로만 알린다."""
    found = []
    values = secret_values()
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        data = f.read_bytes()
        rel = f.relative_to(ROOT)
        for name, value in values:
            if value.strip().encode() in data:
                found.append(f"{rel}: 환경변수 {name} 값")
        if f.suffix in (".html", ".css", ".js", ".json", ".txt", ".md"):
            text = data.decode("utf-8", errors="replace")
            for pat, what in SECRET_PATTERNS:
                if re.search(pat, text):
                    found.append(f"{rel}: {what}")
    return found


def report_secrets(root: Path = DOCS) -> int:
    found = check_secrets(root)
    n = sum(1 for p in root.rglob("*") if p.is_file())
    print(f"[검사] {root.relative_to(ROOT)}/ 파일 {n}개, 환경변수 비밀값 {len(secret_values())}종·토큰 형식 {len(SECRET_PATTERNS)}종 대조")
    if found:
        print("[검사] 비밀값 발견:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    print("[검사] 비밀값 없음")
    miss = missing_noindex(root)
    if miss:
        print("[검사] 검색 제외 표시(noindex) 없음: " + ", ".join(miss), file=sys.stderr)
        return 1
    print("[검사] 모든 HTML에 검색 제외 표시(noindex) 있음")
    return 0


# ---------------------------------------------------------------------------


def build_site(msgs: dict[int, list[Message]], chart_msgs: list[tuple[str, Message]], blogs, failed_blogs,
               docs: Path = DOCS) -> Path:
    """docs(또는 미리보기 폴더)에 오늘 페이지·index.html·archive.html 을 쓴다. 페이지 경로를 돌려준다."""
    now = now_kst()
    date_key = now.date().isoformat()
    date_label = f"{now.year}년 {now.month}월 {now.day}일 ({WEEKDAYS[now.weekday()]})"
    docs.mkdir(parents=True, exist_ok=True)
    if docs != DOCS and not (docs / "archive.html").exists() and (DOCS / "archive.html").exists():
        shutil.copyfile(DOCS / "archive.html", docs / "archive.html")
    page_path = docs / f"{date_key}.html"
    summaries = old_summaries(DOCS / f"{date_key}.html")
    text, used = build_page(date_key, date_label, f"{now:%H:%M} KST", msgs, chart_msgs, blogs, failed_blogs,
                            summaries, docs / "charts")
    page_path.write_text(text, encoding="utf-8")
    (docs / "index.html").write_text(text, encoding="utf-8")
    if (docs / "archive.html").exists():
        update_archive(date_key, docs)
    remove_stale_images(date_key, used, docs / "charts")
    for f in docs.glob("*.html"):
        ensure_noindex(f)
    print(f"[사이트] {page_path.relative_to(ROOT)}, index.html 갱신, 이미지 {len(used)}장")
    return page_path


def build_from_briefing(prepared: dict[int, Prepared], dry_run: bool) -> str:
    """briefing.py 전송이 끝난 뒤 호출: 전송 때 만든 이미지·캡션으로 사이트를 만든다.

    dry-run 은 out/site/ 에 미리보기만 만들고, 아니면 docs/ 에 만들어 main 에 커밋·푸시한다.
    실패하면 예외(briefing.py 가 "사이트" 실패 항목으로 표시). 성공하면 요약 문자열.
    """
    msgs = {no: p.messages for no, p in prepared.items()}
    blog = prepared.get(2)
    blogs = blog.data.get("blogs", []) if blog else []
    failed_blogs = blog.data.get("failed", []) if blog else []
    manifest = OUT_DIR / "charts.json"
    chart_msgs = charts_from_manifest(manifest) if manifest.exists() else \
        [("차트", Message("text", "차트 브리핑 이미지 없음", label="차트"))]
    docs = PREVIEW if dry_run else DOCS
    page = build_site(msgs, chart_msgs, blogs, failed_blogs, docs)
    if report_secrets(docs) != 0:
        raise RuntimeError("비밀값·noindex 검사 실패(커밋하지 않음)")
    if dry_run:
        return f"미리보기 {page.relative_to(ROOT)}"
    return publish(page.stem)


def git(*args: str, timeout: int = 120):
    import subprocess

    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)


def publish(date_key: str) -> str:
    """docs/ 를 커밋하고 main(원격 기본 브랜치)에 푸시한다. 앞서가 있으면 main 을 합친 뒤 다시 푸시."""
    import data_merge

    git("add", "docs")
    if git("diff", "--cached", "--quiet", "--", "docs").returncode == 0:
        return "docs/ 변경 없음"
    ident = []
    if not git("config", "user.email").stdout.strip():
        ident = ["-c", "user.name=Owl Capital Briefing", "-c", "user.email=briefing@users.noreply.github.com"]
    c = git(*ident, "commit", "-m", f"briefing {date_key}", "--", "docs")
    if c.returncode != 0:
        raise RuntimeError(f"커밋 실패: {c.stderr.strip()[-200:]}")
    target = data_merge.default_branch()
    p = git("push", "origin", f"HEAD:refs/heads/{target}")
    if p.returncode != 0:
        # 그 사이 main 이 앞서갔으면 합치고(merge) 한 번 더
        git("fetch", "origin", target)
        m = git(*ident, "merge", "--no-edit", f"origin/{target}")
        if m.returncode != 0:
            git("merge", "--abort")
            raise RuntimeError(f"{target} 합치기 실패: {m.stdout.strip()[-200:]}")
        p = git("push", "origin", f"HEAD:refs/heads/{target}")
        if p.returncode != 0:
            raise RuntimeError(f"{target} 푸시 실패: {p.stderr.strip()[-200:]}")
    return f"docs/ 커밋을 {target} 에 푸시"


def main() -> int:
    ap = argparse.ArgumentParser(description="브리핑 사이트(docs/) 생성, 텔레그램 전송 없음")
    ap.add_argument("--check", action="store_true", help="비밀값 검사만")
    args = ap.parse_args()
    if args.check:
        return report_secrets()

    setup_korean_font()
    with ThreadPoolExecutor(max_workers=1) as ex:  # 블로그는 다른 준비와 동시에
        blog_future = ex.submit(collect_blogs)
        msgs = collect_messages()
    chart_msgs = collect_charts()
    blogs, failed_blogs = blog_future.result()
    build_site(msgs, chart_msgs, blogs, failed_blogs)
    return report_secrets()


if __name__ == "__main__":
    sys.exit(main())
