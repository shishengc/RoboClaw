#!/usr/bin/env bash
set -euo pipefail

COROBOT_URL="${COROBOT_URL:-http://localhost:8765}"
ARM="${ARM:-right}"
CAMERA_FRAME="${CAMERA_FRAME:-head_camera_optical}"
DURATION_S="${DURATION_S:-2.0}"
DX_CAMERA_M="${1:-0.005}"
DY_CAMERA_M="${2:-0.0}"
DZ_CAMERA_M="${3:-0.0}"

python3 - "$COROBOT_URL" "$ARM" "$CAMERA_FRAME" "$DX_CAMERA_M" "$DY_CAMERA_M" "$DZ_CAMERA_M" "$DURATION_S" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request


base_url, arm, camera_frame, dx, dy, dz, duration_s = sys.argv[1:]
delta = [float(dx), float(dy), float(dz)]


def post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc


pose_response = post("/skill/get_eef_pose", {"arm": arm, "camera_frame": camera_frame})
if not pose_response.get("success"):
    raise SystemExit(json.dumps(pose_response, ensure_ascii=False, indent=2))
pose = pose_response.get("data") or {}
if not pose.get("ok"):
    raise SystemExit(json.dumps(pose_response, ensure_ascii=False, indent=2))
if not pose.get("camera_pose_available"):
    raise SystemExit(
        "Current EEF camera pose is unavailable. Check T_exec_camera first:\n"
        + json.dumps(pose_response, ensure_ascii=False, indent=2)
    )

current = [float(value) for value in pose["position_camera_m"]]
target = [current[index] + delta[index] for index in range(3)]
payload = {
    "arm": arm,
    "camera_frame": camera_frame,
    "target_position_camera_m": target,
    "duration_s": float(duration_s),
}

print("Current EEF pose:")
print(json.dumps(pose, ensure_ascii=False, indent=2))
print("\nSmall move payload:")
print(json.dumps(payload, ensure_ascii=False, indent=2))

if os.environ.get("EXECUTE_MOVE") != "1":
    print("\nDry run only. Re-run with EXECUTE_MOVE=1 to execute this move.")
    raise SystemExit(0)

move_response = post("/skill/move_eef", payload)
print("\nMove response:")
print(json.dumps(move_response, ensure_ascii=False, indent=2))
PY
