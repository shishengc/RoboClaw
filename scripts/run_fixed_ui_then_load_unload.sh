#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
BYPASS_POLICY_WAIST_PREPOSE="${BYPASS_POLICY_WAIST_PREPOSE:-0}"

usage() {
  cat >&2 <<'EOF'
Usage:
  bash scripts/run_fixed_ui_then_load_unload.sh

Runs these two validated flows in order:
  1. python3 scripts/run_fixed_ui_sequence.py --execute --skip-drawer
  2. bash scripts/test_load_unload.sh

Environment:
  PYTHON_BIN                     default python3
  COROBOT_URL                    forwarded to both scripts; default http://localhost:8765 in child scripts
  BYPASS_POLICY_WAIST_PREPOSE    default 0; set 1 to avoid test_load_unload.sh triggering
                                RuleControlTask's exact-match drawer waist prepose branch

Any environment accepted by scripts/test_load_unload.sh can also be set here,
for example PULL_POLICY_PORT, PUSH_POLICY_PORT, LOAD_UNLOAD_STAGE, POLICY_TIMEOUT_S.

Examples:
  bash scripts/run_fixed_ui_then_load_unload.sh

  BYPASS_POLICY_WAIST_PREPOSE=1 bash scripts/run_fixed_ui_then_load_unload.sh

  BYPASS_POLICY_WAIST_PREPOSE=1 PULL_POLICY_PORT=8998 PUSH_POLICY_PORT=8999 \
    bash scripts/run_fixed_ui_then_load_unload.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$#" -ne 0 ]]; then
  usage
  exit 2
fi

cd "${REPO_ROOT}"

echo "Combined fixed UI + load/unload sequence:"
echo "  1. ${PYTHON_BIN} scripts/run_fixed_ui_sequence.py --execute --skip-drawer"
echo "  2. bash scripts/test_load_unload.sh"
echo "  BYPASS_POLICY_WAIST_PREPOSE=${BYPASS_POLICY_WAIST_PREPOSE}"
echo

"${PYTHON_BIN}" scripts/run_fixed_ui_sequence.py --execute --skip-drawer

echo
echo "Fixed UI sequence finished. Starting load/unload sequence..."
echo

if [[ "${BYPASS_POLICY_WAIST_PREPOSE}" == "1" ]]; then
  EXPECT_PULL_PREPOSE="${EXPECT_PULL_PREPOSE:-0}" \
  PULL_PROMPT="${PULL_PROMPT:-Pull open the drawer.}" \
  PUSH_PROMPT="${PUSH_PROMPT:-Push close the drawer.}" \
    bash scripts/test_load_unload.sh
else
  bash scripts/test_load_unload.sh
fi

echo
echo "Combined fixed UI + load/unload sequence finished."
