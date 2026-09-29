import math

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QBrush

from .image_view import NavigableView, pen


PALETTE = [
    "#ff5f68", "#55aaff", "#45d6a0", "#f2c14e", "#b38cff",
    "#ff8c42", "#4dd0e1", "#ec70b1", "#a3d65c", "#c6a47e",
]


def rotate_points(points, angle_deg):
    """Rotate plane points counter-clockwise into the launch coordinate system."""
    values = np.asarray(points, dtype=float)
    angle = math.radians(float(angle_deg))
    matrix = np.asarray([
        [math.cos(angle), -math.sin(angle)],
        [math.sin(angle), math.cos(angle)],
    ])
    return values @ matrix.T


def region_corners(region):
    """Return an oriented rectangle's corners in the calibration plane."""
    width = float(region["width_mm"])
    height = float(region["height_mm"])
    local = np.asarray([
        [-width / 2, -height / 2], [width / 2, -height / 2],
        [width / 2, height / 2], [-width / 2, height / 2],
    ])
    return rotate_points(local, region.get("angle_plane_deg", 0.0)) + np.asarray(
        region["center_plane_mm"], dtype=float,
    )


class DispersionView(NavigableView):
    """Equal-scale result view in the rotatable launch coordinate system."""

    regionDrawn = Signal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.set_point_editing_enabled(False)
        self._region_mode = False
        self._region_origin = None
        self._region_preview = None

    def start_region_selection(self):
        self._region_mode = True
        self._region_origin = None
        self._region_preview = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def cancel_region_selection(self):
        if self._region_preview is not None and self._region_preview.scene() is not None:
            self.scene().removeItem(self._region_preview)
        self._region_mode = False
        self._region_origin = None
        self._region_preview = None
        self.unsetCursor()

    def mousePressEvent(self, event):
        if self._region_mode and event.button() == Qt.MouseButton.LeftButton:
            self._region_origin = self.mapToScene(event.position().toPoint())
            preview_pen = pen("#ffffff", 2)
            preview_pen.setStyle(Qt.PenStyle.DashLine)
            self._region_preview = self.scene().addRect(
                QRectF(self._region_origin, self._region_origin), preview_pen,
                QBrush(QColor(255, 255, 255, 28)),
            )
            self._region_preview.setZValue(20)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._region_mode and self._region_origin is not None:
            current = self.mapToScene(event.position().toPoint())
            self._region_preview.setRect(QRectF(self._region_origin, current).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._region_mode and self._region_origin is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            current = self.mapToScene(event.position().toPoint())
            rect = QRectF(self._region_origin, current).normalized()
            self.cancel_region_selection()
            if rect.width() >= 0.1 and rect.height() >= 0.1:
                center = rect.center()
                self.regionDrawn.emit(center.x(), -center.y(), rect.width(), rect.height())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def draw(self, measurements, grid_mm=100.0, visible_ids=None,
             impact_radius_mm=10.0, rotation_deg=0.0, valid_polygon=None,
             regions=None, selected_object=None):
        self.cancel_region_selection()
        self.scene().clear()
        completed = [measurement for measurement in measurements if measurement.complete]
        object_ids = sorted({m.object_id for m in completed}, key=str.casefold)
        colors = {
            object_id: PALETTE[index % len(PALETTE)]
            for index, object_id in enumerate(object_ids)
        }
        visible = set(object_ids) if visible_ids is None else set(visible_ids)
        shown = [measurement for measurement in completed if measurement.object_id in visible]
        regions = regions or {}

        bounds = [np.asarray([0.0, 0.0])]
        if valid_polygon is not None:
            rotated_polygon = rotate_points(valid_polygon, rotation_deg)
            scene_polygon = rotated_polygon * [1, -1]
            bounds.extend(scene_polygon)
            item = self.polygon(scene_polygon, "#43d69a")
            item.setZValue(-3)
            item.setToolTip("标定有效平面")

        for measurement in shown:
            result_point = rotate_points([measurement.plane_mm], rotation_deg)[0]
            scene_point = result_point * [1, -1]
            radius = float(impact_radius_mm)
            bounds.extend([scene_point - radius, scene_point + radius])
            color = colors[measurement.object_id]
            fill = QColor(color)
            fill.setAlpha(48)
            circle = self.scene().addEllipse(
                scene_point[0] - radius, scene_point[1] - radius,
                radius * 2, radius * 2, pen(color, 2), QBrush(fill),
            )
            circle.setZValue(1)
            tooltip = (
                f"{measurement.object_id} / 第 {measurement.trial} 次\n"
                f"X {result_point[0]:.2f} mm, Y {result_point[1]:.2f} mm\n"
                f"落点半径 {radius:.2f} mm"
            )
            circle.setToolTip(tooltip)
            marker = self.marker(scene_point, color, "", show_label=False)
            marker.setToolTip(tooltip)

        for object_id, region in regions.items():
            if object_id not in visible or object_id not in colors:
                continue
            corners = region_corners(region)
            scene_corners = rotate_points(corners, rotation_deg) * [1, -1]
            bounds.extend(scene_corners)
            color = colors[object_id]
            fill = QColor(color)
            fill.setAlpha(25)
            item = self.polygon(
                scene_corners,
                "#ffffff" if object_id == selected_object else color,
                fill.name(QColor.NameFormat.HexArgb),
            )
            item.setZValue(2)
            size = f'{region["width_mm"]:.1f} × {region["height_mm"]:.1f} mm'
            item.setToolTip(f"{object_id} 散布范围\n长 × 宽：{size}")

        if len(bounds) == 1 and not completed:
            self.label("尚无已完成的测量", 0, 0, "#9aabc2")
            self.setSceneRect(-100, -100, 200, 200)
            return

        points = np.asarray(bounds, dtype=float)
        margin = max(float(grid_mm), float(impact_radius_mm) * 2)
        low = points.min(axis=0) - margin
        high = points.max(axis=0) + margin
        span = max(high - low)
        step = max(float(grid_mm), math.ceil(span / float(grid_mm) / 60) * float(grid_mm))
        for x in np.arange(math.floor(low[0] / step) * step, high[0] + step, step):
            item = self.scene().addLine(x, low[1], x, high[1], pen("#303c4f"))
            item.setZValue(-5)
            self.label(f"{x:g}", x, high[1], "#8394ad")
        for y in np.arange(math.floor(low[1] / step) * step, high[1] + step, step):
            item = self.scene().addLine(low[0], y, high[0], y, pen("#303c4f"))
            item.setZValue(-5)
            self.label(f"{-y:g}", low[0], y, "#8394ad")
        self.scene().addLine(low[0], 0, high[0], 0, pen("#d3dce9", 2))
        self.scene().addLine(0, low[1], 0, high[1], pen("#d3dce9", 2))
        self.label("+X / mm →", high[0], 0)
        self.label("↑ +Y / mm", 0, low[1])
        self.setSceneRect(low[0], low[1], *(high - low))
