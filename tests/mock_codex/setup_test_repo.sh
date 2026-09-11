#!/usr/bin/env bash
# ============================================================================
# setup_test_repo.sh — Creates a temporary git repo for golden-case scenarios
# Purpose : Deterministic scratch repo with disjoint module dirs (src/alpha,
#           src/beta), a shared file for conflict scenarios, and a trivially
#           passing test file for acceptance-replay scenarios.
# Input   : $1 = target directory (created; must not already be a git repo)
# Output  : initialized repo on branch `main`, one base commit.
#           Prints target dir (last line). Exit 0 ok, 2 usage.
# Lines   : ~45
# ============================================================================
set -euo pipefail

TARGET="${1:-}"
[ -n "$TARGET" ] || { echo "usage: setup_test_repo.sh <target_dir>" >&2; exit 2; }
mkdir -p "$TARGET"

GIT="git -C $TARGET -c user.name=mock -c user.email=mock@test.local"

git -C "$TARGET" init -q -b main

mkdir -p "$TARGET/src/alpha" "$TARGET/src/beta" "$TARGET/tests"

cat > "$TARGET/src/alpha/alpha.py" <<'EOF'
def alpha():
    return "alpha"
EOF

cat > "$TARGET/src/beta/beta.py" <<'EOF'
def beta():
    return "beta"
EOF

cat > "$TARGET/tests/test_basic.py" <<'EOF'
import unittest

class TestBasic(unittest.TestCase):
    def test_one(self): self.assertEqual(1 + 1, 2)
    def test_two(self): self.assertTrue("a" in "abc")
    def test_three(self): self.assertEqual(len([1, 2, 3]), 3)

if __name__ == "__main__":
    unittest.main()
EOF

printf 'line1\nline2\nline3\n' > "$TARGET/shared.txt"
printf '# scratch repo for golden cases\n' > "$TARGET/README.md"

git -C "$TARGET" add -A
git -C "$TARGET" -c user.name=mock -c user.email=mock@test.local \
    commit -q -m "base commit (golden-case scratch repo)"
echo "$TARGET"
