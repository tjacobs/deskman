#!/usr/bin/env python3

# Head look control through deskman socket, or robot look.py fallback

# Imports
import os
import re
import subprocess
import sys

# Config
ROBOT_SRC = os.path.expanduser("~/robot/src")
LOOK_DEFAULT_DEGREES = 90
LOOK_DIRECTIONS = ["left", "right", "center", "up", "down", "hat_up", "hat_down"]
LOOK_WORDS = ("look", "turn", "face", "hat")
LOOK_ALIASES = {"left": "left", "right": "right", "up": "up", "down": "down", "center": "center", "centre": "center", "straight": "center", "ahead": "center", "forward": "center", "forwards": "center"}
GET_BATTERY_RETRY_PROMPT = "Do not guess. Call get_battery now, then answer using only the tool result."
RESTART_COMMAND = "/usr/local/bin/deskman-restart-services"
RESTART_MESSAGE = "Restarting!"
RESTART_WORD = "restart"
QUIT_WORDS = ("quit", "exit")
QUIT_MESSAGE = "Goodbye!"
QUIT_PENDING = False

# Tools the local model can call for the head and pack
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": "Move my head or hat. Call this immediately. Do not speak first.",
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
    {
        "type": "function",
        "function": {
            "name": "restart",
            "description": "Restart the robot and teleport services now.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "silence",
            "description": "Stop talking and end the conversation. Call when asked to shut up, be quiet, or be silent.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quit",
            "description": "Exit the robot and this program. Call when asked to quit or exit.",
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

# Direction the spoken words ask for, or none when this is not a look
def look_arguments(prompt):
    text = str(prompt or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    words = text.split()

    # Only a head or hat command moves, so other talk of left and right is left alone
    if not any(word in words for word in LOOK_WORDS):
        return None

    # The hat has its own up and down
    if "hat" in words:
        if "up" in words:
            direction = "hat_up"
        elif "down" in words:
            direction = "hat_down"
        else:
            return None
    else:
        direction = next((LOOK_ALIASES[word] for word in words if word in LOOK_ALIASES), None)
    if direction is None:
        return None

    # Take a spoken angle, otherwise go all the way
    degrees = next((int(word) for word in words if word.isdigit()), LOOK_DEFAULT_DEGREES)
    return {"direction": direction, "degrees": degrees}

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

# Bounce robot and teleport after this process can finish
def run_restart():
    print("Restarting robot and teleport.", flush=True)
    try:
        subprocess.Popen(["sudo", "-n", RESTART_COMMAND], start_new_session=True)
    except Exception as error:
        return f"Restart failed: {error}"
    return RESTART_MESSAGE

# Return true when the command asks to restart the services
def needs_restart(prompt):
    text = str(prompt or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\brobot\b", " ", text)
    text = " ".join(text.split())
    return text == RESTART_WORD or text.startswith(RESTART_WORD)

# End the conversation without a spoken reply
def run_silence():
    return "Ready."

# Return true when the person wants talking to stop
def needs_silence(prompt):
    text = str(prompt or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\brobot\b", " ", text)
    text = " ".join(text.split())
    if "shut up" in text or "be quiet" in text:
        return True
    words = text.split()
    return "silence" in words or "quiet" in words

# Exit the robot and this program
def run_quit():
    global QUIT_PENDING
    QUIT_PENDING = True
    try:
        from robot_move import quit_robot
        quit_robot()
    except Exception as error:
        return f"Quit failed: {error}"
    return QUIT_MESSAGE

# True after quit was requested
def quit_pending():
    return QUIT_PENDING

# Return true when the command asks to exit
def needs_quit(prompt):
    text = str(prompt or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    words = text.split()
    return any(word in words for word in QUIT_WORDS)

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
