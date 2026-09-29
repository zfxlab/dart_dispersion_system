import numpy as np

from plane_picker.ui.dispersion_view import region_corners, rotate_points


def test_result_rotation_is_counter_clockwise():
    np.testing.assert_allclose(
        rotate_points([[100, 0], [0, 50]], 90),
        [[0, 100], [-50, 0]],
        atol=1e-10,
    )


def test_manual_region_preserves_physical_width_and_height():
    corners = region_corners({
        "center_plane_mm": [10, 20],
        "width_mm": 80,
        "height_mm": 40,
        "angle_plane_deg": 0,
    })

    np.testing.assert_allclose(corners.min(axis=0), [-30, 0])
    np.testing.assert_allclose(corners.max(axis=0), [50, 40])
