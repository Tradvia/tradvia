#!/usr/bin/env bash
# SPDX-License-Identifier: FSL-1.1-ALv2
#
# Enforces the licensing boundary of this repository:
#   1. core/ never references code in ee/
#   2. every source file carries the SPDX header that matches its directory
#   3. both license files exist
set -euo pipefail

cd "$(dirname "$0")/.."

CORE_ID="SPDX-License-Identifier: FSL-1.1-ALv2"
EE_ID_PREFIX="SPDX-License-Identifier: LicenseRef-"
SRC_EXT='\.(go|ts|tsx|js|jsx|mjs|cjs|py|rs|java|kt|cs|rb|php|sh|c|h|cpp|hpp|swift|scala|sql|proto|vue|svelte)$'
fail=0

err() { echo "::error::$*"; fail=1; }

# 3. license files
[ -f core/LICENSE.md ] || err "core/LICENSE.md is missing"
[ -f ee/LICENSE.md ]   || err "ee/LICENSE.md is missing"

# 1. core -> ee dependencies (imports, relative paths, module paths)
#    Markdown is ignored so docs can mention ee/ freely.
if grep -rInE \
     --exclude='*.md' --exclude-dir=node_modules --exclude-dir=vendor \
     "(\.\./)+ee/|['\"\`/]ee/|^\s*(from|import)\s+ee([./ ]|$)|require\(['\"][^'\"]*/ee/" \
     core 2>/dev/null; then
  err "core/ references ee/ (lines above). core must build and run with ee/ deleted."
fi

# 2. SPDX headers (checked in the first 5 lines of each file)
check_headers() {
  local dir="$1" expected="$2"
  while IFS= read -r -d '' f; do
    echo "$f" | grep -qE "$SRC_EXT" || continue
    if ! head -n 5 "$f" | grep -qF "$expected"; then
      err "$f: missing header '$expected'"
    fi
  done < <(find "$dir" -type f -not -path '*/node_modules/*' -not -path '*/vendor/*' -print0)
}
check_headers core "$CORE_ID"
check_headers ee   "$EE_ID_PREFIX"

if [ "$fail" -ne 0 ]; then
  echo "License boundary check FAILED"
  exit 1
fi
echo "License boundary check passed"
