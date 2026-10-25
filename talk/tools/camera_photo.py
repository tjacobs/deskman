#!/usr/bin/env python3

# Ask the robot for a photo from its camera, and print where the file is

# Imports
import os
import shutil
import sys

# Import the robot socket helper from the text folder
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'text'))
import robot_move

# Main
def main():
    # Read where to save a copy, if asked
    save = parse_args()

    # Take the photo, and copy it when a place was given
    path = robot_move.camera_photo()
    if save:
        shutil.copyfile(path, save)
        path = save
    print(path)

# Parse args, an optional path to save a copy to
def parse_args():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return ''

# Main
if __name__ == '__main__':
    main()
