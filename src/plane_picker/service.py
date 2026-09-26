"""Application layer: UI never detects tags or solves matrices directly."""
import copy
from pathlib import Path
import numpy as np

from .camera.file_source import FileCameraSource
from .calibration.camera_model import CameraModel
from .calibration.tag_layout import TagLayout
from .calibration.tag_detector import AprilTag3Detector
from .calibration.homography import PlaneMapper
from .models.calibration import PlaneCalibration
from .models.session import Session
from .models.shot import ShotRecord, now
from .storage.session_io import SessionRepository


class PickerService:
    def __init__(self, camera_id="cam_left", ransac_threshold_mm=3., ransac_threshold_px=3., id_allocator=None):
        self.session = Session(camera_id=camera_id)
        self.image = None
        self.camera = None
        self.layout = None
        self.mapper = None
        self.detections = []
        self.history = []
        self.high_water = 1
        self.id_allocator = id_allocator
        self.ransac_threshold_mm = ransac_threshold_mm
        self.ransac_threshold_px = ransac_threshold_px

    def require_empty(self):
        if self.session.shots:
            raise ValueError("本轮已有记录。请先保存 Session，再新建本轮后更换图像、布局或标定")

    def invalidate(self):
        self.mapper = None
        self.session.calibration_id = ""
        self.session.calibration_file = ""
        self.session.calibration_snapshot = None
        self.history.clear()

    def new_round(self):
        self.high_water = max(self.high_water, self.session.next_shot_id)
        self.session = Session(camera_id=self.session.camera_id, next_shot_id=self.high_water,
                               image_file=self.session.image_file, calibration_file=self.session.calibration_file,
                               calibration_id=self.session.calibration_id, tag_layout_file=self.session.tag_layout_file,
                               calibration_snapshot=self.session.calibration_snapshot,
                               tag_layout_snapshot=self.session.tag_layout_snapshot)
        self.history.clear()

    def open_image(self, path):
        self.require_empty()
        source = FileCameraSource(path, self.session.camera_id)
        image = source.read()
        # A new file can represent a moved camera; old H is never silently reused.
        self.invalidate()
        self.image = image
        self.session.image_file = source.path
        self.detections = []
        if self.camera and self.camera.image_size != (image.shape[1], image.shape[0]):
            self.camera = None

    def load_camera(self, path):
        self.require_empty()
        camera = CameraModel.load(path)
        if self.image is not None:
            camera.check_size((self.image.shape[1], self.image.shape[0]))
        self.camera = camera
        self.invalidate()

    def set_debug(self, enabled):
        self.require_empty()
        if self.image is None:
            raise ValueError("请先打开图像")
        self.camera = CameraModel.debug((self.image.shape[1], self.image.shape[0])) if enabled else None
        self.invalidate()

    def load_layout(self, path):
        self.require_empty()
        layout = TagLayout.load(path)
        self.layout = layout
        self.session.tag_layout_file = layout.file
        self.session.tag_layout_snapshot = layout.to_dict()
        self.detections = []
        self.invalidate()

    def detect(self):
        if self.image is None or self.layout is None:
            raise ValueError("请先打开图像并加载 Tag 布局")
        return AprilTag3Detector(self.layout.family).detect(self.image)

    def calculate(self):
        self.require_empty()
        if self.image is None or self.camera is None or self.layout is None:
            raise ValueError("请加载图像、相机内参和 Tag 布局；无内参时只能显式启用调试模式")
        self.camera.check_size((self.image.shape[1], self.image.shape[0]))
        return PlaneMapper.fit(self.detections, self.layout, self.camera, self.session.camera_id,
                               ransac_threshold_mm=self.ransac_threshold_mm,
                               ransac_threshold_px=self.ransac_threshold_px)

    def accept_mapping(self, mapper, file=""):
        self.mapper = mapper
        self.camera = mapper.camera
        self.session.calibration_id = mapper.calibration.calibration_id
        self.session.calibration_snapshot = mapper.calibration.to_dict()
        self.session.calibration_file = file
        self.history.clear()

    def load_calibration(self, path):
        self.require_empty()
        if self.image is None or self.layout is None:
            raise ValueError("加载平面标定前请先打开图像并加载布局")
        mapper = PlaneMapper(PlaneCalibration.load(path))
        mapper.camera.check_size((self.image.shape[1], self.image.shape[0]))
        if mapper.calibration.tag_layout_hash != self.layout.layout_hash:
            raise ValueError("标定文件与当前 Tag 布局哈希不一致")
        if mapper.calibration.camera_id != self.session.camera_id:
            raise ValueError("标定文件的 camera_id 不匹配")
        self.accept_mapping(mapper, str(Path(path).resolve()))

    def save_calibration(self, path):
        if not self.mapper:
            raise ValueError("尚无有效平面标定")
        self.mapper.calibration.save(path)
        self.session.calibration_file = str(Path(path).resolve())

    def mapping(self, raw, require_inside=True):
        if self.mapper is None:
            raise ValueError("请先计算或加载有效的平面标定")
        return self.mapper.map_raw(raw, require_inside)

    def checkpoint(self):
        self.history.append(copy.deepcopy(self.session.shots))

    def display_image(self, undistorted=False):
        if self.image is None:
            return None
        if undistorted:
            if self.camera is None:
                raise ValueError("显示去畸变图像需要先加载内参")
            return self.camera.undistort_image(self.image)
        return self.image

    def display_to_raw(self, xy, undistorted=False):
        if undistorted:
            if self.camera is None:
                raise ValueError("未加载相机内参")
            return self.camera.distort([xy])[0]
        return np.asarray(xy, dtype=float)

    def plane_to_raw(self, xy):
        from .calibration.homography import project
        if self.mapper is None:
            raise ValueError("未加载平面标定")
        return self.camera.distort(project(np.linalg.inv(self.mapper.h), [xy]))[0]

    def add_shot(self, raw, color):
        und, xy = self.mapping(raw)
        sid = max(self.high_water, self.session.next_shot_id)
        if self.id_allocator:
            sid = self.id_allocator.allocate(sid)
        shot = ShotRecord(sid, f"{'R' if color == 'red' else 'B'}{sid:02d}", color,
                          self.session.camera_id, self.session.image_file, list(raw), und.tolist(), xy.tolist(),
                          True, self.session.calibration_id, now())
        self.checkpoint()
        self.session.shots.append(shot)
        self.session.next_shot_id = self.high_water = sid + 1
        return shot

    def edit_shot(self, shot_id, raw, color):
        und, xy = self.mapping(raw)
        if color not in ("red", "blue"):
            raise ValueError("颜色必须是 red 或 blue")
        shot = next(s for s in self.session.shots if s.shot_id == shot_id)
        self.checkpoint()
        shot.color = color
        shot.shot_label = f"{'R' if color == 'red' else 'B'}{shot.shot_id:02d}"
        shot.pixel_raw, shot.pixel_undistorted, shot.plane_mm = list(raw), und.tolist(), xy.tolist()

    def delete_shot(self, shot_id):
        if not any(s.shot_id == shot_id for s in self.session.shots):
            raise ValueError("请选择要删除的点")
        self.checkpoint()
        self.session.shots = [s for s in self.session.shots if s.shot_id != shot_id]

    def clear(self):
        self.checkpoint()
        self.session.shots = []

    def renumber_labels(self):
        # Stable shot_id remains the identity; only human-facing labels change.
        self.checkpoint()
        counts = {"red": 0, "blue": 0}
        for s in self.session.shots:
            counts[s.color] += 1
            s.shot_label = f"{'R' if s.color == 'red' else 'B'}{counts[s.color]:02d}"

    def undo(self):
        if self.history:
            self.session.shots = self.history.pop()

    def save_session(self, path):
        if not self.mapper or not self.layout or not self.session.image_file:
            raise ValueError("请先完成平面标定，再保存可恢复的 Session")
        SessionRepository.save(path, self.session)

    def load_session(self, path):
        # Validate all dependencies first; failures leave current state intact.
        session = SessionRepository.load(path)
        def resolve(p):
            return str((Path(path).parent / p).resolve()) if not Path(p).is_absolute() else p
        if not session.image_file or session.calibration_snapshot is None or session.tag_layout_snapshot is None:
            raise ValueError("Session 缺少图像、标定或布局快照")
        image = FileCameraSource(resolve(session.image_file), session.camera_id).read()
        layout = TagLayout.from_dict(session.tag_layout_snapshot, session.tag_layout_file)
        mapper = PlaneMapper(PlaneCalibration(**session.calibration_snapshot))
        mapper.camera.check_size((image.shape[1], image.shape[0]))
        if mapper.calibration.tag_layout_hash != layout.layout_hash or mapper.calibration.calibration_id != session.calibration_id:
            raise ValueError("Session 标定标识或布局哈希不一致")
        if mapper.calibration.camera_id != session.camera_id:
            raise ValueError("Session 相机标识不一致")
        for s in session.shots:
            und, xy = mapper.map_raw(s.pixel_raw)
            if s.camera_id != session.camera_id or s.calibration_id != session.calibration_id or s.image_file != session.image_file:
                raise ValueError("Shot 与 Session 的图像、相机或标定不一致")
            if not np.allclose(und, s.pixel_undistorted, atol=1e-4, rtol=0) or not np.allclose(xy, s.plane_mm, atol=1e-4, rtol=0):
                raise ValueError("Shot 坐标与保存的标定不一致")
        if self.id_allocator:
            self.id_allocator.reserve(session.next_shot_id)
        session.image_file = resolve(session.image_file)
        for shot in session.shots:
            shot.image_file = session.image_file
        self.session, self.image, self.layout, self.mapper = session, image, layout, mapper
        self.camera = mapper.camera
        self.high_water = max(self.high_water, session.next_shot_id)
        self.detections = []
        self.history.clear()

    def display_detections(self, undistorted=False):
        from .calibration.tag_detector import TagDetection
        detections = self.detections
        if not detections and self.mapper:
            c = self.mapper.calibration
            raw = self.camera.distort(c.image_points_undistorted)
            detections = [TagDetection(c.point_tag_ids[i],raw[i:i+4]) for i in range(0,len(raw),4)]
        if undistorted and self.camera:
            detections = [TagDetection(d.tag_id,self.camera.undistort(d.corners_raw)) for d in detections]
        return detections

    def pixel_info(self, display_point, undistorted=False):
        raw = self.display_to_raw(display_point,undistorted)
        und = self.camera.undistort([raw])[0] if self.camera else None
        xy = self.mapping(raw,False)[1] if self.mapper else None
        return raw,und,xy
