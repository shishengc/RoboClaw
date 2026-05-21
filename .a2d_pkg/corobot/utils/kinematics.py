import numpy as np
import pinocchio as pin
from scipy.spatial.transform import Rotation as R


class Kinematics:
    def __init__(self, urdf_path):
        # 加载URDF模型
        self.model = pin.buildModelFromUrdf(urdf_path)
        self.data = self.model.createData()

        # 设置关节限位
        self.bounds = []
        for i in range(2, 16):
            self.bounds.append((self.model.lowerPositionLimit[i], self.model.upperPositionLimit[i]))

        # 获取左右臂的关节名称
        self.left_joint_names = [f"Joint{i}_l" for i in range(1, 8)]
        self.right_joint_names = [f"Joint{i}_r" for i in range(1, 8)]

        # 获取左右臂的关节ID
        try:
            self.left_joint_ids = [self.model.getJointId(name) for name in self.left_joint_names]
        except Exception as e:
            print(f"错误: 找不到 'Joint{i}_l' 关节: {str(e)}")
            self.left_joint_ids = []
        try:
            self.right_joint_ids = [self.model.getJointId(name) for name in self.right_joint_names]
        except Exception as e:
            print(f"错误: 找不到 'Joint{i}_r' 关节: {str(e)}")
            self.right_joint_ids = []

        # 打印所有可用的 frame 名称和对应的 ID
        print("\n所有可用的 frames:")
        for frame_id in range(self.model.nframes):
            frame_name = self.model.frames[frame_id].name
            print(f"Frame {frame_id}: {frame_name}")

        # 获取基座和末端执行器的frame ID
        try:
            self.arm_base_frame_id = self.model.getFrameId("link-arm")
            print(f"\n基座 frame ID: {self.arm_base_frame_id}, 名称: {self.model.frames[self.arm_base_frame_id].name}")
        except Exception as e:
            print(f"错误: 找不到 'link-arm' frame: {str(e)}")
            self.arm_base_frame_id = None

        try:
            self.left_ee_frame_id = self.model.getFrameId("Link7_l")
            print(f"左臂末端 frame ID: {self.left_ee_frame_id}, 名称: {self.model.frames[self.left_ee_frame_id].name}")
        except Exception as e:
            print(f"错误: 找不到 'Link7_l' frame: {str(e)}")
            self.left_ee_frame_id = None

        try:
            self.right_ee_frame_id = self.model.getFrameId("Link7_r")
            print(
                f"右臂末端 frame ID: {self.right_ee_frame_id}, 名称: {self.model.frames[self.right_ee_frame_id].name}"
            )
        except Exception as e:
            print(f"错误: 找不到 'Link7_r' frame: {str(e)}")
            self.right_ee_frame_id = None

        try:
            self.head_frame_id = self.model.getFrameId("link-pitch_head")
            print(f"头 frame ID: {self.head_frame_id}, 名称: {self.model.frames[self.head_frame_id].name}")
        except Exception as e:
            print(f"错误: 找不到 'link-pitch_head' frame: {str(e)}")
            self.head_frame_id = None

        # 验证左右臂末端 frame ID 是否不同
        if self.left_ee_frame_id == self.right_ee_frame_id and self.left_ee_frame_id is not None:
            raise ValueError(f"左右臂末端 frame ID 相同 ({self.left_ee_frame_id})！请检查 URDF 文件中的 frame 名称。")

        self.pos_error_weight = 60.0
        self.ori_error_weight = 20.0
        self.vel_error_weight = 0.0
        self.max_iterations = 100000
        self.error_tol = 1e-9

    def update_pos_error_weight(self, weight):
        self.pos_error_weight = weight

    def update_ori_error_weight(self, weight):
        self.ori_error_weight = weight

    def update_vel_error_weight(self, weight):
        self.vel_error_weight = weight

    def update_max_iterations(self, max_iter):
        self.max_iterations = max_iter

    def update_error_tol(self, error_tol):
        self.error_tol = error_tol

    def rpy_to_quaternion(self, roll, pitch, yaw):
        # 使用 RPY 构造旋转
        r = R.from_euler("xyz", [roll, pitch, yaw], degrees=False)
        # 获取四元数，格式为 (x, y, z, w)
        quat = r.as_quat()
        return quat

    def quaternion_to_rpy(self, x, y, z, w):
        # 使用四元数创建旋转对象
        r = R.from_quat([x, y, z, w])
        # 将旋转对象转为欧拉角 (roll, pitch, yaw)
        rpy = r.as_euler("xyz", degrees=False)  # 'xyz' 指定顺序，返回弧度值
        return rpy

    def compute_arm_fk(self, arm_joint_angles, waist_pitch_value, waist_lift_value):
        if len(arm_joint_angles) != 14:
            raise ValueError(f"传入的关节角度数量 ({len(arm_joint_angles)}) 不是14")

        # Reuse pre-allocated zero vector instead of creating new one
        q = np.array(arm_joint_angles)

        waist_joints = np.array([waist_lift_value, waist_pitch_value])
        # head_joints = np.array([self.head_pitch_value, self.head_yaw_value])
        head_joints = np.zeros(2)
        total_q = np.concatenate([waist_joints, q, head_joints])

        # Compute FK only once and store results
        pin.forwardKinematics(self.model, self.data, total_q)
        pin.updateFramePlacements(self.model, self.data)

        # 获取左臂末端位姿
        left_pose = pin.SE3ToXYZQUAT(self.data.oMf[self.left_ee_frame_id])
        # left_pose_rpy = self.quaternion_to_rpy(*left_pose[3:])
        # left_pose = np.array([left_pose[0], left_pose[1], left_pose[2],
        #                       left_pose_rpy[0], left_pose_rpy[1], left_pose_rpy[2]])
        # 获取右臂末端位姿
        right_pose = pin.SE3ToXYZQUAT(self.data.oMf[self.right_ee_frame_id])
        # right_pose_rpy = self.quaternion_to_rpy(*right_pose[3:])
        # right_pose = np.array([right_pose[0], right_pose[1], right_pose[2],
        #                       right_pose_rpy[0], right_pose_rpy[1], right_pose_rpy[2]])
        return left_pose, right_pose

    def compute_arm_fk_wrt_arm_base(self, arm_joint_angles, waist_pitch_value, waist_lift_value):
        """
        计算左右臂末端在 arm base（link-arm）坐标系下的 FK。

        Args:
            arm_joint_angles (list/np.ndarray): 14 个关节角 [L1..L7, R1..R7]
            waist_pitch_value (float): 腰部 pitch 关节
            waist_lift_value (float): 腰部升降关节
            return_pose (bool): True 返回 [x,y,z, roll,pitch,yaw]；False 只返回 [x,y,z]

        Returns:
            left_rel, right_rel:
                - 若 return_pose=False：np.array(3,)
                - 若 return_pose=True ：np.array(6,)  (RPY 为弧度)
        """
        if len(arm_joint_angles) != 14:
            raise ValueError(f"传入的关节角度数量 ({len(arm_joint_angles)}) 不是14")

        if self.arm_base_frame_id is None:
            raise RuntimeError("未找到 arm base frame（'link-arm'）。请检查 URDF 或初始化日志。")

        # 拼完整的关节向量： [waist_lift, waist_pitch] + 14臂关节 + [head_yaw, head_pitch]
        q_arm = np.array(arm_joint_angles, dtype=float)
        q_waist = np.array([waist_lift_value, waist_pitch_value], dtype=float)
        q_head = np.zeros(2, dtype=float)  # 若需要也可改为真实头部关节
        total_q = np.concatenate([q_waist, q_arm, q_head])

        # FK
        pin.forwardKinematics(self.model, self.data, total_q)
        pin.updateFramePlacements(self.model, self.data)

        # 世界下位姿
        oT_arm = self.data.oMf[self.arm_base_frame_id]
        oT_left = self.data.oMf[self.left_ee_frame_id]
        oT_right = self.data.oMf[self.right_ee_frame_id]

        # 变换到 arm base 坐标系
        armT_left = oT_arm.inverse() * oT_left
        armT_right = oT_arm.inverse() * oT_right

        # 返回 [x,y,z, roll,pitch,yaw]
        left_xyz_quat = pin.SE3ToXYZQUAT(armT_left)
        right_xyz_quat = pin.SE3ToXYZQUAT(armT_right)

        # left_rpy = self.quaternion_to_rpy(*left_xyz_quat[3:])
        # right_rpy = self.quaternion_to_rpy(*right_xyz_quat[3:])

        # left_rel = np.array(
        #     [left_xyz_quat[0], left_xyz_quat[1], left_xyz_quat[2], left_rpy[0], left_rpy[1], left_rpy[2]]
        # )
        # right_rel = np.array(
        #     [right_xyz_quat[0], right_xyz_quat[1], right_xyz_quat[2], right_rpy[0], right_rpy[1], right_rpy[2]]
        # )
        return left_xyz_quat, right_xyz_quat

    def compute_gripper_fk(self, arm_joint_angles, waist_pitch_value, waist_lift_value, gripper_length=0.15):
        if len(arm_joint_angles) != 14:
            raise ValueError(f"传入的关节角度数量 ({len(arm_joint_angles)}) 不是14")

        # Reuse pre-allocated zero vector instead of creating new one
        q = np.array(arm_joint_angles)

        waist_joints = np.array([waist_lift_value, waist_pitch_value])
        # head_joints = np.array([self.head_pitch_value, self.head_yaw_value])
        head_joints = np.zeros(2)
        total_q = np.concatenate([waist_joints, q, head_joints])

        # Compute FK only once and store results
        pin.forwardKinematics(self.model, self.data, total_q)
        pin.updateFramePlacements(self.model, self.data)

        # 获取左臂末端位姿
        left_pose = pin.SE3ToXYZQUAT(self.data.oMf[self.left_ee_frame_id])
        x = left_pose[3]
        y = left_pose[4]
        z = left_pose[5]
        w = left_pose[6]
        left_rot = R.from_quat([x, y, z, w])
        left_world_axis = left_rot.apply(np.array([0, 0, 1]))
        left_gripper_pose = left_pose[:3] + gripper_length * left_world_axis
        left_pose_rpy = self.quaternion_to_rpy(*left_pose[3:])
        left_gripper_pose = np.array(
            [
                left_gripper_pose[0],
                left_gripper_pose[1],
                left_gripper_pose[2],
                left_pose_rpy[0],
                left_pose_rpy[1],
                left_pose_rpy[2],
            ]
        )
        # 获取右臂末端位姿
        right_pose = pin.SE3ToXYZQUAT(self.data.oMf[self.right_ee_frame_id])
        x = right_pose[3]
        y = right_pose[4]
        z = right_pose[5]
        w = right_pose[6]
        right_rot = R.from_quat([x, y, z, w])
        right_world_axis = right_rot.apply(np.array([0, 0, 1]))
        right_gripper_pose = right_pose[:3] + gripper_length * right_world_axis
        right_pose_rpy = self.quaternion_to_rpy(*right_pose[3:])
        right_gripper_pose = np.array(
            [
                right_gripper_pose[0],
                right_gripper_pose[1],
                right_gripper_pose[2],
                right_pose_rpy[0],
                right_pose_rpy[1],
                right_pose_rpy[2],
            ]
        )

        return left_gripper_pose, right_gripper_pose

    def compute_head_fk(self, head_yaw_value, head_pitch_value, waist_pitch_value, waist_lift_value):
        waist_joints = np.array([waist_lift_value, waist_pitch_value])
        head_joints = np.array([head_yaw_value, head_pitch_value])
        arm_joints = np.zeros(14)
        total_q = np.concatenate([waist_joints, arm_joints, head_joints])
        pin.forwardKinematics(self.model, self.data, total_q)
        pin.updateFramePlacements(self.model, self.data)
        head_pose = pin.SE3ToXYZQUAT(self.data.oMf[self.head_frame_id])
        return head_pose

    def compute_jacobian(self, joint_angles):
        """计算左右臂末端执行器的雅可比矩阵

        Args:
            joint_angles: 包含所有关节角度的数组

        Returns:
            left_jacobian: 左臂末端执行器的雅可比矩阵 (6xn)
            right_jacobian: 右臂末端执行器的雅可比矩阵 (6xn)
        """
        # 更新机器人状态
        q = np.array(joint_angles)
        pin.forwardKinematics(self.model, self.data, q)
        pin.updateFramePlacements(self.model, self.data)

        # 计算左臂末端执行器的雅可比矩阵
        left_jacobian = pin.computeFrameJacobian(
            self.model, self.data, q, self.left_ee_frame_id, pin.ReferenceFrame.LOCAL_WORLD_ALIGNED
        )

        # 计算右臂末端执行器的雅可比矩阵
        right_jacobian = pin.computeFrameJacobian(
            self.model, self.data, q, self.right_ee_frame_id, pin.ReferenceFrame.LOCAL_WORLD_ALIGNED
        )

        left_ee_pose = self.data.oMf[self.left_ee_frame_id]
        right_ee_pose = self.data.oMf[self.right_ee_frame_id]
        return left_jacobian, right_jacobian, left_ee_pose, right_ee_pose

    def compute_collision(self, arm_joint_angles):
        return False
