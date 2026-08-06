#!/usr/bin/env python3

# Head look control through deskman robot Unix socket

# Imports
import json
import os
import socket

# Config
SOCKET_PATH = os.environ.get("ROBOT_SOCK", "/tmp/robot.socket")
LOOK_DEFAULT_DEGREES = 60
CONNECT_TIMEOUT_SEC = 2.0
READ_TIMEOUT_SEC = 5.0

# Main
def main():
    # Look center by default
    print(look("center"))

# Turn the head left, right, or center via the deskman control socket
def look(direction, degrees=LOOK_DEFAULT_DEGREES):
    direction = str(direction or "left").lower()
    if direction == "center":
        reply = send_command({"command": "center"})
    else:
        reply = send_command({"command": "move", "direction": direction, "degrees": float(degrees)})
    if not reply.get("ok"):
        error = reply.get("error", "unknown error")
        raise RuntimeError(error)
    if direction == "center":
        return "Centered."
    return f"Moved {direction}."

# Send one JSON line and read one JSON reply line
def send_command(payload):
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(CONNECT_TIMEOUT_SEC)
        sock.connect(SOCKET_PATH)
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

# Main
if __name__ == "__main__":
    main()
