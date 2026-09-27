#!/usr/bin/env bash
# Pretendard 글꼴 설치(대시보드·섹터 전체표 그림용). 이미 있으면 아무것도 하지 않는다.
# 환경 설정 스크립트에 한 줄 추가: bash setup_fonts.sh
#   1순위 GitHub 릴리스 zip, 실패하면 npm 레지스트리 tarball.
#   root 면 /usr/share/fonts/opentype/pretendard, 아니면 ~/.local/share/fonts/pretendard 에 둔다.
set -euo pipefail

VERSION="1.3.9"
WEIGHTS="Regular Medium SemiBold Bold"
if [ "$(id -u)" = "0" ]; then DEST=/usr/share/fonts/opentype/pretendard; else DEST="$HOME/.local/share/fonts/pretendard"; fi

have_all() {
  for w in $WEIGHTS; do [ -f "$DEST/Pretendard-$w.otf" ] || return 1; done
}
if have_all; then echo "[fonts] Pretendard 이미 설치됨 ($DEST)"; exit 0; fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$DEST"

if curl -fsSL --retry 2 -o "$TMP/p.zip" \
     "https://github.com/orioncactus/pretendard/releases/download/v$VERSION/Pretendard-$VERSION.zip" \
   && python3 -c "import zipfile,sys; z=zipfile.ZipFile(sys.argv[1]); [open(sys.argv[2]+'/'+n.rsplit('/',1)[1],'wb').write(z.read(n)) for n in z.namelist() if n.startswith('public/static/Pretendard-') and n.endswith('.otf')]" "$TMP/p.zip" "$TMP"; then
  SRC=GitHub
elif curl -fsSL --retry 2 -o "$TMP/p.tgz" "https://registry.npmjs.org/pretendard/-/pretendard-$VERSION.tgz" \
   && tar -xzf "$TMP/p.tgz" -C "$TMP" && cp "$TMP"/package/dist/public/static/Pretendard-*.otf "$TMP"/; then
  SRC=npm
else
  echo "[fonts] Pretendard 받기 실패" >&2
  exit 1
fi
for w in $WEIGHTS; do cp "$TMP/Pretendard-$w.otf" "$DEST/"; done
command -v fc-cache >/dev/null && fc-cache -f "$DEST" >/dev/null 2>&1 || true
echo "[fonts] Pretendard $VERSION 설치 ($SRC → $DEST)"
