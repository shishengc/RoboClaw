# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

from corobot.motion_control.motion_control_base import MotionControlBase
from corobot.protocol.protocol_schemas import STD_MODEL_OUTPUT


class MockMotionController(MotionControlBase):
    def __init__(self):
        pass

    def start(self, ts=None):
        pass

    def stop(self):
        pass

    def execute(self, std_model_output: STD_MODEL_OUTPUT):
        pass
