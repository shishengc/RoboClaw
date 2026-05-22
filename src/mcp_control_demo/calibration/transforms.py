from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def normalize_vector(values: Sequence[float]) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float64).reshape(3)
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12 or not math.isfinite(norm):
        raise ValueError(f"invalid vector norm: {values}")
    return vector / norm


def as_matrix4(values: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.shape != (4, 4):
        raise ValueError(f"expected 4x4 transform matrix, got shape {matrix.shape}")
    return matrix


def invert_transform(matrix: Sequence[Sequence[float]]) -> np.ndarray:
    return np.linalg.inv(as_matrix4(matrix))


def transform_point(matrix: Sequence[Sequence[float]], point: Sequence[float]) -> np.ndarray:
    transform = as_matrix4(matrix)
    homogeneous = np.ones(4, dtype=np.float64)
    homogeneous[:3] = np.asarray(point, dtype=np.float64).reshape(3)
    return (transform @ homogeneous)[:3]


def quat_xyzw_to_matrix(quat_xyzw: Sequence[float]) -> np.ndarray:
    x, y, z, w = [float(v) for v in quat_xyzw]
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-12 or not math.isfinite(norm):
        raise ValueError(f"invalid quaternion: {quat_xyzw}")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.asarray(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float64,
    )


def matrix_to_quat_xyzw(rotation: Sequence[Sequence[float]]) -> list[float]:
    matrix = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    trace = float(np.trace(matrix))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (matrix[2, 1] - matrix[1, 2]) / scale
        y = (matrix[0, 2] - matrix[2, 0]) / scale
        z = (matrix[1, 0] - matrix[0, 1]) / scale
    elif matrix[0, 0] > matrix[1, 1] and matrix[0, 0] > matrix[2, 2]:
        scale = math.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2]) * 2.0
        w = (matrix[2, 1] - matrix[1, 2]) / scale
        x = 0.25 * scale
        y = (matrix[0, 1] + matrix[1, 0]) / scale
        z = (matrix[0, 2] + matrix[2, 0]) / scale
    elif matrix[1, 1] > matrix[2, 2]:
        scale = math.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2]) * 2.0
        w = (matrix[0, 2] - matrix[2, 0]) / scale
        x = (matrix[0, 1] + matrix[1, 0]) / scale
        y = 0.25 * scale
        z = (matrix[1, 2] + matrix[2, 1]) / scale
    else:
        scale = math.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1]) * 2.0
        w = (matrix[1, 0] - matrix[0, 1]) / scale
        x = (matrix[0, 2] + matrix[2, 0]) / scale
        y = (matrix[1, 2] + matrix[2, 1]) / scale
        z = 0.25 * scale

    quat = np.asarray([x, y, z, w], dtype=np.float64)
    quat /= np.linalg.norm(quat)
    return [float(v) for v in quat]


def transform_orientation_xyzw(matrix: Sequence[Sequence[float]], quat_xyzw: Sequence[float]) -> list[float]:
    transform = as_matrix4(matrix)
    rotation = transform[:3, :3] @ quat_xyzw_to_matrix(quat_xyzw)
    return matrix_to_quat_xyzw(rotation)
