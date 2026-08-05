#!/usr/bin/env python3

# Head look control through robot look.py

# Imports
import os
import sys

# Config
ROBOT_SRC = os.path.expanduser("~/robot/src")
LOOK_DEFAULT_DEGREES = 60

# Tools the local model can call for head movement
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": "Turn the robot head left, right, or center.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "center"],
                        "description": "Which way to face.",
                    },
                    "degrees": {
                        "type": "number",
                        "description": f"How far to turn for left or right, default {LOOK_DEFAULT_DEGREES}, max 90.",
                    },
                },
                "required": ["direction"],
            },
        },
    },
]

# Main
def main():
    # Look center by default
    print(run_look({"direction": "center"}))

# Call robot look.py to turn the head
def run_look(arguments):
    # Import look from the robot source tree
    look = load_robot_look()
    if look is None:
        return "Look is unavailable."

    # Read direction and optional degrees
    direction = arguments.get("direction", "left")
    degrees = arguments.get("degrees", LOOK_DEFAULT_DEGREES)
    try:
        return look.look(direction, degrees)
    except Exception as error:
        return f"Look failed: {error}"

# Import ~/robot/src/look.py once
def load_robot_look():
    if "look" in sys.modules:
        return sys.modules["look"]
    if not os.path.isdir(ROBOT_SRC):
        return None
    if ROBOT_SRC not in sys.path:
        sys.path.insert(0, ROBOT_SRC)
    try:
        import look
    except ImportError:
        return None
    return look

# Main
if __name__ == "__main__":
    main()
