#!/usr/bin/env bash
# Differential parity check: host mdfix vs the SLOW-32 guest build.
#
# Runs both binaries over every markdown file in the repo under several
# option profiles and demands byte-identical output and matching exit
# codes, then exercises the paths the sweep cannot reach: the in-place
# atomic save (whose mkstemp/link/fsync degrade to shims on SLOW-32),
# --diff (open_memstream hunk assembly), and --canonical-lint (the
# render-until-convergence fmemopen loop).
#
# Like check-sync, this fails rather than skips when the SLOW-32
# toolchain is missing: a parity check that silently passes when it
# cannot run is worse than no check. It is deliberately not part of
# `make test`; run it via `make slow32-check` when the slow-32 repo is
# available.
set -u
cd "$(dirname "$0")"

SLOW32_ROOT="${SLOW32_ROOT:-$HOME/slow-32}"
EMU="$SLOW32_ROOT/tools/emulator/slow32-fast"

[ -x "$EMU" ] || { echo "test-slow32: emulator not found: $EMU"; exit 1; }
[ -x ./mdfix ] || { echo "test-slow32: host mdfix missing — run make first"; exit 1; }
[ -f ./mdfix.s32x ] || { echo "test-slow32: mdfix.s32x missing — run make slow32 first"; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Guest exit code comes from the emulator's summary line; its banner and
# stats share stdout with the guest.
guest_rc() { grep -o 'Exit code: [0-9]*' "$1" | awk '{print $3}'; }

PROFILES=(
  ""
  "--canonical"
  "--technical"
  "--canonical --pandoc-safe-links"
  "--normalize-nfc"
)

fails=0
runs=0
while IFS= read -r f; do
    for p in "${PROFILES[@]}"; do
        runs=$((runs+1))
        rm -f "$WORK/host.md" "$WORK/guest.md"
        # shellcheck disable=SC2086
        ./mdfix -q $p "$f" "$WORK/host.md" >/dev/null 2>&1
        host_rc=$?
        # shellcheck disable=SC2086
        "$EMU" mdfix.s32x -q $p "$f" "$WORK/guest.md" >"$WORK/guest.out" 2>&1
        grc=$(guest_rc "$WORK/guest.out")
        if [ "$host_rc" != "${grc:-MISSING}" ]; then
            echo "RC MISMATCH   $f [$p]: host=$host_rc guest=${grc:-none}"
            fails=$((fails+1)); continue
        fi
        if [ "$host_rc" = "0" ] && ! cmp -s "$WORK/host.md" "$WORK/guest.md"; then
            echo "BYTE MISMATCH $f [$p]"
            fails=$((fails+1))
        fi
    done
done < <(find .. -name '*.md' -not -path '*/.git/*')

# In-place save: transformed file and .bak must both match the host's.
cp ../tests/fixtures/fences/input.md "$WORK/h.md"
cp ../tests/fixtures/fences/input.md "$WORK/g.md"
./mdfix -q --canonical -i "$WORK/h.md" >/dev/null 2>&1
"$EMU" mdfix.s32x -q --canonical -i "$WORK/g.md" >/dev/null 2>&1
cmp -s "$WORK/h.md" "$WORK/g.md" || { echo "IN-PLACE MISMATCH"; fails=$((fails+1)); }
cmp -s "$WORK/h.md.bak" "$WORK/g.md.bak" || { echo "BAK MISMATCH"; fails=$((fails+1)); }
leftovers=$(find "$WORK" -name '*.bak.*' -o -name '*XXXXXX*' -o -name '*.md.[0-9]*' | wc -l)
[ "$leftovers" -eq 0 ] || { echo "TEMP FILES LEAKED"; fails=$((fails+1)); }

# --diff hunks assemble through open_memstream; compare them byte for byte.
./mdfix --canonical --diff ../tests/fixtures/fences/input.md >"$WORK/h.diff" 2>/dev/null
"$EMU" mdfix.s32x --canonical --diff ../tests/fixtures/fences/input.md 2>/dev/null \
    | sed -n '/^Starting execution/q;p' >"$WORK/g.diff"
cmp -s "$WORK/h.diff" "$WORK/g.diff" || { echo "DIFF MISMATCH"; fails=$((fails+1)); }

# --canonical-lint runs the fmemopen convergence loop; gate must agree
# on both a dirty and an already-canonical file.
for f in ../tests/fixtures/fences/input.md ../tests/fixtures/fences/mdfix-canonical.md; do
    ./mdfix -q --canonical-lint "$f" >/dev/null 2>&1
    host_rc=$?
    "$EMU" mdfix.s32x -q --canonical-lint "$f" >"$WORK/lint.out" 2>&1
    grc=$(guest_rc "$WORK/lint.out")
    [ "$host_rc" = "${grc:-MISSING}" ] || { echo "LINT RC MISMATCH $f"; fails=$((fails+1)); }
done

echo "test-slow32: $runs sweep runs + save/diff/lint checks, $fails failures"
exit $((fails > 0))
