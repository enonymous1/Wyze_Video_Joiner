"""Optional PyQt6 GUI. The core stays importable without Qt installed.

This replaces the original PyQt5 GUI and fixes its bugs: the join runs
exactly once (not once per clip), the file list shows only the clips
that will actually be joined, in sorted order, errors are shown in a
dialog instead of vanishing into the console, and Cancel stops ffmpeg
instead of being ignored.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wyze_joiner.core import (
    WyzeJoinerError,
    discover_clips,
    join_jobs,
    plan_join,
)

_ASSETS = Path(__file__).parent


class JoinWorker(QThread):
    """Runs planning + joining off the GUI thread."""

    progress = pyqtSignal(float, str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        directory: Path,
        output: Path | None,
        recursive: bool,
        split_by_day: bool,
        strategy: str,
        force: bool,
    ) -> None:
        super().__init__()
        self._directory = directory
        self._output = output
        self._recursive = recursive
        self._split_by_day = split_by_day
        self._strategy = strategy
        self._force = force
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            jobs = plan_join(
                self._directory,
                output=self._output,
                recursive=self._recursive,
                strategy=self._strategy,
                force=self._force,
                split_by_day=self._split_by_day,
            )
            outputs = join_jobs(
                jobs,
                on_progress=lambda fraction, message: self.progress.emit(fraction, message),
                should_cancel=self._cancel.is_set,
            )
        except WyzeJoinerError as exc:
            self.error.emit(str(exc))
        else:
            self.finished.emit([str(path) for path in outputs])


class MainWindow(QMainWindow):
    """The single window: folder picker, clip list, options, join/cancel."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Wyze Video Joiner")
        self.setMinimumSize(520, 420)
        icon_path = _ASSETS / "icon_title_bar.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._worker: JoinWorker | None = None

        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Folder containing Wyze clips…")
        browse_folder = QPushButton("Browse…")
        browse_folder.clicked.connect(self._browse_folder)

        self.list_widget = QListWidget()

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output (default: joined.mp4 in the folder)")
        browse_output = QPushButton("Browse…")
        browse_output.clicked.connect(self._browse_output)

        self.recursive_check = QCheckBox("Include subfolders")
        # The list shows what discovery would find, so toggling the
        # option must refresh it — otherwise it shows the old set.
        self.recursive_check.toggled.connect(lambda _checked: self._refresh_list())
        self.split_check = QCheckBox("One file per day")
        self.force_check = QCheckBox("Overwrite existing output")
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(["auto", "copy", "reencode"])
        self.strategy_combo.setToolTip("auto: stream copy when clips match, re-encode otherwise")

        self.join_button = QPushButton("Join Videos")
        self.join_button.clicked.connect(self._start_join)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_join)
        self.cancel_button.setEnabled(False)

        self.progress_bar = QProgressBar()
        self.status_label = QLabel("Open a folder to get started.")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Clips folder:"))
        layout.addWidget(self.folder_edit)
        layout.addWidget(browse_folder)
        layout.addWidget(self.list_widget)
        layout.addWidget(QLabel("Output:"))
        layout.addWidget(self.output_edit)
        layout.addWidget(browse_output)
        layout.addWidget(self.recursive_check)
        layout.addWidget(self.split_check)
        layout.addWidget(self.force_check)
        layout.addWidget(QLabel("Strategy:"))
        layout.addWidget(self.strategy_combo)
        layout.addWidget(self.join_button)
        layout.addWidget(self.cancel_button)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)

        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open Folder…", self)
        open_action.triggered.connect(self._browse_folder)
        file_menu.addAction(open_action)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def _browse_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open Directory")
        if directory:
            self.folder_edit.setText(directory)
            self._refresh_list()
            if not self.output_edit.text():
                self.output_edit.setText(str(Path(directory) / "joined.mp4"))

    def _browse_output(self) -> None:
        if self.split_check.isChecked():
            directory = QFileDialog.getExistingDirectory(self, "Output Folder")
            if directory:
                self.output_edit.setText(directory)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Output Video", "joined.mp4", "MP4 Video (*.mp4)"
            )
            if path:
                self.output_edit.setText(path)

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        directory = Path(self.folder_edit.text()) if self.folder_edit.text() else None
        if directory is None or not directory.is_dir():
            return
        try:
            clips = discover_clips(directory, recursive=self.recursive_check.isChecked())
        except WyzeJoinerError as exc:
            self.status_label.setText(str(exc))
            return
        self.list_widget.addItems([clip.name for clip in clips])
        self.status_label.setText(f"{len(clips)} clip(s) found.")

    def _start_join(self) -> None:
        directory_text = self.folder_edit.text()
        if not directory_text:
            QMessageBox.warning(self, "No folder", "Choose a folder of clips first.")
            return
        output_text = self.output_edit.text()
        output = Path(output_text) if output_text else None
        self.join_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Planning…")
        self._worker = JoinWorker(
            Path(directory_text),
            output,
            self.recursive_check.isChecked(),
            self.split_check.isChecked(),
            str(self.strategy_combo.currentText()),
            self.force_check.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _cancel_join(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("Cancelling…")

    def _on_progress(self, fraction: float, message: str) -> None:
        self.progress_bar.setValue(int(fraction))
        self.status_label.setText(f"{message} {fraction:.0f}%")

    def _on_finished(self, outputs: list[str]) -> None:
        self.join_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setValue(100)
        self.status_label.setText("Done: " + ", ".join(str(path) for path in outputs))
        self._refresh_list()

    def _on_error(self, message: str) -> None:
        self.join_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Failed.")
        QMessageBox.critical(self, "Error", message)


def main() -> None:
    app = QApplication(sys.argv)
    stylesheet_path = _ASSETS / "gui_stylesheet.qss"
    if stylesheet_path.is_file():
        app.setStyleSheet(stylesheet_path.read_text())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
