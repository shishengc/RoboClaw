# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
真机组帧数据实现
"""

import time
from typing import Any

from scipy.spatial.transform import Rotation as R

from corobot.dataloader.data_source.base_loader import BaseLoader
from corobot.protocol.protocol_schemas import (
    STD_MODEL_INPUT,
    Action,
    ArmEEFAbs,
    ArmEEFDelta,
    ArmJointAbs,
    ArmJointDelta,
    CameraParams,
    States,
    VrData,
)
from corobot.utils.fk_solver import FKTransform
from corobot.utils.log_setting import CoLogger as logger
from corobot.utils.packet_convert import packet_to_dict, packet_to_image

# 导入这些可能需要适配项目环境
try:
    from a2d_sdk.robot import CosineCamera as Camera

    from corobot.robots.g01_robot import G01Robot
except ImportError:
    logger.error("无法导入GDK bundle camera相关模块，请确保已安装相关依赖")


class AlignedRobotLoader(BaseLoader):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.aligned_robot = None
        self.camera = None
        self._fallback_robot = None
        self.supported_cameras = config.get("supported_cameras", [])
        self.image_decode = config.get("data_source", {}).get("ALIGNED_ROBOT", {}).get("image_decode", True)
        self.fk_solver = FKTransform()
        self.timeout = 1

        self.camera_params = {}


    def setup(self):
        """设置真实机器人数据源"""
        try:
            self.aligned_robot = G01Robot().instance_bundle()
            self.vr_control = G01Robot().instance_vrControl()
        except Exception as e:
            print(f"设置真实机器人策略出错: {e}")

        # aligned_robot 为 None 时（旧版 a2d_sdk 无 RobotBundleData），fallback 到 RobotDds
        if self.aligned_robot is None:
            try:
                self._fallback_robot = G01Robot().instance_robot()
                logger.warning("RobotBundleData 不可用，已降级为 RobotDds 读取关节状态")
            except Exception as e:
                self._fallback_robot = None
                logger.error(f"RobotDds fallback 也失败: {e}")
        else:
            self._fallback_robot = None

        try:
            self.camera = Camera(self.supported_cameras)
            self._get_camera_params()
        except Exception as e:
            self.camera = None
            logger.warning(f"相机初始化失败（可在无相机环境下忽略）: {e}")

        return True


    def can_handle(self, data_source_config: dict[str, Any]) -> bool:
        """
        检查是否可以处理真实机器人数据源

        Args:
            data_source_config: 数据源配置

        Returns:
            如果可以处理则返回True
        """

        return data_source_config.get("ALIGNED_ROBOT", {}).get("enabled", False)

    def _get_camera_params(self):
        """获取相机参数"""
        try:
            allowed_camera_param_keys = set(CameraParams.model_fields.keys())
            for camera_name in self.supported_cameras:
                if camera_name not in allowed_camera_param_keys:
                    logger.info(f"相机 {camera_name} 不在CameraParams定义中，跳过参数获取")
                    continue

                logger.info(f"获取相机 {camera_name} 参数")
                params = {}
                try:
                    if hasattr(self.camera, "get_camera_intrinsic_params"):
                        intrinsic = self.camera.get_camera_intrinsic_params(camera_name)
                        if intrinsic:
                            params.update(intrinsic)
                    if hasattr(self.camera, "get_camera_extrinsic_params"):
                        extrinsic = self.camera.get_camera_extrinsic_params(camera_name)
                        if extrinsic:
                            params.update(extrinsic)
                except Exception as e:
                    logger.warning(f"相机 {camera_name} 参数获取失败（可忽略）: {e}")

                logger.info(f"相机 {camera_name} 参数: {params}")
                self.camera_params[camera_name] = params
        except Exception as e:
            logger.error(f"获取相机参数出错: {e}")
            return False


    def get_payload(self, filter_args: dict | None = None) -> STD_MODEL_INPUT:
        """获取数据"""

        # 统一返回结构化 Observation 字典
        observation: dict[str, Any] = {
            "timestamps": {},
            "images": {},
            "camera_params": self.camera_params,
            "states": {},
        }

        # 检查机器人和相机是否已初始化
        if not self.aligned_robot and not self._fallback_robot:
            logger.warning("机器人或相机未初始化")
            return STD_MODEL_INPUT(observation=observation)

        # fallback 路径：用 RobotDds 直接读取关节状态
        if not self.aligned_robot and self._fallback_robot:
            try:
                pos, ts = self._fallback_robot.arm_joint_states()
                pos = [float(x) for x in pos]

                head_pos = []
                waist_pos = []
                gripper_pos = []

                try:
                    head_pos, _ = self._fallback_robot.head_joint_states()
                    head_pos = [float(x) for x in head_pos]
                except Exception:
                    pass

                try:
                    waist_pos, _ = self._fallback_robot.waist_joint_states()
                    waist_pos = [float(x) for x in waist_pos]
                except Exception:
                    pass

                try:
                    gripper_pos, _ = self._fallback_robot.gripper_states()
                    gripper_pos = [float(x) for x in gripper_pos]
                except Exception:
                    pass

                observation["timestamps"]["states"] = ts
                observation["states"] = States(
                    arm_joint_states=pos,
                    head_joint_states=head_pos,
                    waist_joint_states=waist_pos,
                    gripper_states=gripper_pos,
                )
            except Exception as e:
                logger.error(f"RobotDds fallback 读取状态失败: {e}")

            if self.camera and self.supported_cameras:
                import time as _time

                current_ts = int(_time.time() * 1e9)
                for camera_name in self.supported_cameras:
                    try:
                        start = _time.time()
                        while _time.time() - start < self.timeout:
                            packet = self.camera.get_packet_nearest(camera_name, current_ts)
                            if packet is None:
                                _time.sleep(0.001)
                                continue

                            if self.image_decode:
                                observation["images"][camera_name] = packet_to_image(packet)
                            else:
                                observation["images"][camera_name] = packet_to_dict(packet)
                            break
                        else:
                            logger.warning(f"fallback: 获取相机 {camera_name} 图像超时")
                    except Exception as e:
                        logger.error(f"fallback: 获取相机 {camera_name} 图像出错: {e}")

            return STD_MODEL_INPUT(observation=observation)

        current_timestamp, bundle_data_dict = self.aligned_robot.get_latest_bundle()
        observation["timestamps"]["states"] = current_timestamp
        observation["states"] = self._bundle_to_states(bundle_data_dict)

        # 创建VRcontrol states
        vr_data, vr_timestamp = self.vr_control.get_latest_vr_control()
        action = self.convert_vr_control_to_action(vr_data)
        observation["states"].vr_control = action

        # 如果有指定的过滤参数，且只需要机器人状态
        if filter_args and filter_args.get("group") == "body_states_only":
            return STD_MODEL_INPUT(observation=observation)

        # 提取相机时间戳
        camera_timestamps = self._extract_camera_timestamps(bundle_data_dict)
        observation["timestamps"].update(camera_timestamps)

        # 如果没有相机时间戳，使用bundle时间戳作为参考
        if not camera_timestamps:
            logger.warning("未找到相机时间戳，使用bundle时间戳作为参考")
            for camera_name in self.supported_cameras:
                camera_timestamps[camera_name] = current_timestamp

        for camera_name in self.supported_cameras:
            if camera_name not in camera_timestamps:
                logger.warning(f"相机 {camera_name} 没有对应的时间戳，跳过")
                continue

            try:
                start_time = time.time()
                target_timestamp = camera_timestamps[camera_name]

                while time.time() - start_time < self.timeout:
                    camera_packet = self.camera.get_packet_nearest(camera_name, target_timestamp)
                    if camera_packet is None:
                        time.sleep(0.001)  # 短暂等待
                        continue
                    delta_time = abs(camera_packet.source_timestamp - target_timestamp)
                    # logger.debug(f"相机 {camera_name} 时间差: {delta_time}ns")
                    if delta_time > 1e6:  # 1ms in nanoseconds
                        time.sleep(0.001)  # 短暂等待
                        continue
                    else:
                        if self.image_decode:
                            observation["images"].setdefault(camera_name, packet_to_image(camera_packet))
                        else:  # 不进行图像解码，直接返回原始数据包
                            observation["images"].setdefault(camera_name, packet_to_dict(camera_packet))
                        # logger.debug(f"成功获取相机 {camera_name} 图像，时间差: {delta_time}ns")
                        break
                else:
                    raise TimeoutError(f"获取相机 {camera_name} 图像超时")

            except Exception as e:
                logger.error(f"获取相机 {camera_name} 图像出错: {e}")

        return STD_MODEL_INPUT(observation=observation)

    def _extract_camera_timestamps(self, bundle_data_dict: dict) -> dict[str, int]:
        """从bundle_data_dict中提取相机时间戳"""
        camera_map = {
            "timestamp__camera__head_color": "head",
            "timestamp__camera__head_depth": "head_depth",
            "timestamp__camera__hand_left_color": "hand_left",
            "timestamp__camera__hand_right_color": "hand_right",
            "timestamp__camera__hand_left_depth": "hand_left_depth",
            "timestamp__camera__hand_right_depth": "hand_right_depth",
            "timestamp__camera__head_center_fisheye": "head_center_fisheye",
            "timestamp__camera__hand_left_fisheye": "hand_left_fisheye",
            "timestamp__camera__hand_right_fisheye": "hand_right_fisheye",
            "timestamp__camera__head_left_fisheye": "head_left_fisheye",
            "timestamp__camera__head_right_fisheye": "head_right_fisheye",
            "timestamp__camera__back_left_fisheye": "back_left_fisheye",
            "timestamp__camera__back_right_fisheye": "back_right_fisheye",
        }
        camera_timestamps = {}

        # 遍历bundle_data_dict，查找在camera_map中的键
        for key, value in bundle_data_dict.items():
            if key in camera_map:
                # 提取相机名称，例如 timestamp__camera__head_color -> head_color
                camera_name = camera_map[key]

                # 处理numpy数组格式的时间戳
                if hasattr(value, "tolist"):
                    timestamp = value.tolist()[0] if len(value) > 0 else None
                else:
                    timestamp = value[0] if isinstance(value, list | tuple) and len(value) > 0 else value

                if timestamp is not None:
                    camera_timestamps[camera_name] = int(timestamp)

        return camera_timestamps

    def _bundle_to_states(self, bundle_data_dict: dict) -> States:
        # 字段映射配置
        field_mapping = {
            "state__joint__position": "arm_joint_states",
            "state__head__position": "head_joint_states",
            "state__waist__position": "waist_joint_states",
            "state__joint__current_value": "joint_current_values",
            "state__end__wrench": "end_wrench_states",
            "state__end__velocity": "end_velocity",
            "state__robot__position": "robot_position",
            "state__robot__orientation": "robot_orientation",
            "state__copilot_state": "copilot_state",
            "state__copilot_reward": "copilot_reward",
        }

        # VR数据字段映射
        vr_field_mapping = {
            "state__left_vr_button__key_one": "left_vr_button_key_one",
            "state__left_vr_button__key_two": "left_vr_button_key_two",
            "state__left_vr_button__hand_trig": "left_vr_button_hand_trig",
            "state__left_vr_button__index_trig": "left_vr_button_index_trig",
            "state__left_vr_button__axis_x": "left_vr_button_axis_x",
            "state__left_vr_button__axis_y": "left_vr_button_axis_y",
            "state__left_vr_button__axis_click": "left_vr_button_axis_click",
            "state__right_vr_button__key_one": "right_vr_button_key_one",
            "state__right_vr_button__key_two": "right_vr_button_key_two",
            "state__right_vr_button__hand_trig": "left_vr_button_hand_trig",
            "state__right_vr_button__index_trig": "right_vr_button_index_trig",
            "state__right_vr_button__axis_x": "right_vr_button_axis_x",
            "state__right_vr_button__axis_y": "right_vr_button_axis_y",
            "state__right_vr_button__axis_click": "right_vr_button_axis_click",
        }

        # 处理基本字段映射
        states_data = {}
        for bundle_key, states_key in field_mapping.items():
            if bundle_key in bundle_data_dict:
                value = bundle_data_dict[bundle_key]
                if hasattr(value, "tolist"):
                    states_data[states_key] = value.tolist()
                else:
                    states_data[states_key] = str(value) if isinstance(value, int | float) else value

        # 处理末端执行器数据 - 合并左右夹爪到effector_states
        effector_states = []
        left_effector_key = "state__left_effector__position"
        right_effector_key = "state__right_effector__position"

        if left_effector_key in bundle_data_dict:
            left_effector_pos = bundle_data_dict[left_effector_key].tolist()
            effector_states.extend(left_effector_pos)

        if right_effector_key in bundle_data_dict:
            right_effector_pos = bundle_data_dict[right_effector_key].tolist()
            effector_states.extend(right_effector_pos)

        if len(effector_states) == 2:
            states_data["gripper_states"] = effector_states
        else:
            states_data["hand_joint_states"] = effector_states

        # 处理VR数据
        vr_data = None
        vr_data_dict = {}

        # 检查是否有VR相关的键
        has_vr_data = any(key in bundle_data_dict for key in vr_field_mapping)

        if has_vr_data:
            for bundle_key, vr_key in vr_field_mapping.items():
                if bundle_key in bundle_data_dict:
                    value = bundle_data_dict[bundle_key]
                    if hasattr(value, "tolist"):
                        vr_data_dict[vr_key] = value.tolist()[0]
                    else:
                        vr_data_dict[vr_key] = value[0]

            if vr_data_dict:
                vr_data = VrData(**vr_data_dict)

        states_data["vr_data"] = vr_data

        # cal FK to create end_pose
        states_data["end_pose"] = {
            "base_link": self.fk_solver.get_eef_pos(
                states_data["waist_joint_states"],
                states_data["head_joint_states"],
                states_data["arm_joint_states"][:7],
                states_data["arm_joint_states"][7:],
                base_link="base_link",
            ),
            "arm_base_link": self.fk_solver.get_eef_pos(
                states_data["waist_joint_states"],
                states_data["head_joint_states"],
                states_data["arm_joint_states"][:7],
                states_data["arm_joint_states"][7:],
                base_link="arm_base_link",
            ),
        }

        # 创建States对象
        states = States(**states_data)

        return states

    def convert_vr_control_to_action(self, vr_control) -> Action:
        """将VR控制转换为动作"""

        # 假设vr_control是ReactiveControl protobuf消息
        # 这里需要根据实际的protobuf库来解析数据

        # 提取时间戳
        timestamps = 0
        if hasattr(vr_control, "header") and vr_control.header:
            timestamps = vr_control.header.stamp.sec * 1000000000 + vr_control.header.stamp.nanosec

        # 设置轨迹参考时间
        trajectory_reference_time = 1.0
        if hasattr(vr_control, "lifetime") and vr_control.lifetime:
            trajectory_reference_time = vr_control.lifetime

        # 初始化动作数据
        action_data = {
            "timestamps": timestamps,
            "trajectory_reference_time": trajectory_reference_time,
            "base_link": "base_link",
            "left_arm": None,
            "right_arm": None,
            "head": None,
            "waist": None,
            "left_effector": None,
            "right_effector": None,
            "wheels": None,
        }

        # 处理每个控制组
        if hasattr(vr_control, "retarget_groups") and vr_control.retarget_groups:
            for retarget_info in vr_control.retarget_groups:
                group_id = retarget_info.group_id
                control_type = retarget_info.control_type

                # 处理左右臂控制（支持4种控制方式）
                if group_id in [2, 3]:  # GROUP_LEFT_ARM or GROUP_RIGHT_ARM
                    arm_field = "left_arm" if group_id == 2 else "right_arm"
                    self._process_arm_control(retarget_info, control_type, action_data, arm_field)

                # 处理其他关节控制（只有绝对关节角）
                elif group_id in [0, 1, 4, 5, 6, 7]:  # 头部、夹爪、腰部
                    if control_type == 3:  # 绝对关节角
                        self._process_joint_control(retarget_info, group_id, action_data)

        # 创建Action对象
        action = Action(**action_data)
        return action

    def _process_arm_control(self, retarget_info, control_type, action_data, arm_field):
        """处理左右臂控制（支持4种控制方式）"""
        if control_type == 0:  # 末端绝对位姿
            if hasattr(retarget_info, "target_frame_poses") and retarget_info.target_frame_poses:
                action_data["base_link"] = retarget_info.frame_id
                poses = []
                for pose in retarget_info.target_frame_poses:
                    position = [pose.position.x, pose.position.y, pose.position.z]
                    orientation = [
                        pose.orientation.x,
                        pose.orientation.y,
                        pose.orientation.z,
                        pose.orientation.w,
                    ]
                    # euler_angles = self._quaternion_to_euler(orientation)
                    pose_vector = position + orientation
                    poses.append(pose_vector)
                action_data[arm_field] = ArmEEFAbs(kind="EEF_ABS", values=poses)

        elif control_type == 1:  # 末端相对位姿
            if hasattr(retarget_info, "target_frame_poses_delta") and retarget_info.target_frame_poses_delta:
                action_data["base_link"] = retarget_info.frame_id
                poses_delta = []
                for pose in retarget_info.target_frame_poses_delta:
                    position = [pose.position.x, pose.position.y, pose.position.z]
                    orientation = [
                        pose.orientation.x,
                        pose.orientation.y,
                        pose.orientation.z,
                        pose.orientation.w,
                    ]
                    # euler_angles = self._quaternion_to_euler(orientation)
                    pose_vector = position + orientation
                    poses_delta.append(pose_vector)
                action_data[arm_field] = ArmEEFDelta(kind="EEF_DELTA", values=poses_delta)

        elif control_type == 3:  # 绝对关节角
            if hasattr(retarget_info, "target_joint_positions") and retarget_info.target_joint_positions:
                joint_positions = []
                for joint_pos in retarget_info.target_joint_positions:
                    if len(joint_pos) >= 7:
                        joint_positions.append(joint_pos[:7])
                    else:
                        padded_pos = joint_pos + [0.0] * (7 - len(joint_pos))
                        joint_positions.append(padded_pos)
                action_data[arm_field] = ArmJointAbs(kind="JOINT_ABS", values=joint_positions)

        elif control_type == 4:  # 相对关节角
            if hasattr(retarget_info, "target_joint_positions_delta") and retarget_info.target_joint_positions_delta:
                joint_deltas = []
                for joint_delta in retarget_info.target_joint_positions_delta:
                    if len(joint_delta) >= 7:
                        joint_deltas.append(joint_delta[:7])
                    else:
                        padded_delta = joint_delta + [0.0] * (7 - len(joint_delta))
                        joint_deltas.append(padded_delta)
                action_data[arm_field] = ArmJointDelta(kind="JOINT_DELTA", values=joint_deltas)

    def _process_joint_control(self, retarget_info, group_id, action_data):
        """处理其他关节控制（只有绝对关节角）"""
        if not (hasattr(retarget_info, "target_joint_positions") and retarget_info.target_joint_positions):
            return

        # 提取关节值
        joint_values = []
        for joint_pos in retarget_info.target_joint_positions:
            joint_values.append([joint_pos] if isinstance(joint_pos, int | float) else joint_pos)

        if group_id == 4:  # GROUP_LEFT_TOOL (左末端执行器)
            action_data["left_effector"] = joint_values

        elif group_id == 5:  # GROUP_RIGHT_TOOL (右末端执行器)
            action_data["right_effector"] = joint_values

        elif group_id == 0:  # GROUP_HEAD_YAW
            action_data["head"] = joint_values

        elif group_id == 1:  # GROUP_HEAD_PITCH
            if action_data["head"] is not None:
                # 与yaw合并
                combined_head = []
                for i in range(max(len(action_data["head"]), len(joint_values))):
                    yaw_val = action_data["head"][i] if i < len(action_data["head"]) else [0.0]
                    pitch_val = joint_values[i] if i < len(joint_values) else [0.0]
                    combined_head.append(yaw_val + pitch_val)
                action_data["head"] = combined_head
            else:
                # 只有pitch数据
                action_data["head"] = [[0.0] + val for val in joint_values]

        elif group_id == 6:  # GROUP_WAIST_LIFT
            action_data["waist"] = joint_values

        elif group_id == 7:  # GROUP_WAIST_PITCH
            if action_data["waist"] is not None:
                # 与lift合并
                combined_waist = []
                for i in range(max(len(action_data["waist"]), len(joint_values))):
                    lift_val = action_data["waist"][i] if i < len(action_data["waist"]) else [0.0]
                    pitch_val = joint_values[i] if i < len(joint_values) else [0.0]
                    combined_waist.append(lift_val + pitch_val)
                action_data["waist"] = combined_waist
            else:
                # 只有pitch数据
                action_data["waist"] = [[0.0] + val for val in joint_values]

    def _quaternion_to_euler(self, quaternion):
        """将四元数转换为欧拉角 (rx, ry, rz)"""
        euler_array = R.from_quat(quaternion).as_euler("xyz", degrees=False)  # [N, 3]
        return euler_array.tolist()  # 转换为list

    def shutdown(self):
        """关闭策略资源"""
        if self.camera:
            self.camera.close()
