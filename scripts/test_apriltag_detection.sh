#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
TAG_ID="${1:-}"

curl -sS -X POST "${COROBOT_URL}/skill/detect_tags" \
  -H "Content-Type: application/json" \
  -d '{}'

if [[ -n "${TAG_ID}" ]]; then
  echo
  curl -sS -X POST "${COROBOT_URL}/skill/get_tag_pose" \
    -H "Content-Type: application/json" \
    -d "{\"tag_id\": ${TAG_ID}}"
fi
