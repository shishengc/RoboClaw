MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
LOCAL_PKG_DIR ?= $(MAKEFILE_DIR)/.a2d_pkg
COROBOT_WHL ?= ~/corobot-1.0.0.dev0+gui.mh.2-py3-none-any.whl
COROBOT_SITE_PACKAGES ?= /home/ck/miniconda3/envs/robot/lib/python3.10/site-packages
COROBOT_PYTHON ?= /home/ck/miniconda3/envs/robot/bin/python
APRILTAG_SITE_PACKAGES ?= /home/ck/miniconda3/envs/depth/lib/python3.10/site-packages
CAMERA_ARGS ?=
INFER_POLICY_ARGS ?=
DRYRUN_POLICY_ARGS ?=
DEBUG_POLICY_STEP_ARGS ?=
DEBUG_POLICY_RECEDING_CONTINUOUS_ARGS ?=
DEBUG_POLICY_RECEDING_CONTINUOUS_REVERSE_ARGS ?=
EXECUTE_ACTION_CHUNK_ARGS ?=
COROBOT_APP_ARGS ?=
ROBOCLAW_AGENT_TRACE ?= 0
ROBOCLAW_AGENT_TRACE_DIR ?= artifacts/agent_traces
ROBOCLAW_AGENT_TRACE_FILE ?=
ROBOCLAW_AGENT_TRACE_MAX_STRING ?= 4000
ROOT_PYTHON ?= 3.10
MCP_PYTHON ?= 3.12
BASIC_MEMORY_DIR := $(MAKEFILE_DIR)/src/mcp_server_demo/basic-memory
METASEARCH_DIR := $(MAKEFILE_DIR)/src/mcp_server_demo/metasearch-mcp
COROBOT_MCP_DIR := $(MAKEFILE_DIR)/src/mcp_server_demo/corobot_mcp_server
DATA_ANALYST_MCP_DIR := $(MAKEFILE_DIR)/src/mcp_server_demo/data_analyst_mcp_server

# 默认仅注入 src；本地扩展目录存在时自动加入；可选追加授权用户的 site-packages
COROBOT_ENV_LIB := $(if $(COROBOT_SITE_PACKAGES),$(patsubst %/lib/python$(ROOT_PYTHON)/site-packages,%/lib,$(COROBOT_SITE_PACKAGES)),)
LOCAL_CMEEL_SITE_PACKAGES := $(LOCAL_PKG_DIR)/cmeel.prefix/lib/python$(ROOT_PYTHON)/site-packages
COROBOT_CMEEL_SITE_PACKAGES := $(if $(COROBOT_SITE_PACKAGES),$(COROBOT_SITE_PACKAGES)/cmeel.prefix/lib/python$(ROOT_PYTHON)/site-packages,)
LOCAL_CMEEL_LIB := $(LOCAL_PKG_DIR)/cmeel.prefix/lib
COROBOT_CMEEL_LIB := $(if $(COROBOT_SITE_PACKAGES),$(COROBOT_SITE_PACKAGES)/cmeel.prefix/lib,)
PYTHONPATH_VALUE := $(MAKEFILE_DIR)/src$(if $(wildcard $(LOCAL_PKG_DIR)),:$(LOCAL_PKG_DIR),)$(if $(wildcard $(LOCAL_CMEEL_SITE_PACKAGES)),:$(LOCAL_CMEEL_SITE_PACKAGES),)$(if $(COROBOT_SITE_PACKAGES),:$(COROBOT_SITE_PACKAGES),)$(if $(wildcard $(APRILTAG_SITE_PACKAGES)),:$(APRILTAG_SITE_PACKAGES),)$(if $(wildcard $(COROBOT_CMEEL_SITE_PACKAGES)),:$(COROBOT_CMEEL_SITE_PACKAGES),)
PYENV := PYTHONPATH=$(PYTHONPATH_VALUE)
LD_LIBRARY_VALUE := $(if $(wildcard $(COROBOT_ENV_LIB)),$(COROBOT_ENV_LIB):,)/data/opencv45$(if $(wildcard $(LOCAL_CMEEL_LIB)),:$(LOCAL_CMEEL_LIB),)$(if $(wildcard $(COROBOT_CMEEL_LIB)),:$(COROBOT_CMEEL_LIB),):$$LD_LIBRARY_PATH
LD_LIBRARY := LD_LIBRARY_PATH=$(LD_LIBRARY_VALUE)
AGENT_TRACE_ENV := ROBOCLAW_AGENT_TRACE=$(ROBOCLAW_AGENT_TRACE) ROBOCLAW_AGENT_TRACE_DIR=$(ROBOCLAW_AGENT_TRACE_DIR) ROBOCLAW_AGENT_TRACE_FILE=$(ROBOCLAW_AGENT_TRACE_FILE) ROBOCLAW_AGENT_TRACE_MAX_STRING=$(ROBOCLAW_AGENT_TRACE_MAX_STRING)
CURRENT_TIME := $(shell date +"%Y-%m-%d_%H-%M")
UV_RUN_ROOT := uv run --python $(ROOT_PYTHON)
UV_RUN_COROBOT := uv run --python $(COROBOT_PYTHON)


init:
	mkdir -p ./applog/
	git submodule update --init --recursive
	uv python install $(ROOT_PYTHON) $(MCP_PYTHON)
	uv sync --frozen --python $(ROOT_PYTHON)
	$(UV_RUN_ROOT) pre-commit install
	uv --directory "$(BASIC_MEMORY_DIR)" sync --frozen --python $(MCP_PYTHON)
	uv --directory "$(METASEARCH_DIR)" sync --frozen --python $(MCP_PYTHON)
	uv --directory "$(COROBOT_MCP_DIR)" sync --python $(ROOT_PYTHON)
	uv --directory "$(DATA_ANALYST_MCP_DIR)" sync --python $(ROOT_PYTHON)

install_g01_whl:
	@test -n "$(COROBOT_WHL)" || (echo "请提供 COROBOT_WHL=/path/to/corobot-*.whl" && exit 1)
	@test -f "$(COROBOT_WHL)" || (echo "未找到 whl 文件: $(COROBOT_WHL)" && exit 1)
	mkdir -p "$(LOCAL_PKG_DIR)"
	uv pip install --target "$(LOCAL_PKG_DIR)" "$(COROBOT_WHL)"


test:
	$(PYENV) $(UV_RUN_ROOT) python ${MAKEFILE_DIR}tests/agent_demo/agent_layer/llm_manager/openai_client/test_openai_client.py

run:
	$(MAKE) run_tui

run_a2d:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}src/agent_demo/interaction_layer/cmd/olympus_img_cmd.py

run_gui:
	$(PYENV) $(UV_RUN_ROOT) python ${MAKEFILE_DIR}src/agent_demo/interaction_layer/gradio_ui/gradio_ui.py

run_tui:
	$(AGENT_TRACE_ENV) $(PYENV) $(UV_RUN_ROOT) python ${MAKEFILE_DIR}src/agent_demo/interaction_layer/tui/olympus_tui.py

run_tui_trace:
	$(MAKE) run_tui ROBOCLAW_AGENT_TRACE=1

run_corobot_app:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python -m corobot.app.app $(COROBOT_APP_ARGS)

check_corobot_runtime:
	$(LD_LIBRARY) $(PYENV) $(COROBOT_PYTHON) -c "import sys; print(sys.executable); import cv2; print('cv2', cv2.__file__); from pupil_apriltags import Detector; print('pupil_apriltags ok'); import corobot, a2d_sdk, mcp_control_demo; from corobot.policy_tasks.rule_control_task import RuleControlTask; print('RuleControlTask ok'); print('corobot runtime ok')"

test_img:
	$(PYENV) $(UV_RUN_ROOT) python ${MAKEFILE_DIR}tests/agent_demo/agent_layer/llm_manager/openai_client/test_img.py

test_mm:
	$(PYENV) $(UV_RUN_ROOT) python ${MAKEFILE_DIR}tests/agent_demo/agent_layer/memory_manager/test_memory_manager.py

test_a2d:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}tests/agent_demo/machine_layer/test_dataloader_a2d.py

test_camera:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}scripts/test_corobot_camera.py $(CAMERA_ARGS)

test_infer_policy:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}scripts/test_infer_policy.py $(INFER_POLICY_ARGS) $(DRYRUN_POLICY_ARGS)

debug_policy_step:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}scripts/debug_corobot_policy_step.py $(DEBUG_POLICY_STEP_ARGS)

debug_policy_receding_continuous:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}scripts/debug_corobot_policy_receding_continuous.py $(DEBUG_POLICY_RECEDING_CONTINUOUS_ARGS)

debug_policy_receding_continuous_reverse:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}scripts/debug_corobot_policy_receding_continuous_reverse.py $(DEBUG_POLICY_RECEDING_CONTINUOUS_REVERSE_ARGS)

execute_action_chunk:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}scripts/execute_corobot_action_chunk.py $(EXECUTE_ACTION_CHUNK_ARGS)

test_udp:
	$(PYENV) $(UV_RUN_ROOT) python ${MAKEFILE_DIR}tests/agent_demo/interaction_layer/test_udp.py

test_a2d_img:
	$(LD_LIBRARY) $(PYENV) $(UV_RUN_COROBOT) python ${MAKEFILE_DIR}tests/agent_demo/agent_layer/test_img_agent.py
