"""Camera-frame MCP control primitives for CoRobot/RoboClaw."""

__all__ = [
    "CONTROL_DT_S",
    "CONTROL_HZ",
    "TrajectoryTiming",
    "make_timing",
]


def __getattr__(name):
    if name in __all__:
        from .control import timing

        return getattr(timing, name)
    raise AttributeError(name)
