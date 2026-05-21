import os
import sys
import time
from pathlib import Path

import h5py
import numpy as np
import yaml

from corobot.envs.g01_env import G01Env
from corobot.protocol.protocol_schemas import Action, ArmJointAbs
from corobot.utils.dds_setting import dds_env_set
from corobot.utils.log_setting import CoLogger as logger


class ArmReplayPolicy:
    def __init__(
        self,
        replay_config_path: str,
        env: G01Env,
        h5_file_path: str = None,
    ) -> None:
        if not os.path.exists(replay_config_path):
            raise FileNotFoundError(f"Replay config file not found: {replay_config_path}")
        with open(replay_config_path) as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
        replay_config = config["replay"]
        self.config = replay_config
        self.h5_file_path = h5_file_path if h5_file_path is not None else replay_config["h5_file_path"]
        self.action_chunk_size = replay_config["action_chunk_size"]
        self.action_shift = replay_config["action_shift"]
        self.action_source = replay_config["action_source"]  # action来源： state or action

        self.joint_dof = 7
        self.gripper_dof = 1
        self.logger = logger

        self.action_num = 0
        self.action_chunk_num = 0
        self.iter = 0
        self.actions = {}
        self.action_chunks = {}

        self.env = env

        self.setup()

    def setup(self) -> None:
        self.iter = 0
        self.actions = {}

        with h5py.File(self.h5_file_path, "r") as fid:
            if f"{self.action_source}/effector/position" in fid:
                effector_data = fid[f"{self.action_source}/effector/position"][:]
            elif (
                f"{self.action_source}/left_effector/position" in fid
                and f"{self.action_source}/right_effector/position" in fid
            ):
                effector_data = np.concatenate(
                    (
                        fid[f"{self.action_source}/left_effector/position"][:],
                        fid[f"{self.action_source}/right_effector/position"][:],
                    ),
                    axis=1,
                )
            else:
                raise ValueError(f"No effector data in given h5 file: {self.h5_file_path}!")

            self.actions["left_arm_abs_gripper"] = np.array(effector_data[:, : self.gripper_dof], dtype=np.float32)
            self.actions["right_arm_abs_gripper"] = np.array(effector_data[:, self.gripper_dof :], dtype=np.float32)

            self.actions["left_arm_abs_joint"] = np.array(
                fid[f"{self.action_source}/joint/position"][:, : self.joint_dof], dtype=np.float32
            )
            self.actions["right_arm_abs_joint"] = np.array(
                fid[f"{self.action_source}/joint/position"][:, self.joint_dof :], dtype=np.float32
            )

            self.action_num = self.actions["right_arm_abs_joint"].shape[0]
            self.logger.info(f"Total action num: {self.action_num}")
            self.action_chunk_num = (self.action_num - self.action_chunk_size) // self.action_shift + 1
            self.logger.info(f"Total action_chunk_num: {self.action_chunk_num}")

            self.timestamps = np.array(fid["timestamp"], dtype=np.int64)

        self.build_action_chunk(self.action_chunk_size, self.action_shift)

    def build_action_chunk(self, action_chunk_size: int, action_shift: int) -> None:
        index = 0
        left_arm_abs_gripper_chunk = []
        right_arm_abs_gripper_chunk = []
        left_arm_abs_joint_chunk = []
        right_arm_abs_joint_chunk = []
        for _ in range(self.action_chunk_num):
            left_arm_abs_gripper_chunk.append(self.actions["left_arm_abs_gripper"][index : index + action_chunk_size])
            right_arm_abs_gripper_chunk.append(self.actions["right_arm_abs_gripper"][index : index + action_chunk_size])
            left_arm_abs_joint_chunk.append(self.actions["left_arm_abs_joint"][index : index + action_chunk_size])
            right_arm_abs_joint_chunk.append(self.actions["right_arm_abs_joint"][index : index + action_chunk_size])
            index += action_shift

        self.action_chunks["left_arm_abs_gripper"] = np.array(left_arm_abs_gripper_chunk, dtype=np.float32)  # n,chunk,1
        self.action_chunks["right_arm_abs_gripper"] = np.array(
            right_arm_abs_gripper_chunk, dtype=np.float32
        )  # n,chunk,1
        self.action_chunks["left_arm_abs_joint"] = np.array(left_arm_abs_joint_chunk, dtype=np.float32)  # n,chunk,7
        self.action_chunks["right_arm_abs_joint"] = np.array(right_arm_abs_joint_chunk, dtype=np.float32)  # n,chunk,7

    def create_action(self, index: int) -> Action:
        if index > self.action_chunk_num or index < 0:
            raise ValueError(f"Index is out of range: {index} with action_chunk_num: {self.action_chunk_num}")

        left_arm_values = self.action_chunks["left_arm_abs_joint"][index]
        right_arm_values = self.action_chunks["right_arm_abs_joint"][index]
        left_effector_values = self.action_chunks["left_arm_abs_gripper"][index]
        right_effector_values = self.action_chunks["right_arm_abs_gripper"][index]
        action = Action()

        action.left_arm = ArmJointAbs(kind="JOINT_ABS", values=left_arm_values.tolist())
        action.right_arm = ArmJointAbs(kind="JOINT_ABS", values=right_arm_values.tolist())
        action.left_effector = left_effector_values.tolist()
        action.right_effector = right_effector_values.tolist()
        action.timestamps = (int)(time.time() * 1e9)
        action.trajectory_reference_time = 0.033 * len(left_arm_values)
        return action

    def reset(self) -> None:
        self.iter = 0
        self.env.reset()

    def act(self) -> None:
        if self.iter >= self.action_chunk_num:
            self.logger.info(
                f"Action chunk num is out of range: {self.iter} with action_chunk_num: {self.action_chunk_num}"
            )
            return False

        # 创建并执行动作
        action = self.create_action(self.iter)
        self.env.execute_action(action, wait_action_time=action.trajectory_reference_time)
        self.iter += 1

        # 打印美化后的进度条
        progress = (self.iter) / self.action_chunk_num
        bar_length = 30
        filled_length = int(bar_length * progress)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)

        # 计算百分比和当前进度
        percentage = progress * 100
        current = self.iter
        total = self.action_chunk_num

        # 使用 \r 来覆盖同一行，显示进度条
        print(
            f"\r[{'=' * 3} 执行进度 {'=' * 3}] [{bar}] {percentage:6.1f}% ({current:3d}/{total:3d})",
            end="",
            flush=True,
        )

        # 如果是最后一个动作，添加换行和完成消息
        if self.iter >= self.action_chunk_num:
            print()  # 换行
            self.logger.info("🎉 所有动作执行完成！")

        return True


def main():
    import argparse

    dds_env_set()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=str,
        default="~/.cache/agibot/corobot/replay.yml",
        help="replay config file path",
    )
    parser.add_argument("--h5file", type=str, help="h5 file path, use h5 file in replay config if not provided")
    args = parser.parse_args()
    args.config = str(Path(args.config).expanduser())
    env = G01Env(args.config)
    env.setup()
    policy = ArmReplayPolicy(args.config, env, args.h5file)

    print("\n🚀 开始执行机器人动作回放...")
    print(f"📊 总动作数: {policy.action_num}")
    print(f"📦 动作块大小: {policy.action_chunk_size}")
    print(f"🔄 动作块数量: {policy.action_chunk_num}")
    print(f"⏱️  预计执行时间: {policy.action_chunk_num * 0.033:.1f} 秒\n")

    try:
        while policy.act():
            pass
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断执行")
        policy.env.close()
        sys.exit(0)


if __name__ == "__main__":
    main()
