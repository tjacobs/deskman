#!/usr/bin/env python3

# Head look control through deskman socket, or robot look.py fallback

# Imports
import os
import re
import sys

# Config
ROBOT_SRC = os.path.expanduser("~/robot/src")
LOOK_DEFAULT_DEGREES = 90
LOOK_DIRECTIONS = ["left", "right", "center", "up", "down", "hat_up", "hat_down"]
GET_BATTERY_RETRY_PROMPT = "Do not guess. Call get_battery now, then answer using only the tool result."

# Tools the local model can call for the head and pack
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": "Move my head or hat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": LOOK_DIRECTIONS,
                        "description": "left, right, center, up, and down turn the head. hat_up raises the hat. hat_down lowers the hat.",
                    },
                    "degrees": {
                        "type": "number",
                        "description": f"Travel in degrees, {LOOK_DEFAULT_DEGREES} is all the way.",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_battery",
            "description": "Get the robot pack battery percent and voltage.",
            "parameters": {"type": "object", "properties": {}},
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

# Read pack percent and voltage from the robot socket
def run_get_battery():
    try:
        from robot_move import battery_reading
        percent, voltage = battery_reading()
    except Exception as error:
        return f"Battery is unavailable: {error}"
    if percent is None or voltage is None:
        return "Battery meter is not available."
    return f"Battery is {percent} percent, {voltage:.2f} volts."

# Return true when the user wants the pack level
def needs_get_battery(prompt):
    text = prompt.lower()
    if re.search(r"\bbatter(y|ies)\b", text):
        return True
    return bool(re.search(r"\b(charge|voltage)\b", text) and re.search(r"\b(what|how|percent|left)\b", text))

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
