#!/usr/bin/env python3

# Stand in for llama-server with fixed replies, for boards too small to hold a text model, it answers the same endpoints on the same port so client.py needs no changes

# Imports
import argparse
import http.server
import itertools
import json
import re

# Config the address, the same one llama-server uses
HOST = '127.0.0.1'
PORT = 8080

# Config how the robot is named when asked what it runs on
MODEL_NAME = 'prog'
RUNTIME_NAME = 'Fixed replies, no model'
CONTEXT_TOKENS = 4096

# Config the replies, a word in the question picks one, and the rest are used in turn
KEYWORD_REPLIES = (
    (('hello', 'hi', 'hey'), 'Hello! I am running with fixed replies on this small board.'),
    (('name', 'who are you'), 'I am Deskman, your desk robot.'),
    (('how are you',), 'I am doing well, thank you.'),
    (('thanks', 'thank you'), 'You are welcome.'),
    (('bye', 'goodnight'), 'Goodbye!'),
)
FALLBACK_REPLIES = (
    'I only have a few fixed replies on this board.',
    'My thinking model is switched off here, so I cannot answer that.',
    'Sorry, I can only say simple things right now.',
    'That is a good question, but this board is too small for me to answer it.',
)

# State the cycle that hands out the fallback replies in turn
FALLBACK_CYCLE = itertools.cycle(FALLBACK_REPLIES)

# Main
def main():
    # Read the address to serve on
    args = parse_args()

    # Serve until stopped
    serve(args.host, args.port)

# Read the command line
def parse_args():
    parser = argparse.ArgumentParser(description='Serve fixed replies on the llama-server port.')
    parser.add_argument('--host', default=HOST, help='address to serve on')
    parser.add_argument('--port', type=int, default=PORT, help='port to serve on')
    return parser.parse_args()

# Serve until stopped
def serve(host, port):
    server = http.server.ThreadingHTTPServer((host, port), RequestHandler)
    print(f'Model: {MODEL_NAME}', flush=True)
    print(f'API: http://{host}:{port}/v1', flush=True)
    server.serve_forever()

# Handle the requests talk makes, the model list, model properties, health, and chat
class RequestHandler(http.server.BaseHTTPRequestHandler):
    # Answer model list, model properties, and health checks
    def do_GET(self):
        # Say the server is ready
        if self.path == '/health':
            self.send_json(200, {'status': 'ok'})
        elif self.path == '/v1/models':
            self.send_json(200, {'object': 'list', 'data': [{'id': MODEL_NAME, 'object': 'model', 'meta': {'size': 0}}]})
        elif self.path == '/props':
            self.send_json(200, {'model_alias': MODEL_NAME, 'model_path': '', 'runtime': RUNTIME_NAME, 'default_generation_settings': {'n_ctx': CONTEXT_TOKENS}})
        else:
            self.send_json(404, {'error': {'message': 'not found'}})

    # Answer chat requests
    def do_POST(self):
        # Only chat completions are supported
        if self.path != '/v1/chat/completions':
            self.send_json(404, {'error': {'message': 'not found'}})
            return

        # Read the chat and reply with a fixed line
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)))
        reply = choose_reply(body.get('messages') or [])
        self.send_json(200, {'object': 'chat.completion', 'model': MODEL_NAME, 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': reply}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}})

    # Write a JSON reply
    def send_json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # Stay quiet about each request
    def log_message(self, message_format, *arguments):
        pass

# Pick the reply for the last thing the user said, a keyword match first and the next fallback otherwise
def choose_reply(messages):
    # Find the last user message, its content may be a list of parts
    question = ''
    for message in reversed(messages):
        if message.get('role') == 'user':
            content = message.get('content') or ''
            if isinstance(content, list):
                content = ' '.join(part.get('text', '') for part in content)
            question = content.lower()
            break

    # Reply to the first keyword found
    for keywords, reply in KEYWORD_REPLIES:
        if any(re.search(rf'\b{re.escape(keyword)}\b', question) for keyword in keywords):
            return reply

    # Otherwise take the next fallback in turn
    return next(FALLBACK_CYCLE)

# Run
if __name__ == '__main__':
    main()
