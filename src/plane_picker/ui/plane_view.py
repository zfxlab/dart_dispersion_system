import math
import numpy as np
from .image_view import NavigableView, pen


class PlaneView(NavigableView):
    """Scene position is (X, -Y), so text remains upright and +Y points up."""
    def draw(self, layout, mapper, shots, selected=None, grid_mm=100., validation=None):
        self.scene().clear()
        bounds = [[0,0]]
        if layout:
            for tag_id in layout.tags:
                p = layout.corners(tag_id) * [1,-1]
                bounds.extend(p.tolist())
                self.polygon(p, "#8d9eb8", "#293648")
                self.label(f"Tag {tag_id}", *p.mean(axis=0))
        if mapper:
            p = mapper.polygon * [1,-1]
            bounds.extend(p.tolist())
            self.polygon(p, "#43d69a")
        bounds.extend([[s.plane_mm[0],-s.plane_mm[1]] for s in shots])
        b = np.asarray(bounds)
        low, high = b.min(axis=0)-grid_mm, b.max(axis=0)+grid_mm
        # Bound item count for unusually large layouts.
        step = max(grid_mm, math.ceil(max(high-low) / grid_mm / 80) * grid_mm)
        for x in np.arange(math.floor(low[0]/step)*step, high[0]+step, step):
            item = self.scene().addLine(x,low[1],x,high[1],pen("#303c4f"))
            item.setZValue(-5)
            self.label(f"{x:g}", x, high[1], "#8394ad")
        for y in np.arange(math.floor(low[1]/step)*step, high[1]+step, step):
            item = self.scene().addLine(low[0],y,high[0],y,pen("#303c4f"))
            item.setZValue(-5)
            self.label(f"{-y:g}", low[0], y, "#8394ad")
        self.scene().addLine(low[0],0,high[0],0,pen("#d3dce9",2))
        self.scene().addLine(0,low[1],0,high[1],pen("#d3dce9",2))
        self.label("+X / mm →", high[0],0)
        self.label("↑ +Y / mm", 0,low[1])
        for s in shots:
            self.marker([s.plane_mm[0],-s.plane_mm[1]], "#ff525f" if s.color == "red" else "#4da6ff",
                        f"{s.shot_id} · {s.shot_label}", s.shot_id, s.shot_id == selected)
        if validation:
            for target in validation.targets:
                self.marker([target["x_mm"],-target["y_mm"]], "#e5b6ff", target["point_id"])
            for r in validation.results:
                self.marker([r["measured_x_mm"],-r["measured_y_mm"]], "#43d69a", f"{r['error_mm']:.2f} mm")
