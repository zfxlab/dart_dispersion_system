from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QColor, QImage, QPixmap, QPen, QBrush, QPolygonF, QPainter
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem
import numpy as np

from .coordinate_transform import CoordinateTransform


def pen(color, width=1):
    p = QPen(QColor(color), width)
    p.setCosmetic(True)
    return p


class NavigableView(QGraphicsView):
    clicked = Signal(float, float)
    hovered = Signal(float, float)
    selected = Signal(int)
    moved = Signal(int, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor("#161d29"))
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._press = None
        self._last = None
        self._shot = None
        self._button = None
        self._dragged = False

    def scene_point(self, pos):
        # viewportTransform includes centering, letterboxes, scrollbars and zoom.
        t = CoordinateTransform.from_qtransform(self.viewportTransform(), self.devicePixelRatioF())
        return t.view_to_image([pos.x(), pos.y()])

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        scale = self.transform().m11() * factor
        if 1e-4 < scale < 1000:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        self._press = event.position()
        self._last = event.position()
        self._button = event.button()
        self._dragged = False
        item = self.itemAt(event.position().toPoint())
        self._shot = item.data(0) if item is not None else None
        if self._shot is not None and self._button == Qt.MouseButton.LeftButton:
            self.selected.emit(int(self._shot))
        event.accept()

    def mouseMoveEvent(self, event):
        x, y = self.scene_point(event.position())
        self.hovered.emit(float(x), float(y))
        if self._press is not None:
            self._dragged |= (event.position() - self._press).manhattanLength() > 4
            if self._shot is None or self._button != Qt.MouseButton.LeftButton:
                delta = event.position() - self._last
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            self._last = event.position()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._press is not None and self._button == Qt.MouseButton.LeftButton:
            x, y = self.scene_point(event.position())
            if self._shot is not None and self._dragged:
                self.moved.emit(int(self._shot), float(x), float(y))
            elif self._shot is None and not self._dragged:
                self.clicked.emit(float(x), float(y))
        self._press = self._last = self._shot = self._button = None
        event.accept()

    def fit_all(self):
        rect = self.scene().itemsBoundingRect()
        if not rect.isEmpty():
            self.setSceneRect(rect.adjusted(-20,-20,20,20))
            self.fitInView(rect.adjusted(-10,-10,10,10), Qt.AspectRatioMode.KeepAspectRatio)

    def polygon(self, points, color, fill=None):
        return self.scene().addPolygon(QPolygonF([QPointF(float(x),float(y)) for x,y in points]),
                                       pen(color, 2), QBrush(QColor(fill)) if fill else QBrush(Qt.BrushStyle.NoBrush))

    def label(self, text, x, y, color="#dce5f5", shot_id=None):
        item = self.scene().addSimpleText(str(text))
        item.setBrush(QColor(color))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        item.setPos(float(x), float(y))
        if shot_id is not None:
            item.setData(0, shot_id)
        return item

    def marker(self, xy, color, text, shot_id=None, selected=False):
        x,y = map(float, xy)
        item = self.scene().addEllipse(-5,-5,10,10, pen("#ffffff" if selected else color, 2), QBrush(QColor(color)))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        item.setPos(x,y)
        item.setZValue(4)
        if shot_id is not None:
            item.setData(0, shot_id)
        label = self.label(text, x, y, color, shot_id)
        label.setZValue(5)


class ImageView(NavigableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_item = None

    def set_image(self, image):
        self.scene().clear()
        self._image_item = None
        if image is None:
            return
        rgb = np.ascontiguousarray(image[:,:,::-1])
        qimage = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        self._image_item = self.scene().addPixmap(QPixmap.fromImage(qimage))
        self._image_item.setZValue(-10)
        self.setSceneRect(0,0,rgb.shape[1],rgb.shape[0])

    def clear_overlays(self):
        for item in self.scene().items():
            if item is not self._image_item:
                self.scene().removeItem(item)

    def draw_tags(self, detections, mapper, undistorted=False):
        mask = {}
        if mapper:
            c = mapper.calibration
            for index, tag_id in enumerate(c.point_tag_ids):
                mask[tag_id, index % 4] = c.inlier_mask[index]
        for d in detections:
            corners = mapper.camera.undistort(d.corners_raw) if undistorted and mapper else d.corners_raw
            self.polygon(corners, "#f2cc60")
            self.label(f"Tag {d.tag_id}", *corners.mean(axis=0), "#f2cc60")
            for j,p in enumerate(corners):
                color = "#43d69a" if mask.get((d.tag_id,j)) else "#ff783e" if (d.tag_id,j) in mask else "#f2cc60"
                self.marker(p, color, str(j))

    def draw_shots(self, shots, undistorted=False, selected=None):
        for s in shots:
            self.marker(s.pixel_undistorted if undistorted else s.pixel_raw,
                        "#ff525f" if s.color == "red" else "#4da6ff",
                        f"{s.shot_id} · {s.shot_label}", s.shot_id, s.shot_id == selected)
