#!/usr/bin/env python3

# Head look control through deskman socket, or robot look.py fallback

# Imports
import os
import sys

# Config
ROBOT_SRC = os.path.expanduser("~/robot/src")
LOOK_DEFAULT_DEGREES = 90
LOOK_DIRECTIONS = ["left", "right", "center", "forward", "straight", "up", "down", "hat_up", "hat_down", "hat_open", "hat_close"]

# Tools the local model can call for head movement
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": "Move my head or hat. left and right are my left and my right. forward, straight, and center look straight forward. hat_up or hat_open raises my hat, hat_down or hat_close lowers it. Default is all the way, 90. After the tool, say my right or my hat, not your.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": LOOK_DIRECTIONS,
                        "description": "Which way to face. Use forward or straight to look straight ahead. hat_up or hat_open is my hat up, hat_down or hat_close is my hat down.",
                    },
                    "degrees": {
                        "type": "number",
                        "description": f"How far to move. Default {LOOK_DEFAULT_DEGREES} is all the way. 90 is full travel. Never use 0. All the way, maximum, and minimum use 90.",
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

# Call deskman robot_move, or robot look.py, to turn the head
def run_look(arguments):
    # Read direction and optional degrees
    direction = arguments.get("direction", "left")
    degrees = arguments.get("degrees", LOOK_DEFAULT_DEGREES)

    # Prefer the deskman Unix socket
    try:
        from robot_move import look as socket_look
        return socket_look(direction, degrees)
    except Exception as error:
        socket_error = error

    # Fall back to ~/robot/src/look.py when present
    look = load_robot_look()
    if look is not None:
        try:
            return look.look(direction, degrees)
        except Exception as error:
            return f"Look failed: {error}"

    return f"Look is unavailable: {socket_error}"

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
