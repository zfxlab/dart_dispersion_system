from datetime import datetime, timezone
from uuid import uuid4
import cv2
import numpy as np

from .camera_model import CameraModel, points2
from ..models.calibration import PlaneCalibration


def checked_h(matrix):
    h = np.asarray(matrix, dtype=np.float64)
    if h.shape != (3, 3) or not np.isfinite(h).all():
        raise ValueError("Homography 必须为有限的 3×3 矩阵")
    norm = np.linalg.norm(h)
    if norm == 0 or np.linalg.cond(h / norm) > 1e12:
        raise ValueError("Homography 接近奇异或不可逆")
    return h / norm


def project(matrix, points):
    h = checked_h(matrix)
    p = np.c_[points2(points), np.ones(len(points))]
    q = p @ h.T
    # Relative test is invariant to arbitrary homography scaling.
    tolerance = 1e-10 * np.linalg.norm(h[2]) * np.linalg.norm(p, axis=1)
    if np.any(np.abs(q[:, 2]) <= tolerance):
        raise ValueError("映射分母接近 0，位置位于投影地平线附近")
    result = q[:, :2] / q[:, 2:]
    if not np.isfinite(result).all():
        raise ValueError("映射坐标非有限")
    return result


class PlaneMapper:
    def __init__(self, calibration: PlaneCalibration):
        self.calibration = calibration
        self.camera = CameraModel.from_dict(calibration.to_dict())
        self.h = checked_h(calibration.homography_image_to_plane)
        self.polygon = points2(calibration.valid_plane_polygon_mm).astype(np.float32)
        if len(self.polygon) < 3 or abs(cv2.contourArea(self.polygon)) < 1e-6 or not cv2.isContourConvex(self.polygon):
            raise ValueError("有效标定区域必须为非退化凸多边形")
        if calibration.mapping_direction != "undistorted_pixels_to_plane_mm":
            raise ValueError("Homography 方向不正确")
        src = points2(calibration.image_points_undistorted)
        dst = points2(calibration.plane_points_mm)
        if any(type(v) is not bool for v in calibration.inlier_mask):
            raise ValueError("内点掩码必须是布尔列表")
        mask = np.asarray(calibration.inlier_mask, dtype=bool)
        if len(src) != len(dst) or mask.shape != (len(src),) or mask.sum() < 4:
            raise ValueError("标定对应点或内点记录不完整")
        if int(mask.sum()) != calibration.inlier_count or len(calibration.point_tag_ids) != len(src):
            raise ValueError("标定内点计数不一致")
        if any(not np.isfinite(t) or t <= 0 for t in (calibration.ransac_threshold_mm, calibration.ransac_threshold_px)):
            raise ValueError("标定阈值必须为有限正数")
        forward = np.linalg.norm(project(self.h, src[mask]) - dst[mask], axis=1)
        backward = np.linalg.norm(project(np.linalg.inv(self.h), dst[mask]) - src[mask], axis=1)
        if forward.max() > calibration.ransac_threshold_mm * 1.05 or backward.max() > calibration.ransac_threshold_px * 1.05:
            raise ValueError("标定矩阵方向或角点误差异常")
        actual_errors = [float(np.sqrt(np.mean(backward**2))),float(np.sqrt(np.mean(forward**2)))]
        stored_errors = [calibration.image_reprojection_rms_px,calibration.plane_mapping_rms_mm]
        if not np.isfinite(stored_errors).all() or not np.allclose(stored_errors,actual_errors,rtol=1e-5,atol=1e-6):
            raise ValueError("标定误差记录与对应点不一致")
        expected = cv2.convexHull(dst[mask].astype(np.float32)).reshape(-1, 2)
        if abs(cv2.contourArea(expected) - cv2.contourArea(self.polygon)) > max(1e-3, cv2.contourArea(expected) * 1e-5):
            raise ValueError("有效区域与标定内点不一致")
        if any(cv2.pointPolygonTest(expected, tuple(map(float, p)), True) < -1e-3 for p in self.polygon):
            raise ValueError("有效区域超出标定内点凸包")
        # Reject a projective horizon crossing the accepted polygon.
        inv = np.linalg.inv(self.h)
        den = np.c_[self.polygon, np.ones(len(self.polygon))] @ inv[2]
        if den.min() <= 0 <= den.max():
            raise ValueError("有效区域跨越投影地平线")

    @classmethod
    def fit(cls, detections, layout, camera, camera_id="cam_left", *,
            ransac_threshold_mm=3., ransac_threshold_px=3., allow_single_tag=False):
        if any(not np.isfinite(t) or t <= 0 for t in (ransac_threshold_mm, ransac_threshold_px)):
            raise ValueError("RANSAC / 重投影阈值必须为有限正数")
        known = [d for d in detections if d.tag_id in layout.tags]
        ids = [d.tag_id for d in known]
        if len(ids) != len(set(ids)):
            raise ValueError("同一图像中出现重复 Tag ID，无法确定对应关系")
        if len(known) < (1 if allow_single_tag else 2):
            raise ValueError("正式标定至少需要 2 个已知 Tag；单 Tag 仅可显式启用调试")
        src = camera.undistort(np.concatenate([d.corners_raw for d in known]))
        dst = np.concatenate([layout.corners(d.tag_id) for d in known])
        for p in (src, dst):
            if len(p) < 4 or np.linalg.matrix_rank(p - p.mean(axis=0)) < 2:
                raise ValueError("至少需要 4 个非共线对应点")
        # OpenCV threshold uses DESTINATION units. Here they are mm, not px.
        h, initial = cv2.findHomography(src, dst, cv2.RANSAC, ransac_threshold_mm)
        if h is None or initial is None:
            raise ValueError("Homography 求解失败")
        h = checked_h(h)
        mask = initial.ravel().astype(bool)
        # Pixel threshold is an additional inverse-reprojection gate.
        for _ in range(5):
            err_mm = np.linalg.norm(project(h, src) - dst, axis=1)
            err_px = np.linalg.norm(project(np.linalg.inv(h), dst) - src, axis=1)
            new_mask = mask & (err_mm <= ransac_threshold_mm) & (err_px <= ransac_threshold_px)
            if new_mask.sum() < (4 if allow_single_tag else 8):
                raise ValueError("通过误差检查的内点不足（正式模式至少 8 个）")
            if np.array_equal(new_mask, mask):
                break
            mask = new_mask
            h, _ = cv2.findHomography(src[mask], dst[mask], 0)
            h = checked_h(h)
        point_ids = np.repeat(ids, 4)
        err_mm = np.linalg.norm(project(h, src) - dst, axis=1)
        err_px = np.linalg.norm(project(np.linalg.inv(h), dst) - src, axis=1)
        if not allow_single_tag and len(set(point_ids[mask])) < 2:
            raise ValueError("内点必须覆盖至少 2 个 Tag")
        polygon = cv2.convexHull(dst[mask].astype(np.float32)).reshape(-1, 2)
        calibration = PlaneCalibration(
            calibration_id=str(uuid4()), camera_id=camera_id, **camera.to_dict(),
            tag_family=layout.family, tag_layout_file=layout.file, tag_layout_hash=layout.layout_hash,
            detected_tag_ids=ids, homography_image_to_plane=h.tolist(), valid_plane_polygon_mm=polygon.tolist(),
            ransac_threshold_px=float(ransac_threshold_px), ransac_threshold_mm=float(ransac_threshold_mm),
            inlier_count=int(mask.sum()), image_reprojection_rms_px=float(np.sqrt(np.mean(err_px[mask] ** 2))),
            plane_mapping_rms_mm=float(np.sqrt(np.mean(err_mm[mask] ** 2))),
            calibration_timestamp=datetime.now(timezone.utc).isoformat(),
            image_points_undistorted=src.tolist(), plane_points_mm=dst.tolist(), inlier_mask=mask.tolist(),
            point_tag_ids=point_ids.tolist())
        return cls(calibration)

    def contains(self, xy):
        p = points2([xy])[0]
        return cv2.pointPolygonTest(self.polygon, (float(p[0]), float(p[1])), True) >= -1e-5

    def map_raw(self, raw, require_inside=True):
        p = points2([raw])[0]
        w, h = self.camera.image_size
        if not (0 <= p[0] < w and 0 <= p[1] < h):
            raise ValueError("点击位置超出原始图像")
        und = self.camera.undistort([p])[0]
        xy = project(self.h, [und])[0]
        if require_inside and not self.contains(xy):
            raise ValueError("点击超出有效标定区域，未创建记录")
        return und, xy
