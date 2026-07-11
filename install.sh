#!/bin/sh
set -eu

PREFIX="${HOME}/.symphonz"
PREFIX_SET=0
BIN_DIR=""
SOURCE_DIR=""
DEFAULT_REPO_URL="https://github.com/ilordhalo/zhangjiahao_agi"
REPO_URL="${SYMPHONZ_REPO_URL:-$DEFAULT_REPO_URL}"
REF="${SYMPHONZ_REF:-main}"

usage() {
  cat <<'EOF'
Usage: install.sh [--prefix PATH] [--bin-dir PATH] [--source PATH] [--repo URL] [--ref REF]

Installs symphonz to:
  CLI: first writable directory already in PATH, or PATH/bin when --prefix is set
  Library: PATH/lib/symphonz

Examples:
  curl -fsSL https://raw.githubusercontent.com/ilordhalo/zhangjiahao_agi/main/install.sh | sh
  sh install.sh --prefix "$HOME/.local" --source .
EOF
}

path_has_dir() {
  if [ ! -d "$1" ]; then
    return 1
  fi

  wanted=$(cd "$1" && pwd -P)
  old_ifs=$IFS
  IFS=:
  set -- $PATH
  IFS=$old_ifs

  for dir do
    if [ -d "$dir" ] && [ "$(cd "$dir" && pwd -P)" = "$wanted" ]; then
      return 0
    fi
  done

  return 1
}

find_writable_path_dir() {
  old_ifs=$IFS
  IFS=:
  set -- $PATH
  IFS=$old_ifs

  for dir do
    case "$dir" in
      "$HOME"/*|/opt/homebrew/bin|/usr/local/bin)
        if [ -d "$dir" ] && [ -w "$dir" ]; then
          printf '%s\n' "$dir"
          return 0
        fi
        ;;
    esac
  done

  return 1
}

escape_double_quoted() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\$/\\$/g; s/`/\\`/g'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix)
      PREFIX="$2"
      PREFIX_SET=1
      shift 2
      ;;
    --bin-dir)
      BIN_DIR="$2"
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

mkdir -p "$(dirname "$PREFIX")"
PREFIX=$(cd "$(dirname "$PREFIX")" && pwd -P)/$(basename "$PREFIX")
RELEASES_DIR="${PREFIX}/releases"
CURRENT_LINK="${PREFIX}/current"
LIB_DIR="${CURRENT_LINK}/lib"

if [ -z "$BIN_DIR" ]; then
  if [ "$PREFIX_SET" -eq 1 ]; then
    BIN_DIR="${PREFIX}/bin"
  else
    BIN_DIR=$(find_writable_path_dir || true)
    if [ -z "$BIN_DIR" ]; then
      BIN_DIR="${PREFIX}/bin"
    fi
  fi
fi

TMP_DIR=""
STAGING_DIR=""
POINTER_TMP=""

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
  if [ -n "$STAGING_DIR" ] && [ -d "$STAGING_DIR" ]; then
    rm -rf "$STAGING_DIR"
  fi
  if [ -n "$POINTER_TMP" ] && [ -L "$POINTER_TMP" ]; then
    rm -f "$POINTER_TMP"
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

if [ ! -f "${SOURCE_DIR}/bin/symphonz" ] || [ ! -d "${SOURCE_DIR}/symphonz" ] || [ ! -f "${SOURCE_DIR}/WORKFLOW.md" ]; then
  echo "Invalid symphonz source directory: ${SOURCE_DIR}" >&2
  exit 1
fi

mkdir -p "$BIN_DIR" "$RELEASES_DIR"
BIN_DIR=$(cd "$BIN_DIR" && pwd -P)
STAGING_DIR=$(mktemp -d "${PREFIX}/.symphonz-install.XXXXXX")
mkdir -p "${STAGING_DIR}/release/lib" "${STAGING_DIR}/bin"
cp -R "${SOURCE_DIR}/symphonz" "${STAGING_DIR}/release/lib/symphonz"
cp "${SOURCE_DIR}/WORKFLOW.md" "${STAGING_DIR}/release/lib/WORKFLOW.md"

LIB_DIR_ESCAPED=$(escape_double_quoted "$LIB_DIR")
cat > "${STAGING_DIR}/bin/symphonz" <<EOF
#!/bin/sh
SYMPHONZ_LIB_DIR="${LIB_DIR_ESCAPED}"
PYTHONPATH="\${SYMPHONZ_LIB_DIR}\${PYTHONPATH:+:\$PYTHONPATH}"
export PYTHONPATH
exec python3 -c 'from symphonz.cli import main; raise SystemExit(main())' "\$@"
EOF
chmod +x "${STAGING_DIR}/bin/symphonz"

RELEASE_DIR="${RELEASES_DIR}/$(date +%Y%m%d%H%M%S)-$$"
mv "${STAGING_DIR}/release" "$RELEASE_DIR"
ln -s "$RELEASE_DIR" "${STAGING_DIR}/current"
POINTER_TMP="${PREFIX}/current.$$"
mv "${STAGING_DIR}/current" "$POINTER_TMP"
python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$POINTER_TMP" "$CURRENT_LINK"
POINTER_TMP=""
mv "${STAGING_DIR}/bin/symphonz" "${BIN_DIR}/symphonz"

cat <<EOF
symphonz installed.

Binary: ${BIN_DIR}/symphonz
Library: ${LIB_DIR}/symphonz
EOF

if path_has_dir "$BIN_DIR"; then
  cat <<'EOF'
Try:
  symphonz version
  symphonz install --runtime global
EOF
else
  cat <<EOF
Add this to your shell profile if it is not already in PATH:
  export PATH="${BIN_DIR}:\$PATH"

Try:
  ${BIN_DIR}/symphonz version
  ${BIN_DIR}/symphonz install --runtime global
EOF
fi
