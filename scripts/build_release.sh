#!/usr/bin/env bash
# 打包个人版发布包（ADR 0064）：源码（git 里提交了的）+ 打包好的网页 → release/JacarandaParse-版本.zip 和它的 SHA-256。
# 维护者在本机运行，或者推送 v* 标签时由 .github/workflows/release.yml 运行。模型文件不打进去（太大，各有许可）。
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)
NAME="JacarandaParse-$VERSION"
OUT=release

if [ -n "$(git status --porcelain)" ]; then
  echo "有还没提交的改动：发布包只打 git 里提交了的文件，先提交再打包"
  exit 1
fi

echo "打包网页……"
(cd web && npm ci && npm run build)

rm -rf "${OUT:?}/$NAME" "$OUT/$NAME.zip" "$OUT/$NAME.zip.sha256"
mkdir -p "$OUT"
git archive --format=tar --prefix="$NAME/" HEAD | tar -x -C "$OUT"
cp -R web/dist "$OUT/$NAME/web/dist"
cp scripts/JacarandaParse.command "$OUT/$NAME/JacarandaParse.command"
sed -i.bak 's#cd "$(dirname "$0")/.."#cd "$(dirname "$0")"#' "$OUT/$NAME/JacarandaParse.command" && rm "$OUT/$NAME/JacarandaParse.command.bak"
chmod +x "$OUT/$NAME/scripts/"*.sh "$OUT/$NAME/JacarandaParse.command"

(cd "$OUT" && zip -qr "$NAME.zip" "$NAME" && shasum -a 256 "$NAME.zip" > "$NAME.zip.sha256")

# 发布说明：CHANGELOG 里这个版本的那一节
awk -v v="## [$VERSION]" 'index($0, v) == 1 {p = 1; next} p && /^## \[/ {exit} p' CHANGELOG.md > "$OUT/NOTES.md"

echo "好了：$OUT/$NAME.zip"
cat "$OUT/$NAME.zip.sha256"
