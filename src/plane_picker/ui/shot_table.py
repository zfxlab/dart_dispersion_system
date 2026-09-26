from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView


class ShotTable(QTableWidget):
    selected = Signal(int)

    def __init__(self):
        super().__init__(0, 6)
        self.setHorizontalHeaderLabels(["shot_id", "标签", "颜色", "X / mm", "Y / mm", "原始像素"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.itemSelectionChanged.connect(self._selection)

    def _selection(self):
        if self.currentRow() >= 0 and self.item(self.currentRow(),0):
            self.selected.emit(int(self.item(self.currentRow(),0).text()))

    def update_shots(self, shots, selected=None):
        self.blockSignals(True)
        self.setRowCount(len(shots))
        for row,s in enumerate(shots):
            values = [s.shot_id, s.shot_label, "红" if s.color == "red" else "蓝",
                      f"{s.plane_mm[0]:.3f}", f"{s.plane_mm[1]:.3f}", f"{s.pixel_raw[0]:.2f}, {s.pixel_raw[1]:.2f}"]
            for col,v in enumerate(values):
                self.setItem(row,col,QTableWidgetItem(str(v)))
            if s.shot_id == selected:
                self.selectRow(row)
        self.blockSignals(False)
