import argparse
import os
import shutil
import sys
import sysconfig
from pathlib import Path

try:
    from importlib.resources import files as pkg_files  # py3.9+
except Exception:  # pragma: no cover
    pkg_files = None  # type: ignore


def _find_default_config_dir() -> Path | None:
    """Locate default config directory shipped with the project/package.

    Priority:
    1) Project root "config" (editable/development installs)
    2) Package resource corobot/config (if present)
    3) System data dir: <sysconfig data>/corobot/config (if configured via data-files)
    """

    # 1) repo root: ../../.. from this file -> project root
    this_file = Path(__file__).resolve()
    candidates: list[Path] = []

    try:
        project_root = this_file.parents[3]
        candidates.append(project_root / "config")
    except Exception:
        pass

    # 2) inside package resources (corobot/config)
    if pkg_files is not None:
        try:
            pkg_conf = pkg_files("corobot").joinpath("config")  # type: ignore[attr-defined]
            candidates.append(Path(str(pkg_conf)))
        except Exception:
            pass

    # 3) installed data files location (if pyproject configured with tool.setuptools.data-files)
    try:
        data_dir = Path(sysconfig.get_paths()["data"]) / "corobot" / "config"
        candidates.append(data_dir)
    except Exception:
        pass

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _copy_configs(src_dir: Path, dst_dir: Path, overwrite: bool = False) -> list[Path]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []

    for item in sorted(src_dir.iterdir()):
        if item.is_file() and item.suffix.lower() in {".yml", ".yaml"}:
            target = dst_dir / item.name
            if target.exists() and not overwrite:
                continue
            shutil.copy2(item, target)
            copied.append(target)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize CoRobot default configs into cache directory"
    )
    parser.add_argument(
        "--dest",
        default=os.path.expanduser("~/.cache/agibot/corobot"),
        help="Destination directory (default: ~/.cache/agibot/corobot)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )
    args = parser.parse_args()

    dst_dir = Path(args.dest).expanduser()
    src_dir = _find_default_config_dir()

    if src_dir is None:
        print(
            "未找到默认配置目录。请确认安装包包含 config，或在项目根目录下运行。", file=sys.stderr
        )
        return 1

    copied = _copy_configs(src_dir, dst_dir, overwrite=bool(args.force))
    if not copied:
        print(f"没有文件被复制（可能已存在且未使用 --force）。目标目录: {dst_dir}")
    else:
        print(f"已复制 {len(copied)} 个配置文件到 {dst_dir}")
        for p in copied:
            print(f" - {p.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
