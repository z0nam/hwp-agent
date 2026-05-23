#!/usr/bin/env bash
#
# bootstrap.sh — build the HWP -> HWPX converter into vendor/hwp2hwpx.jar
#
# neolord0/hwp2hwpx ships as a Maven *library* with no main method, so we:
#   1. clone it,
#   2. build it and gather its runtime deps (hwplib, hwpxlib) with Maven,
#   3. compile our thin CLI wrapper (scripts/Hwp2HwpxCli.java) against them,
#   4. fuse everything into one runnable fat jar at vendor/hwp2hwpx.jar.
#
# Re-runnable and idempotent. Override the source with HWP2HWPX_REPO / _REF.
set -euo pipefail

HWP2HWPX_REPO="${HWP2HWPX_REPO:-https://github.com/neolord0/hwp2hwpx.git}"
HWP2HWPX_REF="${HWP2HWPX_REF:-master}"
MIN_JDK=17

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor"
WRAPPER_SRC="$SCRIPT_DIR/Hwp2HwpxCli.java"
# Patched hwplib classes overlaid onto the fat jar (see scripts/patches/).
PATCH_SRC="$SCRIPT_DIR/patches/HWPCharNormal.java"
OUT_JAR="$VENDOR_DIR/hwp2hwpx.jar"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Prerequisites ------------------------------------------------------
command -v git   >/dev/null 2>&1 || die "git not found."
command -v java  >/dev/null 2>&1 || die "java not found. Install a JDK ${MIN_JDK}+:  brew install openjdk@${MIN_JDK}"
command -v javac >/dev/null 2>&1 || die "javac not found (need a JDK, not just a JRE):  brew install openjdk@${MIN_JDK}"
command -v mvn   >/dev/null 2>&1 || die "mvn not found. Install Maven:  brew install maven"

# javac -version prints e.g. "javac 21.0.11" -> major 21
jdk_major="$(javac -version 2>&1 | awk '{print $2}' | cut -d. -f1)"
[ "${jdk_major:-0}" -ge "$MIN_JDK" ] \
    || die "JDK ${MIN_JDK}+ required, found ${jdk_major:-unknown}.  brew install openjdk@${MIN_JDK}"
log "JDK ${jdk_major} · $(mvn -version 2>/dev/null | head -1)"

[ -f "$WRAPPER_SRC" ] || die "wrapper source missing: $WRAPPER_SRC"
[ -f "$PATCH_SRC" ]   || die "patch source missing: $PATCH_SRC"

# --- 2. Clone --------------------------------------------------------------
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
SRC="$WORKDIR/hwp2hwpx"
log "Cloning ${HWP2HWPX_REPO} (${HWP2HWPX_REF})"
git clone --quiet --depth 1 --branch "$HWP2HWPX_REF" "$HWP2HWPX_REPO" "$SRC" 2>/dev/null \
    || git clone --quiet --depth 1 "$HWP2HWPX_REPO" "$SRC"

# --- 3. Build library + collect runtime deps ------------------------------
# hwp2hwpx's pom targets Java 7, which a modern javac refuses; force 8.
log "Building hwp2hwpx (downloads hwplib/hwpxlib on first run)"
mvn -q -f "$SRC/pom.xml" -DskipTests \
    -Dmaven.compiler.source=8 -Dmaven.compiler.target=8 \
    clean package
mvn -q -f "$SRC/pom.xml" \
    -DincludeScope=runtime -DoutputDirectory="$SRC/target/deps" \
    dependency:copy-dependencies

LIB_JAR="$(find "$SRC/target" -maxdepth 1 -name 'hwp2hwpx-*.jar' \
    ! -name '*-sources.jar' ! -name '*-javadoc.jar' | head -1)"
[ -n "$LIB_JAR" ] || die "Maven build produced no hwp2hwpx jar"

# --- 4. Compile our CLI wrapper + hwplib patch ----------------------------
# The patched HWPCharNormal fixes hwplib's surrogate-pair handling (non-BMP /
# Hancom-PUA chars -> U+FFFD); see scripts/patches/ and docs/findings.md. It is
# compiled against the resolved hwplib jar and overlaid below so its .class wins.
log "Compiling $(basename "$WRAPPER_SRC") + $(basename "$PATCH_SRC")"
CLASSES="$WORKDIR/classes"
mkdir -p "$CLASSES"
javac -cp "${LIB_JAR}:${SRC}/target/deps/*" -d "$CLASSES" "$WRAPPER_SRC" "$PATCH_SRC"

# --- 5. Assemble the fat jar ----------------------------------------------
log "Assembling fat jar"
STAGE="$WORKDIR/stage"
mkdir -p "$STAGE"
shopt -s nullglob
for jar in "$LIB_JAR" "$SRC"/target/deps/*.jar; do
    (cd "$STAGE" && jar -xf "$jar")
done
shopt -u nullglob
# our wrapper + patched hwplib classes take precedence over the exploded jars
cp -R "$CLASSES/." "$STAGE/"
# strip upstream jar signatures — they don't survive a merged jar
rm -f "$STAGE"/META-INF/*.SF "$STAGE"/META-INF/*.DSA "$STAGE"/META-INF/*.RSA 2>/dev/null || true

mkdir -p "$VENDOR_DIR"
jar --create --file "$OUT_JAR" --main-class Hwp2HwpxCli -C "$STAGE" .

log "Built ${OUT_JAR#"$REPO_ROOT"/}  ($(du -h "$OUT_JAR" | cut -f1))"
# smoke-check: no-arg run should print usage and exit 2
java -jar "$OUT_JAR" >/dev/null 2>&1 && rc=0 || rc=$?
[ "$rc" -eq 2 ] || die "fat jar did not run as expected (exit $rc)"
log "OK — run:  hwp-agent convert input.hwp output.hwpx"
