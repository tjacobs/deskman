#!/usr/bin/env python3

# System and model info for the robot, hardware, uname, and LLM stats

# Imports
import json
import os
import platform
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime

import client

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(SCRIPT_DIR, "models")
SYSTEM_RETRY_PROMPT = "Do not guess. Call get_system_info now, then answer using only the tool result."

# Tools the local model can call for system info
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "Get computer and model info, uname, hardware like Jetson or Raspberry Pi, LLM model name, parameter count, quantization, context size, and model file date. Required for questions about the computer, hardware, or which model is running.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

# Main
def main():
    # Print system info by default
    print(run_get_system_info())

# Return true when the user asked about the computer or model
def needs_system_info(prompt):
    text = prompt.lower()
    if re.search(r"\b(uname|jetson|raspberry|hardware|computer|machine|device)\b", text):
        return True
    if re.search(r"\bwhat (computer|machine|device|hardware)\b", text):
        return True
    if re.search(r"\b(running on|am i on|are you on|what.?re you running)\b", text):
        return True
    if re.search(r"\b(which|what)\s+(model|llm)\b", text):
        return True
    if re.search(r"\b(what'?s|what is)\s+your\s+model\b", text):
        return True
    if re.search(r"\b(1b|2b|e2b|parameter|quantiz|gguf)\b", text):
        return True
    if re.search(r"\bmodel\s+(name|size|info|file|stats?)\b", text):
        return True
    return bool(re.search(r"\b(training|trained)\b.*\b(date|context|length)\b|\bcontext\s+(size|length|window)\b", text))

# Retry get_system_info once, then read it in Python if still missing
def force_get_system_info(prompt, messages, message, already_retried, record_tool):
    # First miss, ask the model again with an explicit system info order
    if not already_retried:
        print("[system] missing get_system_info, retrying", flush=True)
        if message is not None:
            messages.append(message)
        messages.append({"role": "user", "content": SYSTEM_RETRY_PROMPT})
        return True

    # Second miss, read it directly in Python
    result = run_get_system_info()
    record_tool("get_system_info", {}, result)
    print("[system] forced get_system_info", flush=True)
    return result

# Build a short system and model info report
def run_get_system_info(arguments=None):
    lines = []

    # Host and kernel
    lines.append(f"uname: {read_uname()}")
    lines.append(f"Hardware: {read_hardware_name()}")
    lines.append(f"Architecture: {platform.machine()}")
    lines.append(f"CPU cores: {os.cpu_count() or 'unknown'}")
    lines.append(f"Memory: {read_memory_summary()}")

    # Live LLM model from the local server when available
    model_info = read_model_info()
    if model_info:
        lines.extend(model_info)
    else:
        lines.append("LLM model: unavailable, server not responding.")

    return "\n".join(lines)

# Run uname -a
def read_uname():
    result = subprocess.run(["uname", "-a"], capture_output=True, text=True)
    if result.returncode != 0:
        return platform.platform()
    return result.stdout.strip()

# Detect Jetson, Raspberry Pi, or a generic host name
def read_hardware_name():
    # Device tree model is the best label on Jetson and Pi
    model_path = "/proc/device-tree/model"
    if os.path.isfile(model_path):
        with open(model_path, "rb") as model_file:
            text = model_file.read().decode("utf-8", errors="ignore").strip("\x00").strip()
            if text:
                return short_hardware_name(text)

    # Jetson L4T release file
    tegra_path = "/etc/nv_tegra_release"
    if os.path.isfile(tegra_path):
        return "NVIDIA Jetson"

    # Raspberry Pi cpuinfo
    cpuinfo_path = "/proc/cpuinfo"
    if os.path.isfile(cpuinfo_path):
        with open(cpuinfo_path, encoding="utf-8", errors="ignore") as cpuinfo_file:
            text = cpuinfo_file.read().lower()
            if "raspberry pi" in text:
                return "Raspberry Pi"

    return platform.node() or "unknown"

# Shorten long board names to a short spoken label
def short_hardware_name(text):
    lowered = text.lower()
    if "orin nano" in lowered:
        return "NVIDIA Jetson Orin Nano"
    if "jetson" in lowered:
        return "NVIDIA Jetson"
    if "raspberry pi" in lowered:
        return "Raspberry Pi"
    return text

# Summarize total and available memory
def read_memory_summary():
    try:
        result = subprocess.run(["free", "-h"], capture_output=True, text=True)
    except OSError:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    for line in result.stdout.splitlines():
        if not line.lower().startswith("mem:"):
            continue
        parts = line.split()
        if len(parts) >= 7:
            return f"{parts[1]} total, {parts[6]} available"
        if len(parts) >= 2:
            return f"{parts[1]} total"
    return "unknown"

# Read model alias, params, quant, context, and file date from the server
def read_model_info():
    lines = []
    try:
        models_payload = get_json(client.MODELS_URL)
        props_payload = get_json(client.PROPS_URL)
    except urllib.error.URLError:
        return None

    # Alias and path from props
    alias = props_payload.get("model_alias") or ""
    model_path = props_payload.get("model_path") or ""
    model_ftype = props_payload.get("model_ftype") or ""
    if alias:
        lines.append(f"LLM model: {alias}")
    if model_path:
        lines.append(f"Model file: {os.path.basename(model_path)}")
    if model_ftype:
        lines.append(f"Quantization: {model_ftype}")

    # Parameter count and training context from /v1/models meta
    meta = model_meta(models_payload, alias)
    if meta:
        params = meta.get("n_params")
        if params:
            lines.append(f"Parameters: {format_parameter_count(params)}")
        train_context = meta.get("n_ctx_train")
        if train_context:
            lines.append(f"Training context length: {train_context} tokens")
        size = meta.get("size")
        if size:
            lines.append(f"Model size: {format_bytes(size)}")

    # Live context window
    settings = props_payload.get("default_generation_settings") or {}
    context_size = settings.get("n_ctx") or (settings.get("params") or {}).get("n_ctx")
    if context_size:
        lines.append(f"Context size: {context_size} tokens")

    # Model file date as a stand-in when training date is not in the GGUF
    if model_path and os.path.isfile(model_path):
        modified = datetime.fromtimestamp(os.path.getmtime(model_path)).astimezone()
        lines.append(f"Model file date: {modified.strftime('%Y-%m-%d')}")

    return lines

# Pick the matching model meta block from /v1/models
def model_meta(models_payload, alias):
    for item in models_payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        if alias and item.get("id") != alias:
            continue
        meta = item.get("meta")
        if isinstance(meta, dict):
            return meta
    for item in models_payload.get("data") or []:
        meta = item.get("meta") if isinstance(item, dict) else None
        if isinstance(meta, dict):
            return meta
    return None

# GET JSON from the local LLM server
def get_json(url):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {client.API_KEY}"})
    with urllib.request.urlopen(request, timeout=5) as result:
        return json.load(result)

# Format a parameter count for speech
def format_parameter_count(count):
    count = int(count)
    if count >= 1_000_000_000:
        billions = count / 1_000_000_000
        return f"{billions:.2f} billion".rstrip("0").rstrip(".") + " parameters"
    if count >= 1_000_000:
        millions = count / 1_000_000
        return f"{millions:.1f} million".rstrip("0").rstrip(".") + " parameters"
    return f"{count} parameters"

# Format a byte size for speech
def format_bytes(size):
    size = float(size)
    gib = size / (1024 ** 3)
    if gib >= 1:
        return f"{gib:.2f} GiB"
    mib = size / (1024 ** 2)
    return f"{mib:.0f} MiB"

# Main
if __name__ == "__main__":
    main()
