#!/usr/bin/env bash
# Audit a tree before publishing it. Exits non-zero with one "FAIL: ..." line per problem.
#   tools/release_check.sh [DIR]      (default: the repository root)
# In a git repo it checks what would be committed (tracked plus untracked-and-not-ignored files);
# elsewhere it checks every file outside scratch folders. Checks: no personal home path or name inside any
# text file, no removed or derived sample names, no file over 20 MB, LICENSE and NOTICE present.
set -u
DIR="$(cd "${1:-$(dirname "$0")/..}" && pwd)"
fail=0
problem() { echo "FAIL: $*"; fail=1; }

# Files that legitimately name the patterns we look for, or that never ship.
skip='(^|/)(CLAUDE\.md|tools/release_check\.sh|tests/test_release_check\.py)$|^docs/superpowers/'

list_files() {  # relative paths
    if git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 && [ -d "$DIR/.git" ]; then
        git -C "$DIR" ls-files -co --exclude-standard
    else
        (cd "$DIR" && find . \( -name .git -o -name .venv -o -name vendor -o -name __pycache__ \
            -o -name .pytest_cache -o -name .claude -o -name .superpowers -o -name .cache \) -prune \
            -o -type f -print | sed 's#^\./##')
    fi
}

while IFS= read -r rel; do
    [ -f "$DIR/$rel" ] || continue
    printf '%s' "$rel" | grep -Eq "$skip" && continue
    printf '%s' "$rel" | grep -Eq 'phone-2026|ep437|\.ts\.flac$|\.boosted\.flac$' \
        && problem "file that must not ship: $rel"
    size=$(stat -c %s "$DIR/$rel")
    [ "$size" -gt 20971520 ] && problem "file over 20 MB: $rel"
    if [ "$size" -lt 5242880 ] && grep -Iq -e '/home/[a-z]' -e Jaza -e Talon -e Edmonton -e cwduby "$DIR/$rel" 2>/dev/null; then
        problem "personal path or name in $rel"
    fi
done < <(list_files)

for name in LICENSE NOTICE; do
    [ -f "$DIR/$name" ] || problem "missing $name"
done

[ "$fail" -eq 0 ] && echo "release check passed: $DIR"
exit "$fail"
