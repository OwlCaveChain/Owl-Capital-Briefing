"""Owl Capital 아침 브리핑 전체 실행.

0) 원격 모든 브랜치의 data/ 를 합친다(data_merge.py, 좌수 등 직전 값 비교용)
1) 대시보드(메시지 0)·메시지 1~4, 6 데이터 수집·이미지 생성을 병렬로 끝내고
2) 대시보드 → 섹터 전체표 → (월요일) 주간판 → 1 → 2 → 3 → 4 순서로 텔레그램 전송
3) 메시지 5: python charts.py (차트 6장 전송 + data/ 커밋·푸시, 대시보드·메시지 6 저장분 포함)
4) 메시지 6: 메모리(DRAM ETF·7709·7747·DRAM 현물가) 전송(좌수 20거래일 이상 쌓인 항목은 차트)
5) 사이트: 전송 때 만든 이미지로 docs/ 를 만들고 main 에 커밋·푸시(site_build.py).
   실패해도 텔레그램 전송에는 영향이 없고, 실패 항목에 "사이트"로 남는다
6) 메시지별 전송 결과를 한 줄씩 출력하고, 실패 항목이 있으면 종료 코드 1

옵션
  --dry-run    전송 없이 out/에 이미지만 만들고 캡션을 출력 (charts.py도 --dry-run --no-commit,
               사이트는 docs/ 대신 out/site/ 에 미리보기만)
  --skip-charts  메시지 5 생략(dry-run이 아니면 data/ 커밋·푸시는 따로 한다)
  --skip-site    사이트 생성·커밋 생략
  --layout monday|weekday  요일과 관계없이 월요일 주간판을 보낸다/안 보낸다(확인용)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---------------------------------------------------------------------------
# 실행 환경 점검(표준 라이브러리만 사용, 다른 모듈을 가져오기 전에)
# 'python' 이 의존 패키지를 못 불러오는 인터프리터를 가리키면, 불러올 수 있는 다른 python3 로 다시 실행한다.
# 그런 인터프리터가 없으면 지금 인터프리터에 requirements.txt 를 설치한다.
# 폴더만 있고 불러오지 못하는 패키지(다른 파이썬 버전용으로 설치된 Pillow 등)를 잡으려고
# 설치 여부가 아니라 실제 import 를 별도 프로세스에서 해 본다.
# ---------------------------------------------------------------------------

# 모듈 이름: (실제로 해 볼 import 문, pip 패키지 이름)
REQUIRED = {
    "requests": ("import requests", "requests"),
    "pandas": ("import pandas", "pandas"),
    "PIL": ("from PIL import Image, ImageDraw, ImageFont", "pillow"),
    "matplotlib": ("import matplotlib.pyplot", "matplotlib"),
    "yfinance": ("import yfinance", "yfinance"),
}
_CHECK = (
    "import os, sys\n"
    "os.environ.setdefault('MPLBACKEND', 'Agg')\n"
    "bad = []\n"
    "for name, stmt in %r:\n"
    "    try:\n"
    "        exec(stmt)\n"
    "    except Exception as e:\n"
    "        bad.append(name + '\\t' + type(e).__name__ + ': ' + str(e)[:150].replace('\\n', ' '))\n"
    "print('\\n'.join(bad))\n"
    "sys.exit(1 if bad else 0)\n"
) % ([(k, v[0]) for k, v in REQUIRED.items()],)


def _broken(python: str) -> dict[str, str] | None:
    """python 으로 의존 패키지를 실제로 불러와 본다. {못 불러온 모듈: 사유}. 실행 자체가 안 되면 None."""
    try:
        p = subprocess.run([python, "-c", _CHECK], capture_output=True, text=True, timeout=180)
    except Exception:  # noqa: BLE001
        return None
    if p.returncode == 0:
        return {}
    bad = dict(ln.split("\t", 1) for ln in p.stdout.splitlines() if "\t" in ln)
    return bad or None


def _pip(args: list[str]) -> bool:
    """pip install 을 시스템 보호 설정에 맞춰 차례로 시도한다."""
    base = [sys.executable, "-m", "pip", "install", "-q", *args]
    for extra in ([], ["--break-system-packages"], ["--user", "--break-system-packages"]):
        try:
            if subprocess.run(base + extra, timeout=600).returncode == 0:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def _restart(python: str, **env: str) -> None:
    sys.stderr.flush()
    sys.stdout.flush()
    os.execve(python, [python, os.path.abspath(__file__), *sys.argv[1:]], {**os.environ, **env})


def ensure_dependencies() -> None:
    broken = _broken(sys.executable)
    if broken == {}:
        return
    me = os.path.realpath(sys.executable)
    detail = "; ".join(f"{m}({why})" for m, why in (broken or {"?": "점검 실행 실패"}).items())
    print(f"[경고] {sys.executable}(Python {sys.version.split()[0]})에서 못 불러옴: {detail}", file=sys.stderr)
    if os.environ.get("OWL_BRIEFING_REEXEC") != "1":
        import shutil

        names = ["python3"] + [f"python3.{v}" for v in range(14, 9, -1)]
        cands = [shutil.which(n) for n in names] + [f"/usr/bin/{n}" for n in names] + \
                [f"/usr/local/bin/{n}" for n in names]
        seen = {me}
        for c in cands:
            if not c or not os.path.exists(c) or os.path.realpath(c) in seen:
                continue
            seen.add(os.path.realpath(c))
            if _broken(c) == {}:
                print(f"[경고] 패키지를 모두 불러오는 {c} 로 다시 실행합니다", file=sys.stderr)
                _restart(c, OWL_BRIEFING_REEXEC="1")
    if os.environ.get("OWL_BRIEFING_INSTALLED") == "1":
        sys.exit(f"[briefing] 의존 패키지 설치 실패: {detail}. 브리핑을 보내지 못했습니다.")
    req = Path(__file__).resolve().parent / "requirements.txt"
    print(f"[경고] 쓸 수 있는 python3 를 찾지 못해 {sys.executable} 에 requirements.txt 설치", file=sys.stderr)
    _pip(["-r", str(req)])
    # 폴더는 있는데 못 불러오는 패키지는 pip 이 "이미 설치됨"으로 넘어가므로, 이 파이썬용으로 강제로 다시 받는다
    still = _broken(sys.executable)
    if still:
        pkgs = [REQUIRED[m][1] for m in still if m in REQUIRED]
        print(f"[경고] 설치 뒤에도 못 불러옴: {', '.join(still)} → {' '.join(pkgs)} 강제 재설치", file=sys.stderr)
        _pip(["--ignore-installed", "--no-deps", *pkgs])
    # 새로 생긴 site-packages 폴더는 재시작해야 sys.path 에 잡힌다
    _restart(sys.executable, OWL_BRIEFING_REEXEC="1", OWL_BRIEFING_INSTALLED="1")


ensure_dependencies()

import blog_feed  # noqa: E402
import dashboard  # noqa: E402
import data_merge  # noqa: E402
import fear_greed  # noqa: E402
import finviz_heatmap  # noqa: E402
import memory  # noqa: E402
import natgas  # noqa: E402
from briefing_common import OUT_DIR, ROOT, now_kst, prepare_safely, send_messages, setup_korean_font  # noqa: E402
from telegram_send import TelegramError, check, enabled  # noqa: E402

# 차트(5) 앞에 보내는 항목. 0 = 아침 대시보드·섹터 전체표(메시지 1 앞)
ITEMS = [(0, dashboard), (1, fear_greed), (2, blog_feed), (3, finviz_heatmap), (4, natgas)]
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
    ap.add_argument("--skip-site", action="store_true")
    ap.add_argument("--layout", choices=["monday", "weekday"])
    args = ap.parse_args()
    if args.layout:
        os.environ["DASHBOARD_LAYOUT"] = args.layout
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

    (OUT_DIR / "charts.json").unlink(missing_ok=True)  # 사이트가 지난 실행의 차트를 쓰지 않도록
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

    # 사이트: 텔레그램 전송이 모두 끝난 뒤. 어떤 실패도 위 전송 결과에 영향을 주지 않는다
    if args.skip_site:
        log.append("사이트: 생략")
    else:
        try:
            import site_build

            log.append(f"사이트: {site_build.build_from_briefing(prepared, dry_run)}")
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc(file=sys.stderr)
            reason = f"{type(e).__name__}: {e}"[:200]
            log.append(f"사이트: 실패: {reason}")
            failed.append(f"사이트: {reason}")

    print("\n[briefing] 전송 결과")
    for line in log:
        print(f"  {line}")
    print("[briefing] 실패 항목: " + ("없음" if not failed else ""))
    for f in failed:
        print(f"  {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
