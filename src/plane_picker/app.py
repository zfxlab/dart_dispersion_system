import argparse
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="AprilTag 平面点选定位工具")
    parser.add_argument("--config",help="应用 YAML 配置")
    parser.add_argument("--smoke-test",action="store_true",help="启动 UI 后自动退出，用于环境检查")
    args = parser.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer, QStandardPaths
    from .ui.main_window import MainWindow
    from .storage.yaml_io import read_yaml
    from .storage.shot_ids import ShotIdAllocator
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("plane_picker")
    app.setStyle("Fusion")
    app.setStyleSheet("QMainWindow, QWidget { font-size: 13px; } QToolBar { spacing: 5px; padding: 4px; }")
    allocator = None if args.smoke_test else ShotIdAllocator(
        Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "shot_ids.sqlite")
    window = MainWindow(read_yaml(args.config) if args.config else None,allocator)
    window.show()
    if args.smoke_test:
        QTimer.singleShot(400,app.quit)
    return app.exec()
