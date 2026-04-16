#!/usr/bin/env bash
# build.sh — build padb distribution artefacts
#
# Produces up to three artefacts in dist/:
#   padb               — standalone binary  (PyInstaller, no bundled adb)
#   padb-embedded      — self-contained binary (PyInstaller + bundled adb)
#   padb.pyz           — single Python file  (shiv, requires Python 3 on target)
#
# Usage:
#   ./build.sh                    # build all three
#   ./build.sh --no-embedded      # skip the embedded-adb binary
#   ./build.sh --no-pyz           # skip the shiv .pyz
#   ./build.sh standalone         # build only the standalone binary
#   ./build.sh embedded           # build only the embedded-adb binary
#   ./build.sh pyz                # build only the .pyz
#
# Requirements:  pip3 install pyinstaller shiv

set -euo pipefail

# ── add Python user bin to PATH (pip install --user puts scripts here) ────────
PY_USER_BIN="$(python3 -m site --user-base)/bin"
export PATH="$PY_USER_BIN:$PATH"

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
die()     { echo -e "${RED}[error]${RESET} $*" >&2; exit 1; }
header()  { echo -e "\n${BOLD}=== $* ===${RESET}"; }

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
BUILD_DIR="$SCRIPT_DIR/build"
MAIN="$SCRIPT_DIR/main.py"
HOOK="$SCRIPT_DIR/scripts/hook_adb.py"

# ── platform detection ────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Darwin) PLATFORM="darwin"; ADB_ZIP_PLATFORM="darwin" ;;
    Linux)  PLATFORM="linux";  ADB_ZIP_PLATFORM="linux"  ;;
    *)      die "Unsupported OS: $OS. Build on macOS or Linux." ;;
esac

# adb binary name inside the extracted platform-tools archive
ADB_BIN_NAME="adb"
ADB_DOWNLOAD_DIR="$BUILD_DIR/adb_download"
ADB_BIN="$ADB_DOWNLOAD_DIR/platform-tools/adb"

# ── argument parsing ──────────────────────────────────────────────────────────
BUILD_STANDALONE=true
BUILD_EMBEDDED=true
BUILD_PYZ=true

for arg in "$@"; do
    case "$arg" in
        standalone)         BUILD_STANDALONE=true; BUILD_EMBEDDED=false; BUILD_PYZ=false ;;
        embedded)           BUILD_STANDALONE=false; BUILD_EMBEDDED=true; BUILD_PYZ=false ;;
        pyz)                BUILD_STANDALONE=false; BUILD_EMBEDDED=false; BUILD_PYZ=true ;;
        --no-embedded)      BUILD_EMBEDDED=false ;;
        --no-pyz)           BUILD_PYZ=false ;;
        --help|-h)
            sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# ── dependency checks ─────────────────────────────────────────────────────────
header "Checking build dependencies"

check_cmd() {
    if command -v "$1" &>/dev/null; then
        success "$1 found: $(command -v "$1")"
    else
        die "$1 not found. Install with: pip3 install $2"
    fi
}

check_cmd python3 python3
check_cmd pip3 pip3

if $BUILD_STANDALONE || $BUILD_EMBEDDED; then
    check_cmd pyinstaller pyinstaller
fi
if $BUILD_PYZ; then
    check_cmd shiv shiv
fi

# Verify we are in the project root (main.py must exist)
[[ -f "$MAIN" ]] || die "main.py not found. Run this script from the project root."
[[ -f "$HOOK" ]] || die "scripts/hook_adb.py not found."

mkdir -p "$DIST_DIR" "$BUILD_DIR"

# ── version ───────────────────────────────────────────────────────────────────
VERSION="$(python3 -c "from padb import __version__; print(__version__)")"
info "Building padb v${VERSION} for ${PLATFORM}/${ARCH}"

# ── common PyInstaller flags ──────────────────────────────────────────────────
# --collect-all adbutils   → includes data files under adbutils/ (binaries/, etc.)
# --collect-all PIL        → includes PIL plugins and .so extensions
# --strip                  → strip debug symbols (smaller binary, macOS/Linux only)
PYINSTALLER_COMMON=(
    --onefile
    --collect-all adbutils
    --collect-all PIL
    --distpath "$DIST_DIR"
    --workpath "$BUILD_DIR/pyinstaller"
    --specpath "$BUILD_DIR"
    --noconfirm
    --clean
)

# ── helper: print final binary info ──────────────────────────────────────────
show_artifact() {
    local path="$1"
    if [[ -f "$path" ]]; then
        local size
        size=$(du -sh "$path" | cut -f1)
        success "$(basename "$path")  →  $path  ($size)"
    else
        warn "Expected artifact not found: $path"
    fi
}

# ═════════════════════════════════════════════════════════════════════════════
# 1. STANDALONE BINARY  (no bundled adb — adb must be on PATH)
# ═════════════════════════════════════════════════════════════════════════════
build_standalone() {
    header "Building standalone binary (no bundled adb)"

    pyinstaller \
        "${PYINSTALLER_COMMON[@]}" \
        --name "padb" \
        "$MAIN"

    show_artifact "$DIST_DIR/padb"
    echo
    info "This binary requires 'adb' to be on PATH (or set ADBUTILS_ADB_PATH)."
}

# ═════════════════════════════════════════════════════════════════════════════
# 2. EMBEDDED BINARY  (adb downloaded at build time and bundled)
# ═════════════════════════════════════════════════════════════════════════════

download_adb() {
    if [[ -f "$ADB_BIN" ]]; then
        info "Cached adb binary found, skipping download."
        return
    fi

    info "Downloading platform-tools for ${ADB_ZIP_PLATFORM}..."
    mkdir -p "$ADB_DOWNLOAD_DIR"
    local zip_url
    zip_url="https://dl.google.com/android/repository/platform-tools-latest-${ADB_ZIP_PLATFORM}.zip"
    local zip_path="$ADB_DOWNLOAD_DIR/platform-tools.zip"

    if command -v curl &>/dev/null; then
        curl -fsSL --progress-bar -o "$zip_path" "$zip_url"
    elif command -v wget &>/dev/null; then
        wget -q --show-progress -O "$zip_path" "$zip_url"
    else
        die "Neither curl nor wget found. Cannot download adb."
    fi

    info "Extracting adb..."
    unzip -q -o "$zip_path" "platform-tools/adb" -d "$ADB_DOWNLOAD_DIR"
    chmod +x "$ADB_BIN"
    rm "$zip_path"

    local adb_version
    adb_version="$("$ADB_BIN" version 2>/dev/null | head -1 || echo 'unknown')"
    success "Downloaded: $adb_version"
}

build_embedded() {
    header "Building embedded binary (adb bundled)"

    download_adb

    # --add-data src:dest_in_meipass (colon separator on macOS/Linux)
    # Hook sets ADBUTILS_ADB_PATH=_MEIPASS/adb before adbutils is imported
    pyinstaller \
        "${PYINSTALLER_COMMON[@]}" \
        --name "padb-embedded" \
        --add-data "${ADB_BIN}:." \
        --runtime-hook "$HOOK" \
        "$MAIN"

    show_artifact "$DIST_DIR/padb-embedded"
    echo
    info "This binary is fully self-contained — no system adb required."
}

# ═════════════════════════════════════════════════════════════════════════════
# 3. PYTHON SINGLE-FILE  (shiv .pyz — requires Python 3 on target, no pip)
# ═════════════════════════════════════════════════════════════════════════════
#
# WHY SHIV AND NOT PLAIN ZIPAPP:
#
# python -m zipapp bundles pure Python code into a .pyz that runs directly.
# It cannot bundle C extensions (.so/.dylib files) because the OS linker
# (dlopen) requires a real filesystem path — it cannot load shared libraries
# straight from a zip archive.
#
# adbutils imports Pillow at the TOP LEVEL in _device_base.py:
#   from PIL import Image, UnidentifiedImageError   (line 21)
# Pillow ships C extensions for image I/O (jpeg, png, etc.), so plain zipapp
# fails immediately with an ImportError on any `import adbutils` call.
#
# SHIV solves this by extracting site-packages to a real directory on first
# run (~/.shiv/<content-sha256>/) and adding that directory to sys.path.
# Subsequent runs skip the extraction (the hash acts as a cache key).
# The result is:
#   • A single .pyz file ≈ compressed size of all deps
#   • First run: extract (~3s), then launch
#   • Subsequent runs: launch immediately
#   • Only requirement on the target machine: Python 3.11+
#   • adb still must be on PATH (or ADBUTILS_ADB_PATH set)
#
# SHIV_ROOT env var can redirect the extraction cache (default: ~/.shiv/).
#
build_pyz() {
    header "Building Python single-file (.pyz via shiv)"

    local staging="$BUILD_DIR/shiv_staging"
    rm -rf "$staging"
    mkdir -p "$staging"

    info "Installing dependencies into staging area..."
    pip3 install --quiet --target "$staging" \
        adbutils \
        requests \
        retry2 \
        deprecation \
        Pillow

    info "Copying padb package into staging area..."
    cp -r "$SCRIPT_DIR/padb" "$staging/"

    info "Running shiv..."
    shiv \
        --site-packages "$staging" \
        --compressed \
        --python '/usr/bin/env python3' \
        -e 'padb.tui.app:main' \
        -o "$DIST_DIR/padb.pyz"

    rm -rf "$staging"

    show_artifact "$DIST_DIR/padb.pyz"
    echo
    info "Run with:  python3 dist/padb.pyz   (or  chmod +x dist/padb.pyz && ./dist/padb.pyz)"
    info "First run extracts to ~/.shiv/<hash>/ — subsequent runs are instant."
    info "adb must still be on PATH (or set ADBUTILS_ADB_PATH)."
}

# ── run selected builds ───────────────────────────────────────────────────────
header "Build plan"
$BUILD_STANDALONE && info "  • standalone binary  (dist/padb)"
$BUILD_EMBEDDED   && info "  • embedded binary    (dist/padb-embedded)"
$BUILD_PYZ        && info "  • Python single-file (dist/padb.pyz)"
echo

$BUILD_STANDALONE && build_standalone
$BUILD_EMBEDDED   && build_embedded
$BUILD_PYZ        && build_pyz

# ── summary ───────────────────────────────────────────────────────────────────
header "Artifacts"
$BUILD_STANDALONE && show_artifact "$DIST_DIR/padb"
$BUILD_EMBEDDED   && show_artifact "$DIST_DIR/padb-embedded"
$BUILD_PYZ        && show_artifact "$DIST_DIR/padb.pyz"
echo
success "Done."
