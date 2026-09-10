#!.venv/bin/python

# Realtime.py streams microphone audio up to OpenAI and plays the spoken audio reply.

# Imports
import os
import sys
import json
import time
import numpy
import queue
import base64
import argparse
import threading
import websocket
import subprocess

# Imports from the text helper
SCRIPT_DIR = __file__.rsplit('/', 1)[0]
sys.path.insert(0, f'{SCRIPT_DIR}/text')
import ask
import client
import utils

# Config the session, config.json overrides these
REALTIME_MODEL = 'gpt-realtime-2.1-mini'
REALTIME_VOICE = 'echo'
REALTIME_ACCENT = 'You are a British man from London. Speak with a natural British accent.'
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.json')

# Config the connection
REALTIME_URL = 'wss://api.openai.com/v1/realtime'
TURN_DETECTION = 'semantic_vad'

# Config the transcriber, the model hears the audio itself, this only writes the heard lines to the log
TRANSCRIPTION_MODEL = 'gpt-4o-mini-transcribe'

# Config the timing
CONNECT_TIMEOUT = 30
RECEIVE_TIMEOUT = 0.2
IDLE_SECONDS = 20.0
APPEND_BYTES = 32000
BLOCKS_PER_SECOND = 10
REALTIME_RATE = 24000

# Config the reply instructions added to the robot system prompt
SPOKEN_STYLE = 'You are speaking out loud, so keep replies to one or two short sentences.'

# Config tools to hold back, these pick a kokoro voice that OpenAI is not speaking with
SKIP_TOOLS = ('set_voice', 'list_voices')

# Hold one realtime conversation from the microphone
def main():
    # Parse arguments
    arguments = parse_args()

    # Load config
    config = load_config()

    # Load from the command line, overriding config.json
    if arguments.model:
        config['realtime_model'] = arguments.model
    if arguments.voice:
        config['realtime_voice'] = arguments.voice

    # Open the microphone before the socket
    microphone = Microphone()

    # Open the session
    session = open_session(config)

    # Talk until the room goes quiet
    try:
        # Run the conversation
        run_conversation(session, microphone, arguments.ask, ignore_turn)
    except KeyboardInterrupt:
        # Stop the conversation
        print('Stopped.', flush=True)
    finally:
        # Close the session
        close_session(session)
        microphone.stop()

# Parse command line arguments
def parse_args():
    parser = argparse.ArgumentParser(description='Talk to OpenAI with audio up and audio down.')
    parser.add_argument('ask', nargs='?', help='send this question first, instead of waiting for speech')
    parser.add_argument('--model', help='realtime model to use, overrides config.json')
    parser.add_argument('--voice', help='voice to speak with, overrides config.json')
    return parser.parse_args()

# Read config.json, falling back to the defaults above for anything missing
def load_config():
    # Default values
    config = {'realtime_model': REALTIME_MODEL, 'realtime_voice': REALTIME_VOICE, 'realtime_accent': REALTIME_ACCENT}

    # Load from config.json
    try:
        # Load
        with open(CONFIG_PATH) as handle:
            config.update(json.load(handle))
    except (OSError, ValueError):
        print(f'No readable {os.path.basename(CONFIG_PATH)}, using defaults.', flush=True)
    return config

# Connect to the realtime endpoint and configure the session
def open_session(config):
    # Get the API key
    key = client.cloud_api_key()
    if not key:
        raise SystemExit('No OpenAI key, set OPENAI_API_KEY or write talk/openai.env.')

    # Open the socket, the receive timeout lets the reply loop check the idle clock
    model = config['realtime_model']
    print(f'Connecting to {model}, voice {config["realtime_voice"]}...', flush=True)
    socket = websocket.create_connection(f'{REALTIME_URL}?model={model}', header=[f'Authorization: Bearer {key}'], timeout=CONNECT_TIMEOUT)
    socket.settimeout(RECEIVE_TIMEOUT)
    session = Session(socket)

    # Describe the voice, the turn taking, and the tools before any audio moves
    session.send({'type': 'session.update', 'session': {
        'type': 'realtime',
        'output_modalities': ['audio'],
        'audio': {
            'input': {'format': {'type': 'audio/pcm', 'rate': REALTIME_RATE},
                      'turn_detection': {'type': TURN_DETECTION},
                      'transcription': {'model': TRANSCRIPTION_MODEL}},
            'output': {'format': {'type': 'audio/pcm', 'rate': REALTIME_RATE},
                       'voice': config['realtime_voice']}},
        'instructions': session_instructions(config['realtime_accent']),
        'tools': session_realtime_tools(),
        'tool_choice': 'auto'}})
    return session

# Build the system prompt, the robot personality plus a reminder that this reply is spoken
def session_instructions(accent):
    # Load the system prompt
    instructions = ask.load_system_prompt()

    # Append memories and reminders, the same facts the text path sees
    extras = ask.prompt_extras()
    if extras:
        instructions = instructions + '\n\n' + extras

    # Append the accent and spoken style
    instructions = instructions + '\n\n' + accent + ' ' + SPOKEN_STYLE

    # Log the instructions
    if False:
        print(instructions, flush=True)

    # Return the instructions
    return instructions

# Flatten the chat style tool list into the shape a realtime session expects
def session_realtime_tools():
    # Build the tool list
    tools = []
    for tool in ask.active_tools():
        function = tool['function']
        if function['name'] in SKIP_TOOLS:
            continue
        tools.append({'type': 'function', 'name': function['name'],
                      'description': function.get('description', ''),
                      'parameters': function.get('parameters', {})})
    return tools

# Stream microphone audio up and play replies back until the room goes quiet
def run_conversation(session, microphone, first_question, on_turn):
    # Set the callback for when a turn is finished
    session.on_turn = on_turn

    # Send an opening question when one was passed, the wake word already heard it
    if first_question:
        # Set the heard text to the first question
        session.heard = first_question

        # Print the question
        print(f'Asking: {first_question}', flush=True)

        # Send the question as a user turn and ask for a spoken answer
        ask_question(session, first_question)

    # Push microphone audio up in the background while this loop handles replies
    sender = threading.Thread(target=send_microphone, args=(session, microphone), daemon=True)
    sender.start()

    # Open the speaker
    speaker = Speaker()

    # Set the idle deadline
    idle_deadline = time.time() + IDLE_SECONDS

    # Handle events
    try:
        # While the idle deadline is not reached
        while time.time() < idle_deadline:
            # Receive an event
            event = session.receive()
            if event is None:
                continue

            # Any traffic means someone is still talking, so push the idle clock out
            if handle_event(session, microphone, speaker, event):
                idle_deadline = time.time() + IDLE_SECONDS
    finally:
        # Stop the session
        session.stop()
        speaker.stop()
        microphone.unmute()

    # Print the conversation closed
    print('Conversation closed.', flush=True)

# Send one written question as a user turn and ask for a spoken answer
def ask_question(session, question):
    # Send the question as a user turn and ask for a spoken answer
    session.send({'type': 'conversation.item.create', 'item': { 'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': question}]}})
    session.send({'type': 'response.create'})

# Read microphone blocks, resample them, and append them to the input buffer
def send_microphone(session, microphone):
    # While running
    while session.running:
        # Get the next block
        block = microphone.next_block()
        if block is None:
            continue

        # Resample to the only rate the realtime API takes, then send as signed 16 bit integer bytes
        samples = resample(block, utils.SAMPLE_RATE, REALTIME_RATE)
        audio = (numpy.clip(samples, -1.0, 1.0) * 32767).astype(numpy.int16).tobytes()
        for start in range(0, len(audio), APPEND_BYTES):
            # Encode the chunk as base64
            chunk = base64.b64encode(audio[start:start + APPEND_BYTES]).decode()

            # Send the chunk
            session.send({'type': 'input_audio_buffer.append', 'audio': chunk})

# Stretch samples from the microphone rate onto the realtime rate
def resample(samples, source_rate, target_rate):
    # If the rates are the same, return the samples
    if source_rate == target_rate:
        return samples

    # Interpolate onto the new sample count, the microphone is already band limited
    count = int(round(len(samples) * target_rate / source_rate))
    source_points = numpy.arange(len(samples))
    target_points = numpy.linspace(0, len(samples) - 1, count)
    return numpy.interp(target_points, source_points, samples)

# Act on one server event, return true when it counted as activity
def handle_event(session, microphone, speaker, event):
    # Get the event type
    kind = event.get('type', '')

    # Play the reply as it arrives, with the microphone off so it does not hear itself
    if kind == 'response.output_audio.delta':
        microphone.mute()
        speaker.play(base64.b64decode(event['delta']))
        return True

    # Let the microphone back in once the speaker has drained
    if kind == 'response.output_audio.done':
        speaker.drain()
        microphone.unmute()
        return True

    # Print both sides of the conversation, and log them as a pair once the reply lands
    if kind == 'conversation.item.input_audio_transcription.completed':
        session.heard = event.get('transcript', '').strip()
        if not session.heard:
            return False
        print(f'Heard: {session.heard}', flush=True)
        return True

    # If the reply is done, print it and call the callback
    if kind == 'response.output_audio_transcript.done':
        # Get the reply
        reply = event.get('transcript', '').strip()

        # Print the reply
        print(f'Reply: {reply}', flush=True)

        # Call the callback
        session.on_turn(session.heard, reply)

        # Clear the heard text
        session.heard = ''
        return True

    # Run the tool here and hand the result back, the tools are all local
    if kind == 'response.function_call_arguments.done':
        run_function_call(session, event)
        return True

    # Ask for the spoken answer once the turn that called the tools has finished
    if kind == 'response.done':
        if session.tool_pending:
            session.tool_pending = False
            session.send({'type': 'response.create'})
        return True

    # Show why the server gave up
    if kind == 'error':
        print(f'Realtime error: {event.get("error", {}).get("message", "")}', flush=True)
        return False
    return False

# Run one tool locally and return its result to the model
def run_function_call(session, event):
    # Build the call
    call = {'function': {'name': event.get('name'), 'arguments': event.get('arguments')}}
    result = ask.run_tool(call)

    # Hand the result back, the answer is asked for once the whole turn is done
    session.send({'type': 'conversation.item.create', 'item': {
        'type': 'function_call_output', 'call_id': event.get('call_id'), 'output': str(result)}})
    session.tool_pending = True

# Drop a finished turn, running standalone there is nowhere to log it
def ignore_turn(heard, reply):
    return

# Close the socket, the session is finished
def close_session(session):
    # Stop the session
    session.stop()

    # Close the socket
    try:
        session.socket.close()
    except OSError:
        pass


# A realtime socket that is safe to send on from more than one thread
class Session:
    def __init__(self, socket):
        self.socket = socket
        self.lock = threading.Lock()
        self.running = True
        self.tool_pending = False
        self.heard = ''
        self.on_turn = ignore_turn

    # Send one event as JSON
    def send(self, event):
        with self.lock:
            if not self.running:
                return
            try:
                self.socket.send(json.dumps(event))
            except (OSError, websocket.WebSocketException):
                self.running = False

    # Return the next event, or None when nothing arrived before the timeout
    def receive(self):
        try:
            return json.loads(self.socket.recv())
        except websocket.WebSocketTimeoutException:
            return None
        except (OSError, ValueError, websocket.WebSocketException):
            self.running = False
            return None

    # Stop sending, the conversation is over
    def stop(self):
        self.running = False


# The microphone read in the background, so no words are lost between turns
class Microphone:
    def __init__(self):
        self.blocks = queue.Queue()
        self.muted = False
        self.recorder = utils.start_recorder(utils.record_command())
        self.reader = threading.Thread(target=self.read_blocks, daemon=True)
        self.reader.start()

    # Read blocks until the recorder stops, dropping them while muted
    def read_blocks(self):
        block_bytes = utils.SAMPLE_RATE * 2 // BLOCKS_PER_SECOND
        while True:
            data = self.recorder.stdout.read(block_bytes)
            if len(data) < block_bytes:
                self.blocks.put(None)
                return
            if self.muted:
                continue
            self.blocks.put(numpy.frombuffer(data, dtype=numpy.int16).astype(numpy.float32) / 32768.0)

    # Return the next block, or None when nothing arrived before the timeout
    def next_block(self):
        try:
            return self.blocks.get(timeout=RECEIVE_TIMEOUT)
        except queue.Empty:
            return None

    # Stop passing audio on, the robot is speaking
    def mute(self):
        self.muted = True
        self.drop_queued()

    # Start passing audio on again, dropping whatever leaked in while muted
    def unmute(self):
        self.muted = False
        self.drop_queued()

    # Throw away blocks waiting to be sent
    def drop_queued(self):
        while not self.blocks.empty():
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                break

    # Stop the recorder
    def stop(self):
        self.recorder.terminate()


# Raw audio written straight to the speaker as it arrives
class Speaker:
    def __init__(self):
        self.player = None

    # Start a player on the first chunk, then keep feeding the same one
    def play(self, chunk):
        if self.player is None:
            self.player = subprocess.Popen([utils.audio_player(), '-q', '-f', 'S16_LE', '-r', str(REALTIME_RATE), '-c', '1', '-t', 'raw'], stdin=subprocess.PIPE)
        try:
            self.player.stdin.write(chunk)
            self.player.stdin.flush()
        except (BrokenPipeError, ValueError):
            self.player = None

    # Wait for the reply to finish playing
    def drain(self):
        if self.player is None:
            return
        try:
            self.player.stdin.close()
            self.player.wait()
        except (BrokenPipeError, ValueError, OSError):
            pass
        self.player = None

    # Stop the player straight away
    def stop(self):
        if self.player is None:
            return
        self.player.kill()
        self.player = None


# Run main
if __name__ == '__main__':
    main()
