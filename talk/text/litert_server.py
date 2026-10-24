#!/usr/bin/env python3

# Serve Gemma through LiteRT-LM with an OpenAI style chat API, keeping the prompt in the model cache between requests

# Imports
import http.server
import json
import os
import sys
import threading
import time

import litert_lm

# Config the address, the same one llama-server uses so client.py needs no new URL
HOST = '127.0.0.1'
PORT = 8080

# Config the model, and how many tokens the model can hold at once
MODEL_NAME = 'gemma-4-E2B-it.litertlm'
MODEL_PATH = os.path.expanduser('~/.litert-lm/models/gemma-4-E2B-it.litertlm/model.litertlm')
CONTEXT_TOKENS = 4096

# Config when the cached conversation is rebuilt, a long one leaves no room for the reply
REBUILD_TOKENS = 3000

# Config the wildcard for assistant replies, the cached conversation holds the model's own words not the ones the client stored
REPLY_KEY = 'assistant reply'

# State the model and the one cached conversation, protected by a lock because the model answers one request at a time
ENGINE = None
CACHE = {'conversation': None, 'keys': [], 'tools_key': ''}
LOCK = threading.Lock()
CALL_COUNTER = [0]

# Main
def main():
    # Parse args
    host, port = parse_args()

    # Load the model, then answer requests
    load_engine()
    serve(host, port)

# Parse command line arguments
def parse_args():
    host = HOST
    port = PORT
    arguments = sys.argv[1:]
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == '--host' and index + 1 < len(arguments):
            index += 1
            host = arguments[index]
        elif argument == '--port' and index + 1 < len(arguments):
            index += 1
            port = int(arguments[index])
        else:
            print('Usage: ./litert_server.py [--host address] [--port number]')
            sys.exit(0 if argument in ('-h', '--help') else 1)
        index += 1

    # Return the address
    return host, port

# Load the model onto the GPU
def load_engine():
    global ENGINE
    print(f'Loading {MODEL_NAME}...', flush=True)
    start = time.perf_counter()
    ENGINE = litert_lm.Engine(MODEL_PATH, backend=litert_lm.Backend.GPU(), max_num_tokens=CONTEXT_TOKENS, use_ringbuffers_local_attention=True)
    ENGINE.__enter__()
    print(f'Loaded in {time.perf_counter() - start:.1f} sec.', flush=True)

# Answer requests until stopped
def serve(host, port):
    server = http.server.ThreadingHTTPServer((host, port), RequestHandler)
    print(f'API: http://{host}:{port}/v1', flush=True)
    server.serve_forever()

# Handle the three requests talk makes, the model list, health, and chat
class RequestHandler(http.server.BaseHTTPRequestHandler):
    # Answer model list and health checks
    def do_GET(self):
        # Say the model is ready, the model list and health both answer once it loads
        if self.path == '/health':
            self.send_json(200, {'status': 'ok'})
        elif self.path == '/v1/models':
            self.send_json(200, {'object': 'list', 'data': [{'id': MODEL_NAME, 'object': 'model'}]})
        else:
            self.send_json(404, {'error': {'message': 'not found'}})

    # Answer chat requests
    def do_POST(self):
        # Only chat completions are supported
        if self.path != '/v1/chat/completions':
            self.send_json(404, {'error': {'message': 'not found'}})
            return

        # Answer the chat, and reset the cache when the model fails so the next request starts clean
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)))
            self.send_json(200, answer_chat(body))
        except Exception as error:
            reset_cache()
            print(f'Error: {error}', flush=True)
            self.send_json(500, {'error': {'message': str(error)}})

    # Write a JSON reply
    def send_json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # Stay quiet about each request, the chat lines say what happened
    def log_message(self, message_format, *arguments):
        pass

# Answer one chat request, sending only the messages the model has not seen yet when it still holds the rest
def answer_chat(body):
    # Translate the messages and work out what each one is called for matching
    messages = body['messages']
    tools = body.get('tools') or []
    tool_names = {call['id']: call['function']['name'] for message in messages for call in message.get('tool_calls') or []}
    translated = [translate_message(message, tool_names) for message in messages]
    keys = [message_key(message) for message in translated]
    tools_key = json.dumps(tools, sort_keys=True)

    # Send the new messages and keep the model's words in the cache
    with LOCK:
        start = time.perf_counter()
        pending, mode = choose_pending(translated, keys, tools, tools_key, body)
        response = send_pending(pending)
        reply = build_reply(response)
        CACHE['keys'].append(message_key(translate_message(reply, {})))
        tokens = CACHE['conversation'].token_count
        print(f'{mode}, {len(pending)} new, {tokens} tokens, {time.perf_counter() - start:.2f} sec', flush=True)

    # Return the reply in the shape the OpenAI API uses
    return {'object': 'chat.completion', 'model': MODEL_NAME, 'choices': [{'index': 0, 'message': reply, 'finish_reason': 'tool_calls' if reply.get('tool_calls') else 'stop'}], 'usage': {'prompt_tokens': tokens, 'completion_tokens': 0, 'total_tokens': tokens}}

# Turn one OpenAI message into the LiteRT form, a tool reply must name the call it answers
def translate_message(message, tool_names):
    role = message.get('role')
    if role == 'tool':
        return {'role': 'tool', 'content': [{'type': 'tool_response', 'name': tool_names.get(message.get('tool_call_id'), ''), 'response': message.get('content')}]}
    if role == 'assistant' and message.get('tool_calls'):
        calls = [{'type': 'function', 'function': {'name': call['function']['name'], 'arguments': json.loads(call['function'].get('arguments') or '{}')}} for call in message['tool_calls']]
        return {'role': 'assistant', 'tool_calls': calls}
    return {'role': role, 'content': message.get('content') or ''}

# Name a message so two requests can be compared, assistant words are not compared
def message_key(message):
    if message['role'] == 'assistant' and 'tool_calls' not in message:
        return REPLY_KEY
    return json.dumps(message, sort_keys=True)

# Pick the messages to send, only the new ones when the cache holds the start of this request, else rebuild from the whole request
def choose_pending(translated, keys, tools, tools_key, body):
    # Count how many cached messages this request starts with
    cached = CACHE['keys']
    matched = 0
    while matched < len(cached) and matched < len(keys) and cached[matched] == keys[matched]:
        matched += 1

    # Reuse the cache when it all matches and the new messages can be replayed
    pending = translated[matched:]
    reusable = CACHE['conversation'] is not None and CACHE['tools_key'] == tools_key and matched == len(cached) and pending and pending[-1]['role'] in ('user', 'tool') and CACHE['conversation'].token_count < REBUILD_TOKENS
    if reusable and all(message['role'] == 'tool' or message['role'] == 'assistant' and 'tool_calls' not in message for message in pending[:-1]):
        return pending, 'cached'

    # Start a new conversation holding everything but the last message
    reset_cache()
    sampler = litert_lm.SamplerConfig(temperature=body['temperature']) if body.get('temperature') is not None else None
    CACHE['conversation'] = ENGINE.create_conversation(messages=translated[:-1], tools=[ProxyTool(tool) for tool in tools] or None, automatic_tool_calling=False, constrained_decoding_config=litert_lm.ConstrainedDecodingConfig(enable=False), sampler_config=sampler)
    CACHE['keys'] = keys[:-1]
    CACHE['tools_key'] = tools_key
    return translated[-1:], 'rebuilt'

# Tool the model can ask for, the client runs it so this only describes it
class ProxyTool(litert_lm.Tool):
    # Keep the tool description
    def __init__(self, definition):
        self.definition = definition

    # Give the model the tool description
    def get_tool_description(self):
        return self.definition

    # Never run here
    def execute(self, param):
        raise NotImplementedError('The client runs tools.')

# Send each new message, a tool reply that is not the last one is a reply the client already settled, so the model answers it and the answer is dropped
def send_pending(pending):
    conversation = CACHE['conversation']
    response = None
    for index, message in enumerate(pending):
        # Skip the reply the client stored, the cache already holds the model's own
        if message['role'] == 'assistant' and 'tool_calls' not in message:
            continue

        # Send the message and remember it
        response = conversation.send_message(message)
        CACHE['keys'].append(message_key(message))
        if index < len(pending) - 1:
            CACHE['keys'].append(REPLY_KEY)

    # Return the answer to the last message
    return response

# Turn the model's answer into an OpenAI assistant message
def build_reply(response):
    # Join the spoken text, it comes back as a list of parts
    content = response.get('content') or ''
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content)
    reply = {'role': 'assistant', 'content': content or None}

    # Add each tool call with an id the client sends back with the result
    calls = response.get('tool_calls') or []
    if calls:
        reply['tool_calls'] = []
        for call in calls:
            CALL_COUNTER[0] += 1
            reply['tool_calls'].append({'id': f'call_{CALL_COUNTER[0]}', 'type': 'function', 'function': {'name': call['function']['name'], 'arguments': json.dumps(call['function'].get('arguments') or {})}})
    return reply

# Drop the cached conversation
def reset_cache():
    if CACHE['conversation'] is not None:
        CACHE['conversation'].close()
    CACHE['conversation'] = None
    CACHE['keys'] = []
    CACHE['tools_key'] = ''

# Run
if __name__ == '__main__':
    main()
