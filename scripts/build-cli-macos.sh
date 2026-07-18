#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 /path/to/dist/synology-apm-cli/synology_apm_cli-<version>-py3-none-any.whl"
  exit 1
fi

WHEEL="$1"
VENV=".venv-build"
ENTRY="freeze_entry.py"
DIST_DIR="dist/binaries/macos"

if [ ! -f "$WHEEL" ]; then
  echo "Wheel not found: $WHEEL"
  exit 1
fi

WHEEL_FILE="$(basename "$WHEEL")"
WHEEL_BASE="${WHEEL_FILE%.whl}"

IFS='-' read -r DIST_NAME VERSION REST <<< "$WHEEL_BASE"

if [ -z "${VERSION:-}" ]; then
  echo "Failed to parse version from wheel filename: $WHEEL_FILE"
  exit 1
fi

# synology-apm-cli depends on synology-apm-sdk; point pip at the sibling wheel directory so its
# resolver picks up the matching synology-apm-sdk==X.Y.Z wheel (expects the
# dist/synology-apm-sdk/ + dist/synology-apm-cli/ layout produced by `make build`).
SDK_WHEEL_DIR="$(dirname "$(dirname "$WHEEL")")/synology-apm-sdk"

ARCH="$(uname -m)"
APP_NAME="synology-apm-cli-${VERSION}-macos-${ARCH}"
APP_DIR="${DIST_DIR}/${APP_NAME}"
ZIP_PATH="${DIST_DIR}/${APP_NAME}.zip"
SPEC_FILE="${APP_NAME}.spec"

mkdir -p "$DIST_DIR"

cleanup() {
  rm -rf "$VENV"
  rm -f "$ENTRY"
  rm -f "$SPEC_FILE"
  rm -rf build
}
trap cleanup EXIT

python3 -m venv "$VENV"

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install pyinstaller
"$VENV/bin/python" -m pip install --find-links "$SDK_WHEEL_DIR" "${WHEEL}"

cat > "$ENTRY" <<'PY'
from synology_apm.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
PY

"$VENV/bin/pyinstaller" \
  --noconfirm \
  --onedir \
  --clean \
  --name "$APP_NAME" \
  --distpath "$DIST_DIR" \
  --collect-all synology_apm \
  "$ENTRY"

rm -f "$ZIP_PATH"
(
  cd "$DIST_DIR"
  zip -r "${APP_NAME}.zip" "${APP_NAME}"
)

rm -rf "$APP_DIR"

echo "Built: $ZIP_PATH"
