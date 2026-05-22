from __future__ import annotations

import pytest

from mcp_control_demo.perception.apriltag import AprilTagPerceptionService, _camera_params_tuple


def test_yaml_intrinsics_shape_is_accepted_as_camera_params():
    params = {
        "camera_matrix": {
            "data": [
                [641.6347338250084, 0.0, 652.2551428790697],
                [0.0, 638.6652075681625, 362.8615784851012],
                [0.0, 0.0, 1.0],
            ]
        },
        "distortion_coefficients": {"data": [-17.929117703214654, 117.17887806337045, 0.0, 0.0]},
        "image_size": {"width": 1280, "height": 720},
    }
    assert _camera_params_tuple(params) == pytest.approx(
        (641.6347338250084, 638.6652075681625, 652.2551428790697, 362.8615784851012)
    )


def test_perception_status_reports_fallback_camera_params():
    service = AprilTagPerceptionService(fallback_camera_params={"fx": 1.0, "fy": 1.0, "cx": 0.5, "cy": 0.5})
    assert service.status()["has_fallback_camera_params"] is True
