from dataclasses import dataclass, asdict
from ..storage.yaml_io import read_yaml, write_yaml


@dataclass
class PlaneCalibration:
    calibration_id: str
    camera_id: str
    image_size: list
    camera_matrix: list
    distortion: list
    intrinsics_valid: bool
    intrinsics_version: str
    tag_family: str
    tag_layout_file: str
    tag_layout_hash: str
    detected_tag_ids: list
    homography_image_to_plane: list
    valid_plane_polygon_mm: list
    ransac_threshold_px: float
    ransac_threshold_mm: float
    inlier_count: int
    image_reprojection_rms_px: float
    plane_mapping_rms_mm: float
    calibration_timestamp: str
    image_points_undistorted: list
    plane_points_mm: list
    inlier_mask: list
    point_tag_ids: list
    mapping_direction: str = "undistorted_pixels_to_plane_mm"

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        write_yaml(path, self.to_dict())

    @classmethod
    def load(cls, path):
        return cls(**read_yaml(path))
