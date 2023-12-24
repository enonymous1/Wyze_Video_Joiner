# Import necessary modules from PyQt5
from PyQt5.QtWidgets import QApplication
from gui_manager import MainWindow

# Create a new QApplication instance
app = QApplication([])

# Read the stylesheet from the 'gui_stylesheet.qss' file
with open('gui_stylesheet.qss', 'r') as f:
    stylesheet = f.read()

# Set the stylesheet for the application
app.setStyleSheet(stylesheet)

# Create a new MainWindow instance
window = MainWindow()

# Show the MainWindow
window.show()

# Start the QApplication event loop
app.exec_()
