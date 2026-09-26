"""Owl Capital 아침 브리핑 전체 실행.

0) 원격 모든 브랜치의 data/ 를 합친다(data_merge.py, 좌수 등 직전 값 비교용)
1) 메시지 1~4, 6 데이터 수집·이미지 생성을 병렬로 끝내고
2) 1 → 2 → 3 → 4 순서로 텔레그램 전송
3) 메시지 5: python charts.py (차트 6장 전송 + data/ 커밋·푸시, 메시지 6 저장분 포함)
4) 메시지 6: 메모리(DRAM ETF·7709·DRAM 현물가) 텍스트 전송
5) 메시지별 전송 결과를 한 줄씩 출력하고, 실패 항목이 있으면 종료 코드 1

옵션
  --dry-run    전송 없이 out/에 이미지만 만들고 캡션을 출력 (charts.py도 --dry-run --no-commit)
  --skip-charts  메시지 5 생략(dry-run이 아니면 data/ 커밋·푸시는 따로 한다)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import blog_feed
import data_merge
import fear_greed
import finviz_heatmap
import memory
import natgas
from briefing_common import ROOT, now_kst, prepare_safely, send_messages, setup_korean_font
from telegram_send import TelegramError, check, enabled

ITEMS = [(1, fear_greed), (2, blog_feed), (3, finviz_heatmap), (4, natgas)]  # 차트(5) 앞에 보내는 항목
AFTER_CHARTS = [(6, memory)]  # 차트 뒤에 보내는 항목
CHARTS_TIMEOUT = 900


def run_charts(dry_run: bool) -> tuple[str, list[str]]:
    """charts.py를 실행해 (요약, 전송 로그 줄) 을 돌려준다. 출력은 그대로 흘려보낸다."""
    cmd = [sys.executable, str(ROOT / "charts.py")] + (["--dry-run", "--no-commit"] if dry_run else [])
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=CHARTS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"실패: {CHARTS_TIMEOUT}초 시간 초과", []
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    lines = p.stdout.splitlines() + p.stderr.splitlines()
    ok = [ln for ln in lines if ln.startswith("[OK]")]
    bad = [ln for ln in lines if ln.startswith("[실패]")]
    if p.returncode != 0 and not ok:
        return f"실패: 종료 코드 {p.returncode} " + (bad[0] if bad else (lines[-1] if lines else "")), bad
    summary = f"{len(ok)}장 전송" + (f", {len(bad)}건 실패" if bad else "")
    return summary, bad


def main() -> int:
    ap = argparse.ArgumentParser(description="Owl Capital 아침 브리핑")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-charts", action="store_true")
    args = ap.parse_args()
    dry_run = args.dry_run or not enabled()
    if dry_run and not args.dry_run:
        print("[경고] TELEGRAM_BOT_TOKEN 이 없어 dry-run으로 실행합니다.", file=sys.stderr)

    print(f"[briefing] 시작 {now_kst():%Y-%m-%d %H:%M} KST")
    if not dry_run:
        try:
            print(f"[briefing] 텔레그램 확인: ok ({check()})")
        except TelegramError as e:
            print(f"[briefing] 텔레그램 확인 실패: {e}", file=sys.stderr)

    data_merge.merge_remote_data()
    setup_korean_font()
    items = ITEMS + AFTER_CHARTS
    with ThreadPoolExecutor(max_workers=len(items)) as ex:
        futures = {no: ex.submit(prepare_safely, mod) for no, mod in items}
    prepared = {no: f.result() for no, f in futures.items()}

    log: list[str] = []
    failed: list[str] = []

    def send(no, mod):
        p = prepared[no]
        for err in p.errors:
            print(f"[{mod.NAME}] {err}", file=sys.stderr)
        results = send_messages(p.messages, dry_run)
        if p.errors or any(r.startswith("실패") for _, r in results):
            failed.append(f"메시지 {no} {mod.NAME}: " + "; ".join(p.errors + [r for _, r in results
                                                                              if r.startswith("실패")]))
        for m, r in results:
            log.append(f"메시지 {no} {mod.NAME} / {m.label}: {r}")

    for no, mod in ITEMS:
        send(no, mod)

    if args.skip_charts:
        log.append("메시지 5 차트 브리핑: 생략")
        if not dry_run:
            import charts

            charts.commit_data()
    else:
        summary, bad = run_charts(dry_run)
        log.append(f"메시지 5 차트 브리핑: {summary}")
        if summary.startswith("실패") or bad:
            failed.append(f"메시지 5 차트 브리핑: {summary}" + ("".join(f"\n  {b}" for b in bad)))

    for no, mod in AFTER_CHARTS:
        send(no, mod)

    print("\n[briefing] 전송 결과")
    for line in log:
        print(f"  {line}")
    print("[briefing] 실패 항목: " + ("없음" if not failed else ""))
    for f in failed:
        print(f"  {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
