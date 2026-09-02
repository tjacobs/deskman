#!/usr/bin/env python3

# Head look control through deskman robot Unix socket

# Imports
import json
import os
import socket

# Config
LOOK_DEFAULT_DEGREES = 90
LOOK_MAX_DEGREES = 90
HEAD_PERCENT_MAX = 100
CONNECT_TIMEOUT_SEC = 2.0
READ_TIMEOUT_SEC = 5.0

# Map spoken names onto socket directions
DIRECTION_ALIASES = {
    "left": "left",
    "right": "right",
    "center": "center",
    "forward": "center",
    "straight": "center",
    "straight forward": "center",
    "straight_forward": "center",
    "ahead": "center",
    "up": "up",
    "down": "down",
    "hat_up": "hat_up",
    "hat up": "hat_up",
    "raise": "hat_up",
    "open": "hat_up",
    "opened": "hat_up",
    "hat open": "hat_up",
    "hat_open": "hat_up",
    "hat_down": "hat_down",
    "hat down": "hat_down",
    "lower": "hat_down",
    "close": "hat_down",
    "closed": "hat_down",
    "shut": "hat_down",
    "hat close": "hat_down",
    "hat closed": "hat_down",
    "hat_close": "hat_down",
}

# Main
def main():
    # Look center by default
    print(look("center", LOOK_DEFAULT_DEGREES))

# Turn the head via the deskman control socket
def look(direction, degrees):
    direction = normalize_direction(direction)
    percent = degrees_to_percent(degrees)
    if direction == "center":
        reply = send_command({"command": "center"})
    elif direction == "left" or direction == "right":
        reply = send_command({"command": "move", "direction": direction, "degrees": float(percent * LOOK_MAX_DEGREES / HEAD_PERCENT_MAX)})
    elif direction == "up":
        reply = send_command({"command": "move", "y": percent})
    elif direction == "down":
        reply = send_command({"command": "move", "y": -percent})
    elif direction == "hat_up":
        reply = send_command({"command": "move", "hat": HEAD_PERCENT_MAX - percent})
    elif direction == "hat_down":
        reply = send_command({"command": "move", "hat": percent})
    else:
        raise RuntimeError(f"unknown direction {direction}")

    # Fail if the robot rejected the move
    if not reply.get("ok"):
        error = reply.get("error", "unknown error")
        raise RuntimeError(error)

    # Speak from my point of view, not the listener's
    if direction == "center":
        return "Looking straight forward."
    if direction == "left":
        return "Looked to my left."
    if direction == "right":
        return "Looked to my right."
    if direction == "up":
        return "Looked up."
    if direction == "down":
        return "Looked down."
    if direction == "hat_up":
        return "Moved my hat up."
    if direction == "hat_down":
        return "Moved my hat down."
    return f"Moved {direction}."

# Map 0-90 degrees onto 0-100 percent of travel
def degrees_to_percent(degrees):
    try:
        value = float(degrees)
    except (TypeError, ValueError):
        value = float(LOOK_DEFAULT_DEGREES)
    if value <= 0:
        value = float(LOOK_DEFAULT_DEGREES)
    if value > LOOK_MAX_DEGREES:
        value = LOOK_MAX_DEGREES
    return int(round(value / LOOK_MAX_DEGREES * HEAD_PERCENT_MAX))

# Lowercase and collapse aliases like hat up
def normalize_direction(direction):
    text = str(direction or "left").lower().replace("-", " ").strip()
    text = " ".join(text.split())
    if text in DIRECTION_ALIASES:
        return DIRECTION_ALIASES[text]
    return text.replace(" ", "_")

# Read pack percent from the robot, or None when the socket is down
def battery_percent():
    try:
        reply = send_command({"command": "battery"})
    except Exception:
        return None
    if not reply.get("ok"):
        return None
    try:
        return int(round(float(reply.get("percent"))))
    except (TypeError, ValueError):
        return None

# Ask the robot process to exit, it also tells teleport to exit
def quit_robot():
    reply = send_command({"command": "quit"})
    if not reply.get("ok"):
        error = reply.get("error", "unknown error")
        raise RuntimeError(error)

# Send one JSON line and read one JSON reply line
def send_command(payload):
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(CONNECT_TIMEOUT_SEC)
        sock.connect(robot_interface_path())
        sock.settimeout(READ_TIMEOUT_SEC)
        sock.sendall(raw)
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    line = data.decode("utf-8", errors="replace").strip().splitlines()
    if not line:
        return {"ok": False, "error": "empty reply from robot"}
    return json.loads(line[0])

# ROBOT_SOCK override, else $XDG_RUNTIME_DIR/robot.interface
def robot_interface_path():
    override = os.environ.get("ROBOT_SOCK")
    if override:
        return override

    # Match teleport.interface under the per-user runtime dir
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        runtime = f"/run/user/{os.getuid()}"
    if not os.path.isdir(runtime):
        runtime = "/tmp"
    return os.path.join(runtime, "robot.interface")

# Main
if __name__ == "__main__":
    main()
