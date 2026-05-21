# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
标准协议模式定义

定义输入输出数据的标准格式
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ArmEEFAbs(BaseModel):
    kind: Literal["EEF_ABS"]
    values: list[list[float]]  # shape is (n,12) 12:[x,y,z,rx,ry,rz,x,y,z,rx,ry,rz]


class ArmEEFDelta(BaseModel):
    kind: Literal["EEF_DELTA"]
    values: list[list[float]]


class ArmJointAbs(BaseModel):
    kind: Literal["JOINT_ABS"]
    values: list[list[float]]  # shape is (n,12) [j1_l,j2_l,j3_l,j4_l,j5_l,j6_l,j7_l,j1_r,j2_r,j3_r,j4_r,j5_r,j6_r,j7_r]


class ArmJointDelta(BaseModel):
    kind: Literal["JOINT_DELTA"]
    values: list[list[float]]


ArmControl = Annotated[
    ArmEEFAbs | ArmEEFDelta | ArmJointAbs | ArmJointDelta,
    Field(discriminator="kind"),
]


class Action(BaseModel):
    """动作数据"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    timestamps: int = Field(default=0, description="数据源的帧时间/纳秒")
    trajectory_reference_time: float = Field(default=1.0, description="轨迹要在多少时间内完成/秒")
    left_arm: ArmControl | None = None  # 左臂控制
    right_arm: ArmControl | None = None  # 右臂控制
    head: list[list[float]] | None = None  # 头部关节
    waist: list[list[float]] | None = None  # 腰部关节
    left_effector: list[list[float]] | None = None  # 左末端执行器
    right_effector: list[list[float]] | None = None  # 右末端执行器
    wheels: list[list[float]] | None = None  # 轮子
    base_link: str | None = None  # 基座坐标系 [base_link,arm_base_link]


class Images(BaseModel):
    """图像数据"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    head: Any | None = None
    hand_left: Any | None = None
    hand_right: Any | None = None
    head_depth: Any | None = None
    hand_left_depth: Any | None = None
    hand_right_depth: Any | None = None
    head_center_fisheye: Any | None = None
    hand_left_fisheye: Any | None = None
    hand_right_fisheye: Any | None = None
    head_left_fisheye: Any | None = None
    head_right_fisheye: Any | None = None
    back_left_fisheye: Any | None = None
    back_right_fisheye: Any | None = None


class CameraParams(BaseModel):
    """所有相机的参数集合"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    head: dict | None = None
    hand_left: dict | None = None
    hand_right: dict | None = None
    head_center_fisheye: dict | None = None
    hand_left_fisheye: dict | None = None
    hand_right_fisheye: dict | None = None
    head_left_fisheye: dict | None = None
    head_right_fisheye: dict | None = None
    back_left_fisheye: dict | None = None
    back_right_fisheye: dict | None = None


class VrData(BaseModel):
    """VR数据"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    left_vr_button_key_one: int | None = None
    left_vr_button_key_two: int | None = None
    left_vr_button_hand_trig: float | None = None
    left_vr_button_index_trig: float | None = None
    left_vr_button_axis_x: float | None = None
    left_vr_button_axis_y: float | None = None
    left_vr_button_axis_click: int | None = None

    right_vr_button_key_one: int | None = None
    right_vr_button_key_two: int | None = None
    right_vr_button_hand_trig: float | None = None
    right_vr_button_index_trig: float | None = None
    right_vr_button_axis_x: float | None = None
    right_vr_button_axis_y: float | None = None
    right_vr_button_axis_click: int | None = None


class Pose(BaseModel):
    """位姿数据 - 包含位置和方向信息"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    position: Annotated[list[float], Field(min_length=3, max_length=3)] = Field(description="位置 [x, y, z] (米)")
    orientation: Annotated[list[float], Field(min_length=4, max_length=4)] = Field(
        description="四元数方向 [qx, qy, qz, qw]"
    )

class DualArmPose(BaseModel):
    """双臂末端位姿数据"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    left_arm: Pose | None = Field(default=None, description="左臂末端位姿")
    right_arm: Pose | None = Field(default=None, description="右臂末端位姿")


class EndEffectorPoses(BaseModel):
    """末端执行器位姿集合 - 包含不同参考坐标系下的位姿"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    arm_base_link: DualArmPose | None = Field(default=None, description="相对于机械臂基座的位姿")
    base_link: DualArmPose | None = Field(default=None, description="相对于机器人基座的位姿")


class States(BaseModel):
    """状态数据"""

    # ------G01----非组帧版本---#
    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    head_joint_states: list[float] | None = None
    arm_joint_states: list[float] | None = None
    waist_joint_states: list[float] | None = None
    gripper_states: list[float] | None = None
    hand_joint_states: list[float] | None = None
    end_wrench_states: list[float] | None = None

    # -----G01-----组帧版本-额外添加--#
    copilot_state: str | None = None
    copilot_reward: str | None = None
    robot_position: list[float] | None = None
    robot_orientation: list[float] | None = None
    end_velocity: list[float] | None = None
    joint_current_values: list[float] | None = None
    vr_data: VrData | None = None
    end_pose: EndEffectorPoses | None = None
    vr_control: Action | None = None


# TODO 支持多帧数据组合
class Observation(BaseModel):
    """观测数据"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    timestamps: dict[str, int] = Field(default={}, description="数据帧时间/纳秒")
    images: Images = Field(default_factory=Images, description="图像数据")
    camera_params: CameraParams = Field(default_factory=CameraParams, description="相机参数")
    states: States = Field(default_factory=States, description="状态数据")


# TODO 支持组多帧
class STD_MODEL_INPUT(BaseModel):
    """标准模型输入协议"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    observation: Observation = Field(default_factory=Observation)
    prompt: str = Field(default="", description="提示词")


class Status(BaseModel):
    """状态信息"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    success: bool = Field(default=True, description="是否成功")
    error_message: str = Field(default="", description="错误信息")
    processing_time: float = Field(default=0.0, description="处理时间(秒)")


class STD_MODEL_OUTPUT(BaseModel):
    """标准模型输出协议"""

    model_config = ConfigDict(extra="forbid")  # 禁止额外字段
    action: Action = Field(default_factory=Action)
    observation: Observation = Field(default_factory=Observation)
    status: Status = Field(default_factory=Status)
