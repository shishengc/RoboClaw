# Copyright (c) 2023, AgiBot Inc.
# All rights reserved.

"""
API装饰器 - 用于标记需要暴露给外部的方法
"""

import functools
import inspect
from collections.abc import Callable

from flask import jsonify, request


def expose_api(method: str = "GET", path: str = None, **kwargs):
    """
    API暴露装饰器

    Args:
        method: HTTP方法 (GET, POST, PUT, DELETE等)
        path: API路径，如果不指定则使用函数名
        **kwargs: 其他参数

    Usage:
        @expose_api(method="POST", path="/set_prompt")
        def set_prompt(self, prompt: str):
            pass

        @expose_api(method="GET")  # 默认路径为 "/get_status"
        def get_status(self):
            pass
    """

    def decorator(func: Callable) -> Callable:
        # 确定API路径
        api_path = path
        if api_path is None:
            api_path = f"/{func.__name__}"

        # 确保路径以/开头
        if not api_path.startswith("/"):
            api_path = f"/{api_path}"

        # 存储API信息到函数属性
        func._exposed_api = {
            "method": method.upper(),
            "path": api_path,
            "original_func": func,
            **kwargs,
        }

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        # 保持API信息
        wrapper._exposed_api = func._exposed_api

        return wrapper

    return decorator


def create_flask_handler(policy_task_instance, api_info: dict) -> Callable:
    """
    为PolicyTask方法创建Flask处理器

    Args:
        policy_task_instance: PolicyTask实例
        api_info: API信息字典

    Returns:
        Flask处理器函数
    """
    handler_func = api_info["handler"]
    method = api_info["method"]

    def flask_handler():
        try:
            # 获取函数签名
            sig = inspect.signature(handler_func)
            params = {}

            # 处理不同HTTP方法的参数获取
            if method in ["POST", "PUT", "PATCH"]:
                # 从JSON body获取参数
                if request.is_json:
                    json_data = request.get_json() or {}
                    for param_name, param in sig.parameters.items():
                        if param_name == "self":
                            continue
                        if param_name in json_data:
                            params[param_name] = json_data[param_name]
                        elif param.default == inspect.Parameter.empty:
                            return jsonify(
                                {"success": False, "error": f"缺少必需参数: {param_name}"}
                            ), 400
                else:
                    return jsonify(
                        {"success": False, "error": "请求Content-Type必须是application/json"}
                    ), 400

            elif method == "GET":
                # 从URL参数获取
                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue
                    value = request.args.get(param_name)
                    if value is not None:
                        # 简单的类型转换
                        if isinstance(param.annotation, type) and param.annotation is int:
                            try:
                                params[param_name] = int(value)
                            except ValueError:
                                return jsonify(
                                    {"success": False, "error": f"参数 {param_name} 必须是整数"}
                                ), 400
                        elif isinstance(param.annotation, type) and param.annotation is float:
                            try:
                                params[param_name] = float(value)
                            except ValueError:
                                return jsonify(
                                    {"success": False, "error": f"参数 {param_name} 必须是浮点数"}
                                ), 400
                        elif isinstance(param.annotation, type) and param.annotation is bool:
                            params[param_name] = value.lower() in ("true", "1", "yes", "on")
                        else:
                            params[param_name] = value
                    elif param.default == inspect.Parameter.empty:
                        return jsonify(
                            {"success": False, "error": f"缺少必需参数: {param_name}"}
                        ), 400

            # 调用PolicyTask方法
            result = handler_func(**params)

            # 处理返回值
            if result is None:
                response = {"success": True}
            elif isinstance(result, dict):
                response = {"success": True, "data": result}
            elif isinstance(result, list | str | int | float | bool):
                response = {"success": True, "data": result}
            else:
                # 尝试序列化
                try:
                    response = {"success": True, "data": str(result)}
                except Exception:
                    response = {"success": True, "data": None}

            return jsonify(response)

        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # 设置函数名用于Flask路由
    flask_handler.__name__ = f"{api_info['method_name']}_handler"

    return flask_handler
