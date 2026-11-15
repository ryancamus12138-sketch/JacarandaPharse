#!/usr/bin/env bash
# Build the release archive: release/JacarandaParse-<version>.zip and its SHA-256 checksum.
#
# The archive contains only what is needed to install and run the application: runtime source,
# database migrations, configuration templates, the prebuilt web frontend, the install script and
# the end-user README. Development material (tests, design records, changelog, CI configuration)
# is excluded. Model files are not bundled.
#
# Requires a clean working tree; only committed files are packaged.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(grep -m1 '^version' pyproject.toml | cut -d'"' -f2)
NAME="JacarandaParse-$VERSION"
OUT=release
STAGE="$OUT/$NAME"

# Committed files that make up the runtime distribution.
INCLUDE=(
  backend/src
  alembic.ini
  compose.yaml
  pyproject.toml
  uv.lock
  .python-version
  .env.example
  LICENSE
  scripts/install.sh
)

if [ -n "$(git status --porcelain)" ]; then
  echo "error: the working tree has uncommitted changes; commit them before building a release" >&2
  exit 1
fi

echo "Building web frontend..."
(cd web && npm ci && npm run build)

rm -rf "${OUT:?}/$NAME" "$OUT/$NAME.zip" "$OUT/$NAME.zip.sha256" "$OUT/NOTES.md"
mkdir -p "$OUT"
git archive --format=tar --prefix="$NAME/" HEAD -- "${INCLUDE[@]}" | tar -x -C "$OUT"
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +

cp packaging/README.md "$STAGE/README.md"
mkdir -p "$STAGE/web" "$STAGE/models" "$STAGE/data"
cp -R web/dist "$STAGE/web/dist"
sed 's#cd "$(dirname "$0")/.."#cd "$(dirname "$0")"#' scripts/JacarandaParse.command > "$STAGE/JacarandaParse.command"
chmod +x "$STAGE/scripts/install.sh" "$STAGE/JacarandaParse.command"

(cd "$OUT" && zip -qr "$NAME.zip" "$NAME" && shasum -a 256 "$NAME.zip" > "$NAME.zip.sha256")

if [ -f "packaging/notes/$VERSION.md" ]; then
  cp "packaging/notes/$VERSION.md" "$OUT/NOTES.md"
else
  echo "JacarandaParse $VERSION" > "$OUT/NOTES.md"
fi

echo "Created $OUT/$NAME.zip"
cat "$OUT/$NAME.zip.sha256"
