"""Lazy Qt launcher for the optional desktop extra."""
from __future__ import annotations


def run() -> int:
    """Load the Qt runtime only when the desktop launcher is explicitly used."""
    try:
        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
    except ImportError as exc:
        raise RuntimeError(
            "Yuki Desktop requires the optional dependencies. "
            "Install them with: python -m pip install -r requirements-desktop.txt"
        ) from exc

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.setWindowTitle("Yuki")
    window.setMinimumSize(720, 480)
    window.setCentralWidget(QLabel(
        "Yuki Desktop foundation is installed.\n"
        "Connect an authenticated desktop session to enable companion features."
    ))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
