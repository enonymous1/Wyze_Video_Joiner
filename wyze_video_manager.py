# Import necessary modules
import os
import traceback
from pathlib import Path

import ffmpeg
from PyQt5.QtCore import QObject, pyqtSignal


# Define a VideoJoiner class that inherits from QObject
class VideoJoiner(QObject):
    # Define signals for progress, finished, and error events
    progress = pyqtSignal(int)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    # Initialize the VideoJoiner with a directory
    def __init__(self, directory):
        super().__init__()
        self.file_count = None
        self.directory = directory  # Store the directory

    # Define a method to join videos
    def join_videos(self):
        try:
            # Get the list of all mp4 files in the directory
            files = list(Path(self.directory).glob('*.mp4'))
            if not files:
                raise ValueError("No .mp4 files found in the directory")

            # Create a list of input files
            input_files = [ffmpeg.input(str(f)) for f in files]

            # Concatenate the input files
            concatenated = ffmpeg.concat(*input_files, n=len(input_files))

            # Define the output path
            output_path = os.path.join(self.directory, 'output.mp4')

            # Set the output of the concatenated videos to the output path
            joined = concatenated.output(output_path)

            # Run the ffmpeg command
            ffmpeg.run(joined, overwrite_output=True)

        except Exception as e:
            # If an error occurs, emit the error signal with the error message
            self.error.emit(str(e))
            # Log the full traceback
            print(traceback.format_exc())
