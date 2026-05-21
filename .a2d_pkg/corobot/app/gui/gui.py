#!/usr/bin/env python3
# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
GUI.
It can control the robot and display the camera image.
You can change the function of start_task, stop_task, reset_robot_posture for your own task.
"""

import argparse
import atexit
import os
import shutil
import subprocess
import threading
import time

import cv2
import gradio as gr
import numpy as np
import requests

from corobot.utils.dds_setting import dds_env_set
from corobot.utils.log_setting import CoLogger as logger
from corobot.utils.process_utils import check_port_using_ss

try:
    from a2d_sdk.robot import CosineCamera
except Exception as e:
    print(f"Error: {e}")
    print("please install a2d_sdk")
    exit(1)

dds_env_set()
url = "http://localhost:8765"
depth_visualization = True  # Add global variable to control depth map visualization

# Camera group displayed on the GUI
# Here shows all cameras, you can change it to your own camera group
# camera_names = [
#     "/camera/head_color",
#     "/camera/hand_left_color",
#     "/camera/hand_right_color",
#     "/camera/head_left_fisheye",
#     "/camera/head_right_fisheye",
#     "/camera/head_center_fisheye"
# ]
camera_names = ["/camera/head_color", "/camera/hand_left_color", "/camera/hand_right_color"]
camera_group = CosineCamera(camera_names)

CACHE_DIR = "/tmp/gradio_temp"
os.makedirs(CACHE_DIR, exist_ok=True)


@atexit.register
def cleanup():
    if os.path.exists(CACHE_DIR):
        shutil.rmtree(CACHE_DIR)
        print(f"✅ Cleaned temporary directory: {CACHE_DIR}")
        print("if you want to clean all tmp files, please run:")
        print("mount -t tmpfs -o size=2G tmpfs /tmp")
    cmd = "find /tmp/gradio/ -mindepth 1 -delete"
    os.system(cmd)


def apply_jet_colormap(depth_map, steps=4000, clip_range=(5, 95)):
    """Apply 4000-level Jet color mapping to uint16 depth map for better contrast"""
    min_val, max_val = np.percentile(depth_map, clip_range)
    depth_map = np.clip(depth_map, min_val, max_val)  # Clip abnormal values for better contrast

    if min_val == max_val:
        return np.zeros((*depth_map.shape, 3), dtype=np.uint8)

    normalized_depth = (depth_map - min_val) / (max_val - min_val)
    indices = (normalized_depth * (steps - 1)).astype(np.int32)
    colored_image = jet_colormap[indices]
    return colored_image


def generate_jet_colormap(steps=4000):
    base_colors = np.array(
        [
            [0, 0, 255],  # Deep blue
            [0, 255, 255],  # Cyan
            [255, 255, 0],  # Yellow
            [255, 0, 0],  # Red
            [50, 0, 0],  # Deep red
        ],
        dtype=np.uint8,
    )
    indices = np.linspace(0, 1, len(base_colors))
    colormap = np.zeros((steps, 3), dtype=np.uint8)
    for i in range(steps):
        t = i / (steps - 1)
        lower_idx = np.searchsorted(indices, t, side="right") - 1
        upper_idx = min(lower_idx + 1, len(base_colors) - 1)
        denom = indices[upper_idx] - indices[lower_idx]
        alpha = 0 if denom == 0 else (t - indices[lower_idx]) / denom
        colormap[i] = (1 - alpha) * base_colors[lower_idx] + alpha * base_colors[upper_idx]
    return colormap


jet_colormap = generate_jet_colormap()


def get_latest_image(camera_name):
    packet = camera_group.get_latest_packet(camera_name)
    if packet is not None and str(packet.type) == "PacketType.IMAGE":
        image_data = packet.get_image_data()
        if image_data is not None:
            encoding = str(packet.encoding_format)
            color_format = str(packet.color_format)
            width = int(packet.image_width)
            height = int(packet.image_height)

            try:
                if encoding in ["EncodingFormat.JPEG", "EncodingFormat.PNG"]:
                    frame = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
                    if frame is not None:
                        frame = cv2.resize(frame, (320, 256))
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        return frame
                    else:
                        print(f"Failed to decode {encoding} image")
                else:
                    if color_format == "ColorFormat.RS2_FORMAT_Z16":
                        image_depth = np.frombuffer(image_data, dtype=np.uint16)
                        frame = image_depth.reshape(height, width, 1)
                        frame = cv2.resize(frame, (320, 256))

                        if depth_visualization:  # Apply color mapping based on global variable
                            frame_view = apply_jet_colormap(frame)
                            return frame_view
                        else:
                            return frame
                    else:
                        # Convert RGB888 numpy array to BGR format
                        frame = image_data.reshape(height, width, 3)
                        frame = cv2.resize(frame, (320, 256))
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        return frame
            except Exception as e:
                print(f"Failed to process image: {e}")

    return np.zeros((320, 256, 3), dtype=np.uint8)  # Return black image as error case


def update_images():
    images = [get_latest_image(camera_name) for camera_name in camera_names]
    return images


def update_freq():
    freq = [f"{camera_group.get_fps(camera_name):.2f}" for camera_name in camera_names]
    return freq


def http_post(url, data):
    try:
        response = requests.post(url, json=data, headers={"Content-Type": "application/json"})
        logger.info(f"Request sent: {data}")
        logger.info(f"Received response: {response.json()}")
        return response.json()
    except Exception as e:
        logger.warning(f"Error sending request: {e}")
        return None


def send_system_request(endpoint, method="POST", data=None):
    """发送系统API请求"""
    global connection_error_logged

    try:
        if method == "GET":
            response = requests.get(f"{url}/system/{endpoint}")
        elif method == "POST":
            response = requests.post(
                f"{url}/system/{endpoint}",
                json=data or {},
                headers={"Content-Type": "application/json"},
            )

        # logger.info(f"Request sent to /system/{endpoint}: {data or {}}")
        response_data = response.json()
        # logger.info(f"Received response: {response_data}")

        # 如果连接成功，重置错误标志
        if connection_error_logged:
            connection_error_logged = False

        return response_data
    except Exception as e:
        # 只在第一次连接失败时打印错误
        if not connection_error_logged:
            logger.warning(f"Error sending request to /system/{endpoint}: {e}")
            connection_error_logged = True
        return None


def start_task():
    response = send_system_request("start_policytask")
    if response and response.get("success"):
        return response.get("message", "启动任务成功")
    else:
        error_msg = response.get("error") if response else "未知错误"
        return f"启动任务失败: {error_msg}"


def stop_task():
    response = send_system_request("stop_policytask")
    if response and response.get("success"):
        return response.get("message", "停止任务成功")
    else:
        error_msg = response.get("error") if response else "未知错误"
        return f"停止任务失败: {error_msg}"


def reset_robot_posture():
    """重置机器人姿态"""
    try:
        response = send_system_request("reset_policytask")
        if response and response.get("success"):
            return response.get("message", "重置机器人姿态成功")
        else:
            error_msg = response.get("error") if response else "未知错误"
            return f"重置机器人姿态失败: {error_msg}"
    except Exception as e:
        return f"重置机器人姿态失败: {str(e)}"


def get_status():
    response = send_system_request("status", method="GET")

    if response is None:
        return "Server is not exit", gr.update(elem_classes="status-error")

    # 获取系统状态和PolicyTask状态
    data = response.get("data", {})
    app_status = data.get("app_status", "unknown")
    policytask_status = data.get("policytask_status", {})
    policytask_running = policytask_status.get("running", False) if policytask_status else False

    if app_status == "running" or policytask_running:
        return "RUNNING", gr.update(elem_classes="status-success")
    elif app_status == "error":
        return "ERROR", gr.update(elem_classes="status-error")
    else:
        return "STOPPED", gr.update(elem_classes="status-warning")


# 全局状态变量
REMOTE_PASSWORD = "1"  # 需要设置实际的密码
REMOTE_HOST = "agi@10.42.0.101"  # 需要设置实际的主机地址
a2d_mode_status = "unknown"
mode_switching = False
mode_result = "就绪"
connection_error_logged = False  # 用于控制连接错误只打印一次


# mode switch and status
def check_a2d_mode_status():
    """定时检查A2D模式状态"""
    global a2d_mode_status
    # 获取当前环境变量并复制
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = ""

    # 远程配置文件路径
    remote_config_path = "/data/launcher/scene"

    try:
        # 使用sshpass从远程主机读取配置文件
        result = subprocess.run(
            [
                "sshpass",
                "-p",
                REMOTE_PASSWORD,
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                REMOTE_HOST,
                f"cat {remote_config_path}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )

        if result.returncode == 0:
            a2d_mode_status = result.stdout.split("/")[-1].split(".")[0]
        else:
            a2d_mode_status = "unknown"
    except Exception as e:
        logger.warning(f"检查机器人模式失败: {e}")
        a2d_mode_status = "unknown"

    return f"机器人模式: {a2d_mode_status}"


def check_mode_status():
    """定时检查模式切换状态"""
    global mode_switching, mode_result
    if mode_switching:
        return "⌛ 正在切换模式，请稍等...", gr.update(interactive=False, value="模式切换中...")
    else:
        return mode_result, gr.update(interactive=True, value="模式切换")


# 新的远程SSH模式切换函数
def remote_ssh_command(command):
    """通过SSH执行远程命令"""
    global REMOTE_PASSWORD, REMOTE_HOST
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = ""

    try:
        # 构建SSH命令
        ssh_cmd = ["sshpass", "-p", REMOTE_PASSWORD, "ssh", "-o", "StrictHostKeyChecking=no", REMOTE_HOST, command]

        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30, env=env)

        return result.returncode == 0, result.stdout, result.stderr

    except subprocess.TimeoutExpired:
        return False, "", "命令执行超时"
    except Exception as e:
        return False, "", f"SSH执行错误: {str(e)}"


def get_available_modes():
    """适配可用列表"""
    return ["idle", "rl", "copilot", "vr"]


def change_mode_handler(selected_mode=None):
    """通过远程SSH执行模式切换"""
    global mode_switching, mode_result

    if mode_switching:
        return "⌛ 模式正在切换中，请稍等...", gr.update(interactive=False, value="模式切换中...")

    if not selected_mode:
        return "❌ 请选择一个模式", gr.update(interactive=True, value="模式切换")

    mode_switching = True
    mode_result = f"⌛ 正在切换到 {selected_mode} 模式..."

    # 启动后台线程执行模式切换
    threading.Thread(target=change_mode_background, args=(selected_mode,), daemon=True).start()

    return mode_result, gr.update(interactive=False, value="模式切换中...")


def change_mode_background(selected_mode):
    """在后台执行远程模式切换"""
    global mode_result, mode_switching

    # 通过SSH执行模式切换命令
    # 这里需要根据您的实际远程命令进行调整
    command = f"source /home/agi/app/env.sh /home/agi/app && /home/agi/app/bin/scene_control switch {selected_mode}"

    success, stdout, stderr = remote_ssh_command(command)

    if success:
        mode_result = f"✅ 模式切换成功: {selected_mode}, 机器人程序启动需等待20s"
        logger.info(f"模式切换成功: {stdout}")
    else:
        mode_result = f"❌ 模式切换失败: {stderr}"
        logger.error(f"模式切换失败: {stderr}")

    # 添加冷却时间
    time.sleep(5)
    mode_switching = False


# Create Gradio interface
with gr.Blocks(
    delete_cache=(5, 2),
    css="""
    .button-active {
        background-color: #2ecc71 !important;
        border-color: #27ae60 !important;
    }
    .button-error {
        background-color: #e74c3c !important;
        border-color: #c0392b !important;
    }
    .custom-button:active {
        background-color: #3498db !important;
        transform: scale(0.98);
        transition: all 0.1s ease;
    }
    .status-output {
        font-size: 16px;
        padding: 10px;
        border-radius: 5px;
    }
    .status-success {
        background-color: #d4edda; /* Green background */
        color: #155724; /* Dark green text */
    }
    .status-error {
        background-color: #f8d7da; /* Red background */
        color: #721c24; /* Dark red text */
    }
    .status-warning {
        background-color: #fff3cd; /* Yellow background */
        color: #856404; /* Dark yellow text */
    }
""",
) as demo:
    timer = gr.Timer(0.1)
    status_timer = gr.Timer(1)
    upload_timer = gr.Timer(1)
    model_log_timer = gr.Timer(0.5)
    reflection_status_timer = gr.Timer(0.3)

    # Camera image display
    with gr.Row():
        image_outputs = [gr.Image(label=name.split("/")[-1]) for name in camera_names]

    # Frame rate display
    with gr.Row():
        freq_outputs = [gr.Text(label=f"{name.split('/')[-1]} fps") for name in camera_names]

    with gr.Row():
        force_reset_button = gr.Button("Reset robot posture", elem_classes="custom-button", variant="secondary")
        submit_button = gr.Button("Start task", elem_classes="custom-button")
        stop_button = gr.Button("Stop task", elem_classes="custom-button")
    with gr.Row():
        status_output = gr.Textbox(label="Task status")

    # Task status display
    submit_button.click(start_task, inputs=[], outputs=[])
    stop_button.click(stop_task, outputs=[])
    force_reset_button.click(reset_robot_posture, outputs=[status_output])

    # Timed update display content
    timer.tick(update_images, outputs=image_outputs)
    timer.tick(update_freq, outputs=freq_outputs)
    status_timer.tick(get_status, outputs=[status_output, status_output])

    # mode switch and display
    with gr.Row():
        mode_switch_row = {}
        with gr.Column(scale=2):
            mode_switch_row["a2d_mode_status"] = gr.Textbox(
                label="A2D模式状态",
                lines=2,  # 增加行数
                min_width=400,
                container=True,  # 添加容器
                scale=1,
            )
            mode_switch_row["mode_status"] = gr.Textbox(
                label="模式切换状态",
                lines=2,  # 增加行数
                min_width=400,
                container=True,  # 添加容器
                scale=1,
            )
            mode_switch_row["mode_selection"] = gr.Dropdown(
                label="选择模式",
                choices=get_available_modes(),
                type="value",
                min_width=400,
                container=True,  # 添加容器
                scale=1,
            )
        with gr.Column(scale=1):  # 第二列：切换按钮
            gr.Textbox(visible=False, scale=2)  # 减小空白区域的比例
            mode_switch_row["change_mode_button"] = gr.Button(
                "模式切换",
                variant="primary",
                scale=2,  # 减小比例
                min_width=200,  # 从400改为200
                size="lg",
                elem_classes=["custom-button"],
                elem_id="mode_switch_btn",
            )

    check_mode_status_timer = gr.Timer(0.2)
    check_a2d_mode_status_timer = gr.Timer(0.2)
    mode_switch_row["change_mode_button"].click(
        change_mode_handler,
        inputs=[mode_switch_row["mode_selection"]],
        outputs=[mode_switch_row["mode_status"], mode_switch_row["change_mode_button"]],
    )
    check_mode_status_timer.tick(
        check_mode_status, outputs=[mode_switch_row["mode_status"], mode_switch_row["change_mode_button"]]
    )
    check_a2d_mode_status_timer.tick(check_a2d_mode_status, outputs=[mode_switch_row["a2d_mode_status"]])


# Start Gradio interface
def main():
    parser = argparse.ArgumentParser(description="启动 Corobot GUI 应用")
    parser.add_argument("--port", type=int, default=7860, help="设置服务器端口 (默认: 7860)")
    args = parser.parse_args()

    try:
        if check_port_using_ss(args.port):
            print(f"❌ Port {args.port} is in use, please close the process using it.")
        else:
            demo.launch(server_name="0.0.0.0", server_port=args.port)
    except KeyboardInterrupt:
        cleanup()
        exit(0)


if __name__ == "__main__":
    main()
