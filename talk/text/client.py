#!/usr/bin/env python3

# Interface to the local or cloud LLM.
# Grows context on the local server when a request exceeds the window.

# Imports
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Config
DEFAULT_MODEL = "gemma-4-e2b"
DEFAULT_CLOUD_MODEL = "gpt-4o-mini"
DEFAULT_CLOUD_BASE = "https://api.openai.com"
DEFAULT_CONTEXT_SIZE = 4096
MAX_TOKENS = 100
API_BASE = "http://127.0.0.1:8080"
API_KEY = "local"
API_URL = f"{API_BASE}/v1/chat/completions"
MODELS_URL = f"{API_BASE}/v1/models"
PROPS_URL = f"{API_BASE}/props"
HEALTH_URL = f"{API_BASE}/health"
APPLY_TEMPLATE_URL = f"{API_BASE}/apply-template"
TOKENIZE_URL = f"{API_BASE}/tokenize"
CACHE_URL = f"{API_BASE}/slots"
LLM_LOCAL = "local"
LLM_CLOUD = "cloud"
LLM_ENV_NAME = "TALK_LLM"
CLOUD_MODEL_ENV_NAME = "TALK_CLOUD_MODEL"
CLOUD_BASE_ENV_NAME = "TALK_CLOUD_BASE"
OPENAI_KEY_ENV_NAME = "OPENAI_API_KEY"
MAX_CONTEXT_SIZE = 16384
REQUEST_TIMEOUT_SECONDS = 300
GROW_HEADROOM = 256
GROW_ATTEMPTS = 2
READY_TIMEOUT_SECONDS = 180
READY_POLL_SECONDS = 0.5
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GROW_REQUEST_PATH = os.path.join(SCRIPT_DIR, "cache", "grow_to")
OPENAI_ENV_FILE = os.path.join(os.path.dirname(SCRIPT_DIR), "openai.env")

# Cloud path state, TALK_LLM and flags fill these in
cloud_enabled = False
cloud_model = DEFAULT_CLOUD_MODEL
cloud_base = DEFAULT_CLOUD_BASE

# Read OPENAI_API_KEY from talk/openai.env when the environment has none
def load_openai_env_file():
    if os.environ.get(OPENAI_KEY_ENV_NAME, "").strip():
        return
    if not os.path.isfile(OPENAI_ENV_FILE):
        return
    with open(OPENAI_ENV_FILE, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line.startswith(OPENAI_KEY_ENV_NAME + "="):
                continue
            value = line.split("=", 1)[1].strip().strip("'\"")
            if value:
                os.environ[OPENAI_KEY_ENV_NAME] = value
            return

# Read TALK_LLM, model, and base from the environment
def load_cloud_env():
    global cloud_enabled, cloud_model, cloud_base
    load_openai_env_file()
    if env_wants_cloud():
        cloud_enabled = True
    env_model = os.environ.get(CLOUD_MODEL_ENV_NAME, "").strip()
    if env_model:
        cloud_model = env_model
    env_base = os.environ.get(CLOUD_BASE_ENV_NAME, "").strip()
    if env_base:
        cloud_base = env_base.rstrip("/")

# Turn on cloud from --cloud or TALK_LLM, and pick model and base URL
def apply_cloud_settings(cloud_flag, model_name):
    global cloud_enabled, cloud_model
    load_cloud_env()
    if cloud_flag:
        cloud_enabled = True
    if model_name:
        cloud_model = model_name
    if cloud_enabled:
        require_cloud_key()

# True when TALK_LLM selects the cloud path
def env_wants_cloud():
    return os.environ.get(LLM_ENV_NAME, LLM_LOCAL).strip().lower() == LLM_CLOUD

# True when chat completions go to the cloud
def use_cloud():
    return cloud_enabled

# Stay on the local server even if TALK_LLM asked for cloud
def disable_cloud():
    global cloud_enabled
    cloud_enabled = False

# Quit when cloud is on and OPENAI_API_KEY is missing
def require_cloud_key():
    if cloud_api_key():
        return
    print("Error: OPENAI_API_KEY is required for --cloud or TALK_LLM=cloud.", flush=True)
    sys.exit(1)

# Cloud API key from the environment
def cloud_api_key():
    return os.environ.get(OPENAI_KEY_ENV_NAME, "").strip()

# Bearer token for the current chat path
def chat_api_key():
    if use_cloud():
        return cloud_api_key()
    return API_KEY

# Chat completions URL for local llama-server or the cloud host
def chat_api_url():
    if use_cloud():
        return f"{cloud_base}/v1/chat/completions"
    return API_URL

# Cloud model alias
def cloud_model_name():
    return cloud_model

# Short provider label for logs
def cloud_provider_name():
    if "openai.com" in cloud_base:
        return "OpenAI"
    return cloud_base

# Apply TALK_LLM from the environment at import
load_cloud_env()

# Send a chat-completions request, asking server.sh to grow context on overflow
def request_chat(body, api_key, timeout_seconds):
    if use_cloud():
        try:
            return post_chat(body, api_key, timeout_seconds)
        except urllib.error.HTTPError as error:
            error_body = error.read().decode(errors="replace")
            raise urllib.error.URLError(f"HTTP {error.code}: {error_body.strip() or error.reason}") from error
    last_error = None
    for attempt in range(GROW_ATTEMPTS + 1):
        try:
            return post_chat(body, api_key, timeout_seconds)
        except urllib.error.HTTPError as error:
            last_error = error
            error_body = error.read().decode(errors="replace")
            max_tokens = body.get("max_tokens") or MAX_TOKENS
            if not grow_after_overflow(error.code, error_body, max_tokens):
                raise urllib.error.URLError(f"HTTP {error.code}: {error_body.strip() or error.reason}") from error
    raise urllib.error.URLError(str(getattr(last_error, "reason", last_error) or "chat failed after context grow"))

# POST one chat-completions request to the current server
def post_chat(body, api_key, timeout_seconds):
    request = urllib.request.Request(chat_api_url(), data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as result:
        return json.load(result)

# If this is a context overflow, request a grow and wait for the new size
def grow_after_overflow(status_code, error_body, max_tokens):
    overflow = parse_context_overflow(status_code, error_body)
    if not overflow:
        return False
    new_context_size = next_context_size(overflow["n_prompt_tokens"], overflow["n_ctx"], max_tokens)
    print(f"Context full ({overflow['n_prompt_tokens']}/{overflow['n_ctx']}), requesting grow to {new_context_size}...", flush=True)
    write_grow_request(new_context_size)
    wait_for_context_size(new_context_size)
    return True

# Parse an exceed_context_size_error body from llama-server
def parse_context_overflow(status_code, error_body):
    if status_code != 400:
        return None
    try:
        payload = json.loads(error_body)
    except json.JSONDecodeError:
        return None
    error = payload.get("error") or {}
    if error.get("type") != "exceed_context_size_error":
        return None
    prompt_tokens = int(error.get("n_prompt_tokens") or 0)
    context_size = int(error.get("n_ctx") or 0)
    if prompt_tokens <= 0 or context_size <= 0:
        return None
    return {"n_prompt_tokens": prompt_tokens, "n_ctx": context_size}

# Choose the next context size that fits the prompt plus reply room
def next_context_size(prompt_tokens, current_context_size, max_tokens):
    needed_tokens = prompt_tokens + int(max_tokens) + GROW_HEADROOM
    new_context_size = current_context_size
    while new_context_size < needed_tokens:
        new_context_size *= 2
    if new_context_size > MAX_CONTEXT_SIZE:
        new_context_size = MAX_CONTEXT_SIZE
    if new_context_size <= current_context_size:
        raise urllib.error.URLError(f"request needs about {needed_tokens} tokens but context is already at max {MAX_CONTEXT_SIZE}")
    return new_context_size

# Ask server.sh to restart llama-server with a larger context
def write_grow_request(context_size):
    os.makedirs(os.path.dirname(GROW_REQUEST_PATH), exist_ok=True)
    with open(GROW_REQUEST_PATH, "w", encoding="utf-8") as grow_file:
        grow_file.write(str(context_size))

# Wait until the server is healthy with at least the requested context size
def wait_for_context_size(context_size):
    deadline = time.time() + READY_TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            current_size = read_live_context_size()
            health_request = urllib.request.Request(HEALTH_URL, headers={"Authorization": f"Bearer {API_KEY}"})
            with urllib.request.urlopen(health_request, timeout=2) as result:
                payload = json.load(result)
            if payload.get("status") == "ok" and current_size is not None and current_size >= context_size:
                print(f"Server ready with context {current_size}.", flush=True)
                return
        except urllib.error.URLError:
            pass
        time.sleep(READY_POLL_SECONDS)
    raise urllib.error.URLError(f"server did not become ready after growing context to {context_size}")

# Read n_ctx from the live server, None when unavailable
def read_live_context_size():
    request = urllib.request.Request(PROPS_URL, headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(request, timeout=2) as result:
        payload = json.load(result)
    settings = payload.get("default_generation_settings") or {}
    context_size = settings.get("n_ctx") or (settings.get("params") or {}).get("n_ctx")
    if not context_size:
        return None
    return int(context_size)

# Model alias that uses JSON tool calls in content instead of native tool_calls
CONTENT_TOOLS_MODEL = "gemma-3-1b"

# System add-on that teaches that model to emit tool JSON in content
def content_tools_instruction(tool_names):
    names = ", ".join(tool_names)
    text = (
        "When you need a tool, reply with ONLY valid JSON and nothing else, "
        'for example {"name":"get_time","arguments":{}}. '
        f"Allowed names: {names}. "
        "arguments must be a JSON object. "
        'For calculate use {"name":"calculate","arguments":{"expression":"7 + 8"}}. '
        'For look use {"direction":"left"|"right"|"center"|"up"|"down"|"hat_up"|"hat_down"} and degrees 90 for all the way. '
        'For set_volume use {"percent":N}. '
        'For set_voice use {"voice":"..."}. '
        'For remember or forget use {"text":"..."}. '
        'For set_reminder use {"name":"...","time":"HH:MM"}. '
        'For cancel_reminder use {"name":"..."}. '
        'For load_talk_log use {"date":"today"|"yesterday"|"YYYY-MM-DD"}. '
        'For get_system_info use {"name":"get_system_info","arguments":{}}. '
    )
    if "play_sonos" in tool_names:
        text += (
            'For play_sonos or pause_sonos use {} or {"room":"all"}. '
            'For set_sonos_volume use {"percent":N} or {"percent":N,"room":"all"}. '
        )
    if "list_calendar_events" in tool_names:
        text += (
            'For list_calendar_events use {"day":"today"|"tomorrow"|"YYYY-MM-DD"}. '
            'For get_next_calendar_event use {"name":"get_next_calendar_event","arguments":{}}.'
        )
    return text
