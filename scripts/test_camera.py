from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import cv2

from agent_demo.machine_layer.dataloader_corobot import DataLoaderCoRobot


def _shape_text(image) -> str:
    return "None" if image is None else "x".join(str(v) for v in image.shape)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch robot camera frames through DataLoaderCoRobot.")
    parser.add_argument("--frames", type=int, default=3, help="Number of valid frames to capture.")
    parser.add_argument("--attempts", type=int, default=20, help="Maximum fetch attempts before failing.")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds to wait between attempts.")
    parser.add_argument("--output-dir", default="artifacts/test_camera", help="Directory for saved images.")
    parser.add_argument("--format", choices=("jpg", "jpeg", "png"), default="jpg", help="Saved image format.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    dataloader: DataLoaderCoRobot | None = None
    captured = 0
    try:
        dataloader = DataLoaderCoRobot(format=args.format, base_url="http://localhost:8765")
        print("DataLoaderCoRobot initialized")
        for attempt in range(1, args.attempts + 1):
            data = await dataloader.get_latest_concatenate_image_base64()
            if data is None:
                print(f"[{attempt}/{args.attempts}] no complete camera frame yet")
                time.sleep(args.interval)
                continue

            print(
                f"[{attempt}/{args.attempts}] frame_id={data.frame_id} ts={data.image_ts} "
                f"head={_shape_text(data.head_image)} "
                f"left={_shape_text(data.left_wrist_image)} "
                f"right={_shape_text(data.right_wrist_image)} "
                f"concat={_shape_text(data.concatenated_image)}"
            )

            if data.concatenated_image is not None:
                ext = ".jpg" if args.format in {"jpg", "jpeg"} else ".png"
                image_path = output_dir / f"frame_{data.frame_id:06d}{ext}"
                cv2.imwrite(str(image_path), data.concatenated_image)
                print(f"saved {image_path}")
                captured += 1

            if captured >= args.frames:
                break

            time.sleep(args.interval)
    finally:
        if dataloader is not None:
            dataloader.shutdown()

    if captured == 0:
        print("No complete camera frame was captured.", file=sys.stderr)
        return 1

    print(f"Captured {captured} frame(s) into {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
