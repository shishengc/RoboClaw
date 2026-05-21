# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

from corobot.utils.singleton import singleton


@singleton
class G01Robot:
    def __init__(self):
        from a2d_sdk.robot import RobotController
        from a2d_sdk.robot import RobotDds as Robot

        self.robot = Robot()
        self.controller = RobotController()

        # RobotBundleData / VRControl 在旧版 a2d_sdk 中不存在，兼容处理
        try:
            from a2d_sdk.robot import RobotBundleData as Bundle
            self.aligned_robot = Bundle()
        except ImportError:
            self.aligned_robot = None

        try:
            from a2d_sdk.robot import VRControl
            self.vr_control = VRControl()
        except ImportError:
            self.vr_control = None

    def instance_robot(self):
        return self.robot

    def instance_controller(self):
        return self.controller

    def instance_bundle(self):
        return self.aligned_robot

    def instance_vrControl(self):
        return self.vr_control
