#!/bin/sh
set -eu

PREFIX="${HOME}/.symphonz"
SOURCE_DIR=""
REPO_URL="${SYMPHONZ_REPO_URL:-https://github.com/ilordhalo/zhangjiahao_agi}"
REF="${SYMPHONZ_REF:-main}"

usage() {
  cat <<'EOF'
Usage: install.sh [--prefix PATH] [--source PATH] [--repo URL] [--ref REF]

Installs symphonz to:
  PATH/bin/symphonz
  PATH/lib/symphonz

Examples:
  curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh
  sh install.sh --prefix "$HOME/.local" --source .
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix)
      PREFIX="$2"
      shift 2
      ;;
    --source)
      SOURCE_DIR="$2"
      shift 2
      ;;
    --repo)
      REPO_URL="$2"
      shift 2
      ;;
    --ref)
      REF="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

PREFIX=$(cd "$(dirname "$PREFIX")" && pwd -P)/$(basename "$PREFIX")
BIN_DIR="${PREFIX}/bin"
LIB_DIR="${PREFIX}/lib"
TMP_DIR=""

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

if [ -z "$SOURCE_DIR" ]; then
  TMP_DIR=$(mktemp -d)

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "${REPO_URL}/archive/${REF}.tar.gz" -o "${TMP_DIR}/symphonz.tar.gz"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "${TMP_DIR}/symphonz.tar.gz" "${REPO_URL}/archive/${REF}.tar.gz"
  else
    echo "curl or wget is required to download symphonz" >&2
    exit 1
  fi

  tar -xzf "${TMP_DIR}/symphonz.tar.gz" -C "$TMP_DIR"
  SOURCE_DIR=$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)
fi

if [ ! -f "${SOURCE_DIR}/bin/symphonz" ] || [ ! -d "${SOURCE_DIR}/symphonz" ]; then
  echo "Invalid symphonz source directory: ${SOURCE_DIR}" >&2
  exit 1
fi

mkdir -p "$BIN_DIR" "$LIB_DIR"
rm -rf "${LIB_DIR}/symphonz"
cp "${SOURCE_DIR}/bin/symphonz" "${BIN_DIR}/symphonz"
cp -R "${SOURCE_DIR}/symphonz" "${LIB_DIR}/symphonz"
chmod +x "${BIN_DIR}/symphonz"

cat <<EOF
symphonz installed.

Binary: ${BIN_DIR}/symphonz

Add this to your shell profile if it is not already in PATH:
  export PATH="${BIN_DIR}:\$PATH"

Try:
  symphonz version
  symphonz install --runtime global
EOF
