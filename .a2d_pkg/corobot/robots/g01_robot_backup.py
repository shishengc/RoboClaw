# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.


from corobot.utils.singleton import singleton


@singleton
class G01Robot:
    def __init__(self):
        from a2d_sdk.robot import RobotBundleData as Bundle
        from a2d_sdk.robot import RobotController, VRControl
        from a2d_sdk.robot import RobotDds as Robot
        self.aligned_robot = Bundle()
        self.vr_control = VRControl()

        self.robot = Robot()
        self.controller = RobotController()

    def instance_robot(self):
        return self.robot

    def instance_controller(self):
        return self.controller

    def instance_bundle(self):
        return self.aligned_robot
    
    def instance_vrControl(self):
        return self.vr_control
