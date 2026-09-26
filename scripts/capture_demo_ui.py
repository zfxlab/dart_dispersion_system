#!/usr/bin/env python3
"""Capture the actual Qt UI for a saved Session, without modifying that Session."""
import argparse
from pathlib import Path
from PySide6.QtWidgets import QApplication
from plane_picker.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session",type=Path)
    parser.add_argument("output",type=Path)
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle("Fusion")
    w = MainWindow()
    w.service.load_session(args.session)
    w.show()
    app.processEvents()
    w.sync_flags()
    w.refresh(image_changed=True,fit=True)
    app.processEvents()
    if not w.grab().save(str(args.output),"PNG"):
        raise ValueError("截图保存失败")
    w.close()


if __name__ == "__main__":
    main()
