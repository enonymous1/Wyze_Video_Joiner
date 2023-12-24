import os

from PyQt5.QtWidgets import QMainWindow, QAction, QFileDialog, QListWidget, QLabel, QVBoxLayout, QWidget, QPushButton, \
    QProgressDialog, QMessageBox
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from wyze_video_manager import VideoJoiner


class VideoJoinerThread(QThread):
    # Define a progress signal
    progress = pyqtSignal(int)

    # Initialize the VideoJoinerThread with a directory and the number of files
    def __init__(self, directory, file_count):
        super().__init__()
        self.directory = directory
        self.file_count = file_count

    # Define the run method that is called when the thread starts
    def run(self):
        # Create a VideoJoiner
        joiner = VideoJoiner(self.directory)
        # Join the videos and emit the progress signal
        for i in range(self.file_count):
            joiner.join_videos()
            self.progress.emit(i + 1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Create a QLabel to display the introductory text
        self.file_count = None
        self.list_widget = None
        self.label = QLabel("Welcome to Wyze Video Joiner!<br>Please open a directory to get started.", self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 14pt; font-weight: bold;")  # Set the font size and weight
        self.setCentralWidget(self.label)

        # Create a QLabel to display the directory path
        self.path_label = QLabel(self)
        self.path_label.setMargin(10)  # Add some padding

        # Set minimum size of the window (width, height)
        self.setMinimumSize(400, 300)

        # Set the title of the window
        self.setWindowTitle("Wyze Video Joiner")

        # Set the window icon
        self.setWindowIcon(QIcon('icon_title_bar.png'))

        # Create a menu bar
        menu_bar = self.menuBar()

        # Create a File menu
        file_menu = menu_bar.addMenu("File")

        # Create an Open action
        open_action = QAction("Open", self)
        open_action.triggered.connect(self.open_directory)

        # Add the Open action to the File menu
        file_menu.addAction(open_action)

        # Create an Exit action
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)

        # Add the Exit action to the File menu
        file_menu.addAction(exit_action)

        # Create a QPushButton to join the videos
        self.join_button = QPushButton("Join Videos", self)
        self.join_button.clicked.connect(self.join_videos)
        self.join_button.hide()  # Hide the button initially

        # Add a reference to the QThread
        self.thread = None

    def open_directory(self):
        # Open a directory picker dialog
        directory = QFileDialog.getExistingDirectory(self, "Open Directory")

        # If a directory was selected, display its contents
        if directory:
            self.label.hide()  # Hide the label
            self.path_label.setText(directory)  # Set the text of the path label
            # Store the number of files in the directory
            self.file_count = len(os.listdir(directory))

            # Create a QListWidget
            self.list_widget = QListWidget(self)

            # Create a QVBoxLayout
            layout = QVBoxLayout()
            layout.addWidget(self.path_label)  # Add the path label to the layout
            layout.addWidget(self.list_widget)  # Add the list widget to the layout

            # Show the join button
            self.join_button.show()

            # Add the join button to the layout
            layout.addWidget(self.join_button)

            # Create a QWidget to hold the layout
            widget = QWidget(self)
            widget.setLayout(layout)

            # Set the QWidget as the central widget
            self.setCentralWidget(widget)

            # Add the directory contents to the QListWidget
            self.list_widget.addItems(os.listdir(directory))

    def join_videos(self):
        # Create a QProgressDialog with the maximum value set to the number of files in the directory
        progress = QProgressDialog("Joining videos...", None, 0, self.file_count, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setWindowTitle("Working")  # Set the title of the progress dialog

        # Apply the stylesheet to the progress dialog
        with open('gui_stylesheet.qss', 'r') as f:
            progress.setStyleSheet(f.read())

        # Create a VideoJoinerThread and connect its progress signal to the progress dialog
        self.thread = VideoJoinerThread(self.path_label.text(), self.file_count)
        self.thread.progress.connect(progress.setValue)

        # Start the thread
        self.thread.start()

        # Show the progress dialog
        progress.exec_()

    def show_error(self, message):
        QMessageBox.critical(self, "Error", message)
