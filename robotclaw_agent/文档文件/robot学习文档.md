##使用流程

可以按“三个阶段”使用：先起后端服务，再起 GenieSim 仿真，最后下发任务。

  推荐第一次运行流程

  开第一个终端：

  cd /home/easyai/桌面/shisheng
  bash scripts/01_before_app.sh

  这个会启动 manual policy server 和临时 gRPC server。

  开第二个终端启动仿真，这个命令会一直占用终端：

  cd /home/easyai/桌面/shisheng
  bash start_abs_pose_sim.sh

  等仿真窗口/日志里进入等待状态后，回到第一个终端执行：

  cd /home/easyai/桌面/shisheng
  bash scripts/02_after_app_init.sh

  bash scripts/03_run_grpc_command.sh status

  如果状态里 phase 是 running，就可以开始发任务。
  抓取一个方块并放到另一个方块上，例如红块放到黄块：

  SOURCE_COLOR=red TARGET_COLOR=yellow bash run_abs_pose_red_block.sh

  这个序列会执行：红块放黄块上，绿块到 zone 1，紫块到 zone 2，蓝块到 zone 2。

  常用调试命令

  bash scripts/03_run_grpc_command.sh status
  bash scripts/03_run_grpc_command.sh reset
  bash scripts/03_run_grpc_command.sh cameras
  bash scripts/03_run_grpc_command.sh localize-red --camera right

  结束后清理临时服务：

  bash scripts/grpc_red_block.sh cleanup

  关键点：start_abs_pose_sim.sh 必须单独开一个终端跑，因为它负责启动 GenieSim；其他命令通过 gRPC 和 .geniesim_bridge 给仿真里的机器人发动作。

  