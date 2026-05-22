from __future__ import annotations

import math
import time
from typing import Any

import numpy as np


def extract_tag_position_camera(tag_pose: dict[str, Any]) -> list[float]:
    for key in ("position_camera_m", "translation_m"):
        if tag_pose.get(key) is not None:
            return [float(v) for v in tag_pose[key]]
    camera_pose = tag_pose.get("camera_pose") or {}
    if camera_pose.get("position_m") is not None:
        return [float(v) for v in camera_pose["position_m"]]
    raise ValueError("tag pose does not contain a camera-frame position")


class AprilTagPerceptionService:
    """Detect AprilTags from CoRobot observations and cache results by tag_id."""

    def __init__(
        self,
        *,
        camera_name: str = "head",
        camera_frame: str = "head_camera_optical",
        tag_family: str = "tag25h9",
        tag_size_m: float = 0.018,
        stale_after_s: float = 1.0,
        fallback_camera_params: dict[str, Any] | None = None,
    ):
        self.camera_name = camera_name
        self.camera_frame = camera_frame
        self.tag_family = tag_family
        self.tag_size_m = float(tag_size_m)
        self.stale_after_s = float(stale_after_s)
        self.fallback_camera_params = fallback_camera_params
        self._cache: dict[int, dict[str, Any]] = {}

    def status(self) -> dict[str, Any]:
        now = time.time()
        return {
            "camera_name": self.camera_name,
            "camera_frame": self.camera_frame,
            "tag_family": self.tag_family,
            "tag_size_m": self.tag_size_m,
            "has_fallback_camera_params": self.fallback_camera_params is not None,
            "cached_tag_ids": sorted(self._cache),
            "detections": [
                {
                    "tag_id": tag_id,
                    "age_s": round(now - float(item.get("timestamp_s", 0.0)), 4),
                    "stale": now - float(item.get("timestamp_s", 0.0)) > self.stale_after_s,
                }
                for tag_id, item in sorted(self._cache.items())
            ],
        }

    def detect_from_observation(self, observation: Any) -> dict[str, Any]:
        image = _get_camera_image(observation, self.camera_name)
        if image is None:
            return {"ok": False, "message": f"observation image not found: {self.camera_name}"}
        camera_params = _get_camera_params(observation, self.camera_name) or self.fallback_camera_params
        detections = detect_tags_from_image(
            image,
            camera_params,
            camera_frame=self.camera_frame,
            camera_name=self.camera_name,
            tag_family=self.tag_family,
            tag_size_m=self.tag_size_m,
        )
        now = time.time()
        for item in detections:
            item["timestamp_s"] = now
            self._cache[int(item["tag_id"])] = item
        return {
            "ok": True,
            "camera_name": self.camera_name,
            "camera_frame": self.camera_frame,
            "tag_family": self.tag_family,
            "tag_size_m": self.tag_size_m,
            "detections": detections,
        }

    def get_tag_pose(self, tag_id: int, *, allow_stale: bool = False) -> dict[str, Any]:
        tag_id = int(tag_id)
        item = self._cache.get(tag_id)
        if item is None:
            return {"ok": False, "message": f"tag_id {tag_id} not found", "tag_id": tag_id}
        age_s = time.time() - float(item.get("timestamp_s", 0.0))
        if age_s > self.stale_after_s and not allow_stale:
            return {
                "ok": False,
                "message": f"tag_id {tag_id} is stale",
                "tag_id": tag_id,
                "age_s": age_s,
                "stale_after_s": self.stale_after_s,
                "last_detection": item,
            }
        return {"ok": True, "age_s": age_s, "stale": age_s > self.stale_after_s, **item}


def detect_tags_from_image(
    image: Any,
    camera_params: dict[str, Any] | None,
    *,
    camera_frame: str,
    camera_name: str,
    tag_family: str,
    tag_size_m: float,
) -> list[dict[str, Any]]:
    cv2, Detector = _load_runtime_deps()
    bgr = np.asarray(image)
    detection_image, pose_matrix, undistorted = _prepare_image_for_pose(cv2, bgr, camera_params)
    gray = cv2.cvtColor(detection_image, cv2.COLOR_RGB2GRAY) if detection_image.ndim == 3 else detection_image
    fx, fy, cx, cy = _camera_params_from_matrix(pose_matrix)
    detector = Detector(
        families=tag_family,
        nthreads=2,
        quad_decimate=1.5,
        quad_sigma=0.8,
        refine_edges=True,
        decode_sharpening=0.25,
    )
    raw = detector.detect(
        gray,
        estimate_tag_pose=True,
        camera_params=(fx, fy, cx, cy),
        tag_size=float(tag_size_m),
    )
    results: list[dict[str, Any]] = []
    for det in raw:
        if det.pose_t is None or det.pose_R is None:
            continue
        position = np.asarray(det.pose_t, dtype=np.float64).reshape(3)
        rotation = np.asarray(det.pose_R, dtype=np.float64).reshape(3, 3)
        results.append(
            {
                "tag_id": int(det.tag_id),
                "tag_family": det.tag_family.decode() if hasattr(det.tag_family, "decode") else str(det.tag_family),
                "camera_name": camera_name,
                "camera_frame": camera_frame,
                "tag_size_m": float(tag_size_m),
                "position_camera_m": [float(v) for v in position.tolist()],
                "translation_m": [float(v) for v in position.tolist()],
                "distance_m": float(np.linalg.norm(position)),
                "rotation_matrix": rotation.tolist(),
                "euler_rpy_deg": _rotation_matrix_to_euler_deg(rotation),
                "center_px": [float(v) for v in np.asarray(det.center).reshape(2).tolist()],
                "corners_px": np.asarray(det.corners, dtype=np.float64).reshape(4, 2).tolist(),
                "camera_params": {"fx": fx, "fy": fy, "cx": cx, "cy": cy, "undistorted": undistorted},
            }
        )
    return results


def _load_runtime_deps():
    try:
        import cv2
        from pupil_apriltags import Detector
    except Exception as exc:
        raise RuntimeError("AprilTag detection requires cv2 and pupil_apriltags in the runtime environment") from exc
    return cv2, Detector


def _camera_params_tuple(camera_params: dict[str, Any] | None) -> tuple[float, float, float, float]:
    return _camera_params_from_matrix(_camera_matrix(camera_params))


def _camera_params_from_matrix(matrix: np.ndarray) -> tuple[float, float, float, float]:
    return float(matrix[0, 0]), float(matrix[1, 1]), float(matrix[0, 2]), float(matrix[1, 2])


def _prepare_image_for_pose(cv2: Any, image: np.ndarray, camera_params: dict[str, Any] | None) -> tuple[np.ndarray, np.ndarray, bool]:
    matrix = _camera_matrix(camera_params)
    distortion = _distortion_coefficients(camera_params)
    if distortion is None:
        return image, matrix, False

    width, height = _image_size(camera_params, image)
    new_matrix, _ = cv2.getOptimalNewCameraMatrix(matrix, distortion, (width, height), alpha=0.0)
    undistorted = cv2.undistort(image, matrix, distortion, None, new_matrix)
    return undistorted, new_matrix, True


def _camera_matrix(camera_params: dict[str, Any] | None) -> np.ndarray:
    if not camera_params:
        raise ValueError("camera_params are required for AprilTag pose estimation")
    if "fx" in camera_params:
        return np.asarray(
            [
                [float(camera_params["fx"]), 0.0, float(camera_params["cx"])],
                [0.0, float(camera_params["fy"]), float(camera_params["cy"])],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
    k = camera_params.get("K") if camera_params.get("K") is not None else camera_params.get("camera_matrix")
    if isinstance(k, dict):
        k = k.get("data")
    return np.asarray(k, dtype=np.float64).reshape(3, 3)


def _distortion_coefficients(camera_params: dict[str, Any] | None) -> np.ndarray | None:
    if not camera_params:
        return None
    raw = None
    for key in ("D", "dist", "distortion", "distortion_coefficients"):
        if key in camera_params and camera_params[key] is not None:
            raw = camera_params[key]
            break
    if isinstance(raw, dict):
        raw = raw.get("data")
    if raw is None:
        return None
    distortion = np.asarray(raw, dtype=np.float64).reshape(-1)
    if not np.any(np.abs(distortion) > 0.0):
        return None
    return distortion


def _image_size(camera_params: dict[str, Any] | None, image: np.ndarray) -> tuple[int, int]:
    size = (camera_params or {}).get("image_size")
    if isinstance(size, dict):
        return int(size["width"]), int(size["height"])
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        return int(size[0]), int(size[1])
    height, width = image.shape[:2]
    return int(width), int(height)


def _rotation_matrix_to_euler_deg(rotation: np.ndarray) -> list[float]:
    sy = math.sqrt(float(rotation[0, 0] ** 2 + rotation[1, 0] ** 2))
    singular = sy < 1e-6
    if not singular:
        rx = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
        ry = math.atan2(float(-rotation[2, 0]), sy)
        rz = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    else:
        rx = math.atan2(float(-rotation[1, 2]), float(rotation[1, 1]))
        ry = math.atan2(float(-rotation[2, 0]), sy)
        rz = 0.0
    return [math.degrees(rx), math.degrees(ry), math.degrees(rz)]


def _get_camera_image(observation: Any, camera_name: str) -> Any:
    images = _get(_get(observation, "observation") or observation, "images")
    return _get(images, camera_name)


def _get_camera_params(observation: Any, camera_name: str) -> dict[str, Any] | None:
    params = _get(_get(observation, "observation") or observation, "camera_params")
    value = _get(params, camera_name)
    return value if isinstance(value, dict) else None


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
