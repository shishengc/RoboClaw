from __future__ import annotations

import math
from collections.abc import Sequence


HEAD_YAW_RAD_ABS_LIMIT = 1.5708
HEAD_PITCH_RAD_ABS_LIMIT = 0.5233
HEAD_UNIT_LIMIT_MARGIN_RAD = 0.05


def normalize_head_joint_states_rad(head_joint_states: Sequence[float]) -> list[float]:
    """Return head joint states in radians.

    CoRobot FK expects radians, but some RobotDds fallback observations expose
    head joints in degrees. If any head joint is outside the URDF radian range,
    treat the full head pair as degrees so yaw/pitch stay in the same unit.
    """

    values = [float(value) for value in head_joint_states]
    if len(values) < 2:
        return values

    yaw, pitch = values[:2]
    looks_like_degrees = (
        abs(yaw) > HEAD_YAW_RAD_ABS_LIMIT + HEAD_UNIT_LIMIT_MARGIN_RAD
        or abs(pitch) > HEAD_PITCH_RAD_ABS_LIMIT + HEAD_UNIT_LIMIT_MARGIN_RAD
    )
    if not looks_like_degrees:
        return values

    return [math.radians(value) for value in values]
