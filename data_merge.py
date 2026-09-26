"""data/ CSV를 여러 git 브랜치에서 모아 날짜 기준으로 합친다.

예약 실행은 세션 브랜치(claude/*)에만 푸시할 수 있어 data/ 누적분이 브랜치마다 흩어진다.
실행할 때마다 원격 모든 브랜치의 data/ 를 현재 작업 폴더로 합쳐 과거 값을 잃지 않게 한다.

합치는 규칙
- date 로 시작하는 CSV(date,value 등)는 날짜별 합집합. 같은 날짜가 여러 곳에 있으면
  먼저 넣은 쪽(현재 작업 폴더 → 기본 브랜치 → 최근 커밋 순 브랜치)의 줄을 그대로 쓴다.
- 줄은 원문 그대로 옮기고 날짜순으로 정렬한다(바뀐 게 없으면 파일도 그대로).
- 그 밖의 파일은 작업 폴더에 없을 때만 가져온다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FETCH_TIMEOUT = 90


def git(*args: str, cwd: Path = ROOT, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)


def fetch_all(cwd: Path = ROOT) -> str | None:
    """원격 모든 브랜치를 받는다(없어진 브랜치는 정리). 실패하면 사유 문자열."""
    try:
        p = git("fetch", "--prune", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*",
                cwd=cwd, timeout=FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"git fetch {FETCH_TIMEOUT}초 시간 초과"
    return None if p.returncode == 0 else (p.stderr.strip().splitlines() or ["git fetch 실패"])[-1]


def default_branch(cwd: Path = ROOT) -> str:
    head = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=cwd).stdout.strip()
    if "/" in head:
        return head.split("/", 1)[1]
    names = remote_branches(cwd)
    return "main" if "main" in names else ("master" if "master" in names else (names[0] if names else "main"))


def remote_branches(cwd: Path = ROOT) -> list[str]:
    """origin 브랜치 이름, 최근 커밋 순."""
    out = git("for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/remotes/origin",
              cwd=cwd).stdout.split()
    return [r.split("/", 1)[1] for r in out if "/" in r and not r.endswith("/HEAD") and r != "origin"]


def ordered_refs(cwd: Path = ROOT) -> list[str]:
    """합칠 순서: 기본 브랜치 먼저, 나머지는 최근 커밋 순."""
    base = default_branch(cwd)
    names = remote_branches(cwd)
    return [f"origin/{b}" for b in ([base] if base in names else []) + [b for b in names if b != base]]


def ref_files(ref: str, cwd: Path = ROOT) -> dict[str, str]:
    """ref 의 data/ 아래 파일 {상대경로: 내용}."""
    p = git("ls-tree", "-r", "--name-only", ref, "--", "data", cwd=cwd)
    files = {}
    for name in p.stdout.split("\n"):
        if name.strip():
            c = git("show", f"{ref}:{name}", cwd=cwd)
            if c.returncode == 0:
                files[name] = c.stdout
    return files


def _rows(text: str) -> tuple[str, dict[str, str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "", {}
    return lines[0], {ln.split(",", 1)[0]: ln for ln in lines[1:]}


def is_dated_csv(name: str, text: str) -> bool:
    return name.endswith(".csv") and text.lstrip().lower().startswith("date,")


def merge_texts(texts: list[str]) -> str:
    """같은 형식의 CSV 여러 개를 날짜 기준 합집합으로. 앞쪽 텍스트가 우선."""
    header, rows = "", {}
    for t in texts:
        h, r = _rows(t)
        header = header or h
        for d, ln in r.items():
            rows.setdefault(d, ln)
    return "\n".join([header] + [rows[d] for d in sorted(rows)]) + "\n"


def row_count(text: str) -> int:
    return len(_rows(text)[1])


def merge_into(work: Path, refs: list[str], cwd: Path = ROOT) -> dict[str, int]:
    """refs 의 data/ 를 work/data 로 합친다. {파일: 새로 들어온 행 수(새 파일은 전체 행 수)}."""
    sources = [ref_files(r, cwd) for r in refs]
    names = sorted({n for s in sources for n in s})
    changed: dict[str, int] = {}
    for name in names:
        path = work / name
        local = path.read_text(encoding="utf-8") if path.exists() else None
        others = [s[name] for s in sources if name in s]
        if local is None:
            merged = merge_texts(others) if is_dated_csv(name, others[0]) else others[0]
        elif is_dated_csv(name, local) and all(is_dated_csv(name, t) for t in others):
            merged = merge_texts([local] + others)
        else:
            continue
        if local is not None and _rows(merged)[1].keys() == _rows(local)[1].keys():
            continue  # 새 날짜 없음(작업 폴더 줄이 우선이므로 파일을 건드리지 않는다)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(merged, encoding="utf-8")
        changed[name] = row_count(merged) - (row_count(local) if local is not None else 0)
    return changed


def missing_rows(ref: str, work: Path, cwd: Path = ROOT) -> dict[str, int]:
    """ref 의 data/ 행 중 work/data 에 없는 것 {파일: 행 수}. 브랜치를 지워도 되는지 확인할 때 쓴다."""
    missing = {}
    for name, text in ref_files(ref, cwd).items():
        path = work / name
        if not path.exists():
            missing[name] = max(row_count(text), 1)
            continue
        have = _rows(path.read_text(encoding="utf-8"))[1]
        if is_dated_csv(name, text):
            n = sum(1 for d in _rows(text)[1] if d not in have)
        else:
            n = 0 if path.read_text(encoding="utf-8") == text else 1
        if n:
            missing[name] = n
    return missing


def merge_remote_data(fetch: bool = True) -> dict[str, int]:
    """원격 모든 브랜치의 data/ 를 현재 작업 폴더 data/ 로 합친다. 실패해도 예외 없이 경고만."""
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return {}
    try:
        if fetch:
            err = fetch_all()
            if err:
                print(f"[경고] data 합치기: git fetch 실패({err}), 받아 둔 브랜치로 진행", file=sys.stderr)
        changed = merge_into(ROOT, ordered_refs())
    except Exception as e:  # noqa: BLE001
        print(f"[경고] data 합치기 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return {}
    if changed:
        print("[data] 다른 브랜치에서 합침: " + ", ".join(f"{Path(k).name} +{v}" for k, v in changed.items()))
    return changed


if __name__ == "__main__":
    merge_remote_data()
