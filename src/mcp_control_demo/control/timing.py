from __future__ import annotations

import math
from dataclasses import dataclass


CONTROL_HZ = 30.0
CONTROL_DT_S = 1.0 / CONTROL_HZ


@dataclass(frozen=True)
class TrajectoryTiming:
    requested_duration_s: float
    control_hz: float
    num_steps: int
    actual_duration_s: float


def make_timing(duration_s: float | None = None) -> TrajectoryTiming:
    requested = 1.0 if duration_s is None else float(duration_s)
    if requested < 0.0 or not math.isfinite(requested):
        raise ValueError(f"duration_s must be finite and non-negative, got {duration_s}")
    num_steps = max(1, int(math.ceil(requested * CONTROL_HZ)))
    actual = num_steps / CONTROL_HZ
    return TrajectoryTiming(
        requested_duration_s=requested,
        control_hz=CONTROL_HZ,
        num_steps=num_steps,
        actual_duration_s=actual,
    )


def validate_no_control_hz(payload: dict) -> None:
    if "control_hz" in payload or "control_frequency_hz" in payload:
        raise ValueError("control frequency is fixed at 30Hz and cannot be overridden")
