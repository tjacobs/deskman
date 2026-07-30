#!/usr/bin/env python3

# Imports
import json
import sys
import urllib.error
import urllib.request

# Config
API_URL = "http://127.0.0.1:8080/v1/chat/completions"
API_KEY = "local"
MODEL = "gemma-4-e2b"
DEFAULT_PROMPT = "Introduce yourself in one short sentence."
SYSTEM_PROMPT = "You are a helpful robot. Answer in one or two short sentences suitable for speaking aloud."
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 100
TEMPERATURE = 0.7

# Main
def main():
    # Parse prompt
    prompt = parse_args()

    # Ask local model
    response = ask_model(prompt)
    print(response)

# Parse prompt from command line
def parse_args():
    # Use supplied text or a useful default
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:])
    return DEFAULT_PROMPT

# Send one chat request to llama-server
def ask_model(prompt):
    # Build request body
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }

    # Send request
    request = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as result:
            response = json.load(result)
    except urllib.error.URLError as error:
        print(f"LLM unavailable at {API_URL}: {error.reason}")
        sys.exit(1)

    # Return generated text
    return response["choices"][0]["message"]["content"].strip()

# Main
if __name__ == "__main__":
    main()
