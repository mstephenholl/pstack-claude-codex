#!/usr/bin/env bash
# Model-checks the orch store lock (StoreLock.tla) with TLC from TLA+ v1.7.4.
#   check.sh [StoreLock.cfg] [StoreLockNoCrash.cfg]    no argument runs both
# Prints each configuration's result, with the counterexample when an
# invariant fails, and exits 1 if any configuration found one.
set -euo pipefail

version=v1.7.4
sha256=936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88
here=$(cd "$(dirname "$0")" && pwd)
jar=${TLA2TOOLS_JAR:-${XDG_CACHE_HOME:-$HOME/.cache}/tla2tools/$version/tla2tools.jar}

if [ ! -f "$jar" ]; then
  mkdir -p "$(dirname "$jar")"
  curl -fsSL -o "$jar.part" "https://github.com/tlaplus/tlaplus/releases/download/$version/tla2tools.jar"
  mv "$jar.part" "$jar"
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$jar")
else
  actual=$(shasum -a 256 "$jar")
fi
if [ "${actual%% *}" != "$sha256" ]; then
  echo "error: $jar is not tla2tools.jar $version (sha256 ${actual%% *})" >&2
  exit 2
fi

# TLC writes states/ and trace files next to the spec, so run on a copy.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp "$here/StoreLock.tla" "$here"/*.cfg "$work/"

[ $# -gt 0 ] || set -- StoreLock.cfg StoreLockNoCrash.cfg
status=0
for cfg in "$@"; do
  echo "== $cfg"
  if (cd "$work" && java -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers 1 \
      -metadir "$work/states" -config "$cfg" StoreLock.tla) >"$work/out" 2>&1; then
    grep -E "^Model checking completed|distinct states found" "$work/out"
  elif grep -q "is violated" "$work/out"; then
    sed -n '/^Error: Invariant/,/distinct states found/p' "$work/out"
    status=1
  else
    cat "$work/out"
    echo "error: TLC did not finish $cfg" >&2
    exit 2
  fi
done
exit $status
