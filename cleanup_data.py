"""data/ 정리: 모든 브랜치의 data/ 를 합쳐 기본 브랜치에 커밋하고, 필요 없어진 claude/* 브랜치를 지운다.

대화형 세션에서 실행한다.
  python cleanup_data.py             합치기 → 기본 브랜치에 커밋·푸시 → 지울 브랜치 목록 → 확인(y) 후 삭제
  python cleanup_data.py --dry-run   합친 결과와 지울 브랜치 목록만 보여 준다(커밋·푸시·삭제 없음)
  python cleanup_data.py --no-delete 합치기·커밋·푸시까지만

작업 폴더는 건드리지 않는다. 기본 브랜치를 임시 worktree 로 꺼내 그 안에서 합치고 커밋한다.

지우는 브랜치(모두 만족할 때만)
- 이름이 claude/ 로 시작하고, 지금 체크아웃한 브랜치가 아니다(main·master 는 어떤 경우에도 지우지 않는다)
- 기본 브랜치와 갈라진 뒤 바뀐 파일이 data/ 뿐이다(코드·문서 변경이 있으면 남긴다)
- 그 브랜치의 data/ 행이 합친 기본 브랜치에 모두 들어 있다
- 예전 기본 브랜치(FORMER_DEFAULTS)는 더 엄격하게: 그 브랜치의 모든 커밋이 원격 main 이력에 들어 있고
  data/ 행도 (합치기 전) 원격 main 에 모두 있을 때만
기본 브랜치 푸시가 실패하면 아무것도 지우지 않는다. 삭제는 y 를 입력해야만 한다.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from data_merge import (ROOT, default_branch, fetch_all, git, merge_into, missing_rows, ordered_refs,
                        remote_branches)

PREFIX = "claude/"
PROTECTED = {"main", "master"}  # 절대 지우지 않는다
FORMER_DEFAULTS = {"claude/bold-lamport-70e83u"}  # main 전에 기본 브랜치로 쓰던 브랜치


def git_ok(*args: str, cwd: Path = ROOT, timeout: int = 120) -> str:
    p = git(*args, cwd=cwd, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} 실패: {p.stderr.strip()[-300:]}")
    return p.stdout


def ask(prompt: str) -> bool:
    if not sys.stdin.isatty():
        print(f"{prompt} → 대화형 입력이 아니므로 '아니오'로 처리합니다.")
        return False
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes", "예", "ㅛ")
    except EOFError:
        return False


def former_default_gap(ref: str, base: str, pristine: Path) -> str:
    """예전 기본 브랜치가 원격 main 에 다 들어갔는지. 들어갔으면 '', 아니면 이유."""
    if git("merge-base", "--is-ancestor", ref, f"origin/{base}").returncode != 0:
        ahead = git_ok("rev-list", "--count", f"origin/{base}..{ref}").strip()
        return f"{base}에 없는 커밋 {ahead}개"
    miss = missing_rows(ref, pristine)
    if miss:
        return f"{base}에 아직 없는 data 행: " + ", ".join(f"{Path(k).name} {v}행" for k, v in miss.items())
    return ""


def classify(branches: list[str], base: str, work: Path, current: str,
             pristine: Path | None = None) -> tuple[list[str], list[tuple[str, str]]]:
    """(지울 브랜치, [(남길 브랜치, 이유)]). pristine 은 합치기 전 원격 기본 브랜치 작업 폴더."""
    pristine = pristine or work
    delete, keep = [], []
    for b in branches:
        ref = f"origin/{b}"
        if b == base or b in PROTECTED:
            if b != base:
                keep.append((b, "보호 브랜치"))
            continue
        if not b.startswith(PREFIX):
            keep.append((b, f"{PREFIX} 브랜치가 아님"))
            continue
        if b == current:
            keep.append((b, "지금 체크아웃한 브랜치"))
            continue
        if git("merge-base", f"origin/{base}", ref).returncode != 0:
            keep.append((b, f"{base}와 공통 이력이 없음"))
            continue
        changed = [f for f in git_ok("diff", "--name-only", f"origin/{base}...{ref}").split("\n") if f.strip()]
        other = [f for f in changed if not f.startswith("data/")]
        if other:
            more = f" 외 {len(other) - 3}개" if len(other) > 3 else ""
            keep.append((b, "data/ 외 변경: " + ", ".join(other[:3]) + more))
            continue
        miss = missing_rows(ref, work)
        if miss:
            keep.append((b, "합치지 못한 data 행: " + ", ".join(f"{Path(k).name} {v}행" for k, v in miss.items())))
            continue
        if b in FORMER_DEFAULTS:
            why = former_default_gap(ref, base, pristine)
            if why:
                keep.append((b, f"예전 기본 브랜치, {why}"))
                continue
        delete.append(b)
    return delete, keep


def main() -> int:
    ap = argparse.ArgumentParser(description="모든 브랜치의 data/ 를 기본 브랜치로 합치고 claude/* 브랜치 정리")
    ap.add_argument("--dry-run", action="store_true", help="커밋·푸시·삭제 없이 결과만 보기")
    ap.add_argument("--no-delete", action="store_true", help="브랜치 삭제 단계 생략")
    args = ap.parse_args()

    err = fetch_all()
    if err:
        print(f"[실패] git fetch: {err}", file=sys.stderr)
        return 1
    base = default_branch()
    current = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    refs = ordered_refs()
    print(f"[정리] 기본 브랜치: {base}, 원격 브랜치 {len(refs)}개")

    if base in PROTECTED and base != "main":
        print(f"[경고] 기본 브랜치가 {base}입니다(main 이 아님).", file=sys.stderr)
    tmp = Path(tempfile.mkdtemp(prefix="owl-data-"))
    work, pristine = tmp / "wt", tmp / "base"
    try:
        git_ok("worktree", "add", "--detach", str(work), f"origin/{base}")
        git_ok("worktree", "add", "--detach", str(pristine), f"origin/{base}")
        changed = merge_into(work, refs)
        if changed:
            print("[정리] 합친 결과(새로 들어온 행):")
            for name, n in changed.items():
                print(f"  {name}: +{n}")
        else:
            print(f"[정리] {base}에 이미 모든 data 행이 있음")

        if changed and not args.dry_run:
            git_ok("add", "data", cwd=work)
            ident = []
            if not git("config", "user.email", cwd=work).stdout.strip():
                ident = ["-c", "user.name=Owl Capital Briefing", "-c", "user.email=briefing@users.noreply.github.com"]
            git_ok(*ident, "commit", "-m", f"data: 모든 브랜치의 data/ 합치기 ({len(changed)}개 파일)", cwd=work)
            p = git("push", "origin", f"HEAD:refs/heads/{base}", cwd=work, timeout=180)
            if p.returncode != 0:
                print(f"[실패] {base} 푸시 실패, 브랜치는 지우지 않습니다: {p.stderr.strip()[-300:]}", file=sys.stderr)
                return 1
            print(f"[정리] {base}에 커밋·푸시 완료: {git_ok('rev-parse', '--short', 'HEAD', cwd=work).strip()}")
            fetch_all()
            git_ok("checkout", "--detach", f"origin/{base}", cwd=pristine)

        delete, keep = classify(remote_branches(), base, work, current, pristine)
        delete = [b for b in delete if b not in PROTECTED and b != base]  # 마지막 안전장치
        print(f"\n[정리] 남길 브랜치 {len(keep)}개")
        for b, why in keep:
            print(f"  {b}  ({why})")
        print(f"\n[정리] 지울 브랜치 {len(delete)}개 (data/ 만 바뀌었고 모든 행이 {base}에 있음)")
        for b in delete:
            print(f"  {b}")
        if not delete or args.no_delete:
            return 0
        if args.dry_run:
            print("[정리] --dry-run: 삭제하지 않음")
            return 0
        if not ask(f"\n위 {len(delete)}개 원격 브랜치를 삭제할까요?"):
            print("[정리] 삭제하지 않음")
            return 0
        failed = []
        for b in delete:
            p = git("push", "origin", "--delete", b, timeout=120)
            if p.returncode == 0:
                print(f"  삭제: {b}")
            else:
                failed.append(b)
                print(f"  실패: {b} ({p.stderr.strip().splitlines()[-1] if p.stderr.strip() else '사유 없음'})",
                      file=sys.stderr)
        return 1 if failed else 0
    finally:
        git("worktree", "remove", "--force", str(work))
        git("worktree", "remove", "--force", str(pristine))
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
