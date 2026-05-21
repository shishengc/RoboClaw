import argparse
import subprocess
import sys
from pathlib import Path


def _default_script_path() -> Path:
    # Prefer repository script path during development
    this_file = Path(__file__).resolve()
    try:
        project_root = this_file.parents[3]
        script = project_root / "scripts" / "install.sh"
        if script.exists():
            return script
    except Exception:
        pass

    # Fallback: use embedded logic matching scripts/install.sh
    return Path("scripts/install.sh")


def _run_shell(cmd: list[str]) -> int:
    try:
        proc = subprocess.run(cmd, check=False)
        return proc.returncode
    except FileNotFoundError:
        print("找不到可执行文件，请检查环境。", file=sys.stderr)
        return 127


def main() -> int:
    parser = argparse.ArgumentParser(description="Install GDK via project install script")
    parser.add_argument(
        "--useremote",
        action="store_true",
        default=True,
        help="忽略本地 scripts/install.sh，直接执行远程 curl 安装",
    )
    args = parser.parse_args()

    if args.useremote:
        # Directly mirror scripts/install.sh core command
        cmd = [
            "/usr/bin/bash",
            "-lc",
            "curl -sSL http://10.42.0.101:8849/install.sh | "
            "bash -s -- --no_update_bashrc --user gma",
        ]
        return _run_shell(cmd)

    script_path = _default_script_path()
    if script_path.exists():
        cmd = ["/usr/bin/bash", str(script_path)]
        return _run_shell(cmd)

    # If script not found (e.g., installed without scripts), fallback to remote
    print("未找到 scripts/install.sh，改为执行远程安装。")
    cmd = [
        "/usr/bin/bash",
        "-lc",
        "curl -sSL http://10.42.0.101:8849/install.sh | bash -s -- --no_update_bashrc --user gma",
    ]
    return _run_shell(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
