#!/usr/bin/env python3

# Record and play back videos on the robot, through the deskman socket

# Imports
import robot_move

# What to say once a recording starts, stops, or plays
RECORD_START_REPLY = "Recording."
RECORD_STOP_REPLY = "Stopped recording."
PLAY_REPLY = "Playing the last recording."

# Tools the model can call to record and play video
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "record_video",
            "description": "Start or stop recording a video on my camera. Call this immediately. Do not speak first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "boolean",
                        "description": "True starts recording, false stops it.",
                    },
                },
                "required": ["start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_video",
            "description": "Play the video I recorded most recently on my screen.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Main
def main():
    # Say what the robot is holding, without recording anything
    print(run_play_video())

# Start or stop a recording
def run_record_video(arguments):
    start = arguments.get("start", True)
    try:
        robot_move.set_recording(start)
    except Exception as error:
        return f"Recording failed: {error}"
    return RECORD_START_REPLY if start else RECORD_STOP_REPLY

# Play the newest recording on the robot screen
def run_play_video():
    try:
        robot_move.play_last_recording()
    except Exception as error:
        return f"Playing failed: {error}"
    return PLAY_REPLY

# Main
if __name__ == "__main__":
    main()
