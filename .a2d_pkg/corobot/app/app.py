#!/usr/bin/env python3
# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
CoRobot主应用程序 - 管理PolicyTask的动态加载和API暴露
"""

import importlib
import logging
import sys
import threading
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, jsonify, request
from flask_cors import CORS

from corobot.policy_tasks.policy_task_base import PolicyTaskBase
from corobot.utils.api_decorators import create_flask_handler
from corobot.utils.dds_setting import dds_env_set
from corobot.utils.log_setting import CoLogger as logger

# 禁用Flask默认日志
logging.getLogger("werkzeug").setLevel(logging.ERROR)


class CoRobotApp:
    """po
    CoRobot主应用程序

    职责:
    1. 管理PolicyTask的生命周期
    2. 动态加载PolicyTask
    3. 暴露PolicyTask的API接口
    4. 提供系统管理接口
    """

    def __init__(self, app_config_path: str):
        """
        初始化应用程序

        Args:
            app_config_path: 应用配置文件路径
        """
        self.app_config_path = app_config_path
        self.app_config = self._load_app_config()

        logger.log_level_set(self.app_config)

        # Flask应用
        self.flask_app = Flask(__name__)
        CORS(self.flask_app)

        # 当前PolicyTask
        self.current_policytask: PolicyTaskBase | None = None
        self.current_policytask_info: dict[str, str] = {}

        # 状态
        self.app_status = "idle"  # idle, loading, running, error
        self.last_error = ""

        # 设置系统API路由
        self._setup_system_routes()

        # 加载默认PolicyTask
        self._load_default_policytask()

    def _load_app_config(self) -> dict[str, Any]:
        """加载应用配置"""
        try:
            config_file = Path(self.app_config_path)
            if not config_file.exists():
                raise FileNotFoundError(f"应用配置文件不存在: {self.app_config_path}")

            with open(config_file, encoding="utf-8") as f:
                config = yaml.safe_load(f)

            return config or {}

        except Exception as e:
            logger.error(f"加载应用配置失败: {e}")
            return {"app": {"host": "0.0.0.0", "port": 8765}, "default_policytask": None}

    def _setup_system_routes(self):
        """设置系统管理API路由"""

        @self.flask_app.route("/system/status", methods=["GET"])
        def system_status():
            """获取系统状态"""
            status_info = {
                "app_status": self.app_status,
                "current_policytask": self.current_policytask_info,
                "last_error": self.last_error,
            }

            if self.current_policytask:
                status_info["policytask_status"] = self.current_policytask.get_status()

            return jsonify({"success": True, "data": status_info})

        @self.flask_app.route("/system/load_policytask", methods=["POST"])
        def load_policytask():
            """动态加载PolicyTask"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({"success": False, "error": "请求体不能为空"}), 400

                policy_module = data.get("policy_module")
                class_name = data.get("class_name")
                config_path = data.get("config_path")

                if not all([policy_module, class_name, config_path]):
                    return jsonify(
                        {
                            "success": False,
                            "error": "缺少必需参数: policy_module, class_name, config_path",
                        }
                    ), 400

                success = self._load_policytask(policy_module, class_name, config_path)

                if success:
                    return jsonify({"success": True, "message": "PolicyTask加载成功"})
                else:
                    return jsonify({"success": False, "error": self.last_error}), 500

            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @self.flask_app.route("/system/start_policytask", methods=["POST"])
        def start_policytask():
            """启动当前PolicyTask"""
            try:
                if not self.current_policytask:
                    return jsonify({"success": False, "error": "没有可用的PolicyTask"}), 400

                if self.current_policytask.is_running():
                    return jsonify({"success": False, "error": "PolicyTask已经在运行中"}), 400

                # 在新线程中启动
                def start_task():
                    try:
                        self.current_policytask.start()
                        self.app_status = "running"
                    except Exception as e:
                        self.app_status = "error"
                        self.last_error = str(e)

                thread = threading.Thread(target=start_task, daemon=True)
                thread.start()

                return jsonify({"success": True, "message": "PolicyTask启动中..."})

            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @self.flask_app.route("/system/stop_policytask", methods=["POST"])
        def stop_policytask():
            """停止当前PolicyTask"""
            try:
                if not self.current_policytask:
                    return jsonify({"success": False, "error": "没有可用的PolicyTask"}), 400

                self.current_policytask.stop()
                self.app_status = "idle"

                return jsonify({"success": True, "message": "PolicyTask已停止"})

            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @self.flask_app.route("/system/reset_policytask", methods=["POST"])
        def reset_policytask():
            """重置当前PolicyTask"""
            try:
                if not self.current_policytask:
                    return jsonify({"success": False, "error": "没有可用的PolicyTask"}), 400

                self.current_policytask.reset()
                self.app_status = "idle"

                return jsonify({"success": True, "message": "PolicyTask已重置"})

            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    def _load_default_policytask(self):
        """加载默认PolicyTask"""
        default_config = self.app_config.get("default_policytask")
        if not default_config:
            logger.error("未配置默认PolicyTask")
            return

        policy_module = default_config.get("policy_module")
        class_name = default_config.get("class_name")
        config_path = default_config.get("config_path")

        if all([policy_module, class_name, config_path]):
            logger.info(f"加载默认PolicyTask: {class_name}")
            self._load_policytask(policy_module, class_name, config_path)

    def _load_policytask(self, policy_module: str, class_name: str, config_path: str) -> bool:
        """
        动态加载PolicyTask

        Args:
            policy_module: 模块路径
            class_name: 类名
            config_path: 配置文件路径

        Returns:
            bool: 加载是否成功
        """
        try:
            self.app_status = "loading"
            self.last_error = ""
            config_path = str(Path(config_path).expanduser())

            # 1. 停止并清理旧的PolicyTask
            if self.current_policytask:
                logger.info("停止旧的PolicyTask...")
                try:
                    self.current_policytask.stop()
                    self.current_policytask.cleanup()
                except Exception as e:
                    logger.error(f"清理旧PolicyTask时出错: {e}")

                # 清理旧的API路由
                self._cleanup_policytask_routes()

            # 2. 动态导入模块
            logger.info(f"导入模块: {policy_module}")
            if policy_module in sys.modules:
                # 重新加载模块以获取最新代码
                module = importlib.reload(sys.modules[policy_module])
            else:
                module = importlib.import_module(policy_module)

            # 3. 获取PolicyTask类
            if not hasattr(module, class_name):
                raise AttributeError(f"模块 {policy_module} 中未找到类 {class_name}")

            policytask_class: type[PolicyTaskBase] = getattr(module, class_name)

            # 4. 检查是否继承自PolicyTaskBase
            if not issubclass(policytask_class, PolicyTaskBase):
                raise TypeError(f"类 {class_name} 必须继承自 PolicyTaskBase")

            # 5. 创建实例
            logger.info(f"创建PolicyTask实例: {class_name}")
            self.current_policytask = policytask_class(config_path)

            # 6. 初始化
            logger.info("初始化PolicyTask...")
            if not self.current_policytask.initialize():
                logger.warning("PolicyTask初始化失败")

            # 7. 注册API路由
            self._register_policytask_routes()

            # 8. 更新状态
            self.current_policytask_info = {
                "policy_module": policy_module,
                "class_name": class_name,
                "config_path": config_path,
            }
            self.app_status = "idle"

            logger.info(f"PolicyTask加载成功: {class_name}")
            return True

        except Exception as e:
            self.app_status = "error"
            self.last_error = str(e)
            logger.error(f"加载PolicyTask失败: {e}")
            return False

    def _register_policytask_routes(self):
        """注册PolicyTask的API路由"""
        if not self.current_policytask:
            return

        exposed_apis = self.current_policytask.get_exposed_apis()
        logger.info(f"注册 {len(exposed_apis)} 个API接口")

        for api_info in exposed_apis:
            path = api_info["path"]
            method = api_info["method"]

            # 创建Flask处理器
            handler = create_flask_handler(self.current_policytask, api_info)

            # 注册路由
            self.flask_app.add_url_rule(
                path,
                endpoint=f"policytask_{api_info['method_name']}",
                view_func=handler,
                methods=[method],
            )

            logger.info(f"  注册API: {method} {path}")

    def _cleanup_policytask_routes(self):
        """清理PolicyTask的API路由"""
        # 移除以policytask_开头的endpoint
        rules_to_remove = []
        for rule in self.flask_app.url_map.iter_rules():
            if rule.endpoint and rule.endpoint.startswith("policytask_"):
                rules_to_remove.append(rule)

        for rule in rules_to_remove:
            self.flask_app.url_map._rules.remove(rule)
            if rule.endpoint in self.flask_app.view_functions:
                del self.flask_app.view_functions[rule.endpoint]

    def run(self, debug: bool = False):
        """运行Flask应用"""
        app_config = self.app_config.get("app", {})
        host = app_config.get("host", "0.0.0.0")
        port = app_config.get("port", 8765)

        logger.info(f"启动CoRobot应用服务器: http://{host}:{port}")
        logger.info("系统API:")
        logger.info("  GET  /system/status")
        logger.info("  POST /system/load_policytask")
        logger.info("  POST /system/start_policytask")
        logger.info("  POST /system/stop_policytask")
        logger.info("  POST /system/reset_policytask")

        if self.current_policytask:
            logger.info("PolicyTask API:")
            for api_info in self.current_policytask.get_exposed_apis():
                logger.info(f"  {api_info['method']} {api_info['path']}")

        try:
            self.flask_app.run(host=host, port=port, debug=debug, use_reloader=False)
        except KeyboardInterrupt:
            logger.info("\n正在关闭应用...")
            self._shutdown()

    def _shutdown(self):
        """应用关闭处理"""
        if self.current_policytask:
            try:
                logger.info("停止PolicyTask...")
                self.current_policytask.stop()
                self.current_policytask.cleanup()
            except Exception as e:
                logger.error(f"关闭PolicyTask时出错: {e}")


def main():
    """主函数"""
    import argparse

    dds_env_set()
    parser = argparse.ArgumentParser(description="CoRobot应用服务器")
    parser.add_argument(
        "--config",
        type=str,
        default="~/.cache/agibot/corobot/app_config.yml",
        help="应用配置文件路径",
    )
    parser.add_argument("--debug", action="store_true", help="调试模式")

    args = parser.parse_args()
    args.config = str(Path(args.config).expanduser())
    app = CoRobotApp(args.config)
    app.run(debug=args.debug)


if __name__ == "__main__":
    main()
