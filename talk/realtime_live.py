#!.venv/bin/python

# Realtime_live.py streams microphone audio up to OpenAI GPT Live and plays the spoken audio reply.

# Imports
import json
import time
import queue
import base64
import numpy
import argparse
import threading
import websocket

# Imports from the shared voice helper, which also puts the text helper on the path
import voice
import client
import ask
import robot_move

# Config the session, config.json overrides these
LIVE_MODEL = 'gpt-live-1'
LIVE_BACKEND = 'gpt-6-luna'
LIVE_VOICE = 'marin'
LIVE_ACCENT = ''

# Config the connection
LIVE_URL = 'wss://api.openai.com/v1/live/sessions'

# Config the voices GPT Live speaks with and how each comes across, taken from the published list
LIVE_VOICES = {'meridian': 'male', 'vesper': 'male', 'ripple': 'male', 'marin': 'female', 'gleam': 'female'}

# Config example voice names given to the model when it picks one
VOICE_EXAMPLES = 'marin, meridian, or vesper'

# Config the timing
CONNECT_TIMEOUT = 30
CLOSE_TIMEOUT = 5
SPEECH_END_SECONDS = 0.6
SILENCE_SECONDS = 0.1
SILENCE_LAG_SECONDS = 0.25
SILENCE_POLL_SECONDS = 0.02

# Config how loud a chunk of reply audio must be to count as speech, the model streams silence between replies
SPEECH_LEVEL = 200

# Config the rate of the audio sent up, and the bytes in one second of it
LIVE_RATE = 24000
BYTES_PER_SECOND = LIVE_RATE * 2

# Hold one GPT Live conversation from the microphone
def main():
    # Parse arguments
    arguments = parse_args()

    # Load config
    config = load_config()

    # Load from the command line, overriding config.json
    if arguments.model:
        config['live_model'] = arguments.model
    if arguments.voice:
        config['realtime_voice'] = arguments.voice

    # Open the microphone before the connection, muted so connect time is not sent as speech
    microphone = voice.Microphone()
    microphone.mute()

    # Open the session
    session = open_session(config)

    # Talk until the room goes quiet
    try:
        # Run the conversation
        voice.run_conversation(session, microphone, arguments.ask, voice.ignore_turn, '')
    except KeyboardInterrupt:
        # Stop the conversation
        print('Stopped.', flush=True)
    finally:
        # Close the session
        close_session(session)
        microphone.stop()

# Parse command line arguments
def parse_args():
    # Describe the arguments
    parser = argparse.ArgumentParser(description='Talk to OpenAI GPT Live with audio up and audio down.')
    parser.add_argument('ask', nargs='?', help='send this question first, instead of waiting for speech')
    parser.add_argument('--model', help='GPT Live model to use, overrides config.json')
    parser.add_argument('--voice', help='voice to speak with, overrides config.json')
    return parser.parse_args()

# Read config.json, falling back to the defaults above for anything missing
def load_config():
    # Load the settings with the defaults above
    config = voice.load_config({'live_model': LIVE_MODEL, 'live_backend': LIVE_BACKEND, 'realtime_voice': LIVE_VOICE, 'realtime_accent': LIVE_ACCENT})

    # Fall back to the default voice when the saved one belongs to another provider
    if config['realtime_voice'] not in LIVE_VOICES:
        config['realtime_voice'] = LIVE_VOICE
    return config

# Connect to GPT Live and start the session
def open_session(config):
    # Get the API key
    key = client.cloud_api_key()
    if not key:
        raise SystemExit('No OpenAI key, set OPENAI_API_KEY or write talk/openai.env.')

    # Open the socket, without the browser origin header the server turns down
    print('Connecting to OpenAI...', flush=True)
    try:
        socket = websocket.create_connection(LIVE_URL, header=[f'Authorization: Bearer {key}'], timeout=CONNECT_TIMEOUT, suppress_origin=True)
    except (OSError, websocket.WebSocketException) as error:
        raise SystemExit(f'GPT Live connection failed: {error!r}')
    session = LiveSession(socket)
    session.config = config

    # Describe the voice, the instructions, the earlier turns, and the backend with its tools before any audio moves
    session.send({'type': 'session.start', 'event_id': 'start', 'session': session_settings(config)})
    session.wait_started()
    print('Connected.', flush=True)

    # Start reading events and keeping the input timeline moving
    session.start_threads()
    return session

# Build the session settings, the live model talks and the backend runs the tools
def session_settings(config):
    # Seed the recent conversation when a voice change reopened the session
    earlier = []
    for heard, reply in config.get('earlier', []):
        earlier.append({'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': heard}]})
        earlier.append({'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': reply}]})

    # Hand the tools to the backend, it calls them and the results come back here
    backend = {'model': config['live_backend'], 'instructions': ask.load_system_prompt(), 'tools': session_tools(), 'tool_choice': 'auto'}
    audio = {'format': {'type': 'audio/pcm', 'rate': LIVE_RATE}, 'output': {'voice': config['realtime_voice']}}
    settings = {'model': config['live_model'], 'instructions': voice.session_instructions(config['realtime_accent']), 'audio': audio, 'delegation': {'type': 'responses', 'responses': backend}}
    if earlier:
        settings['input'] = earlier
    return settings

# Wrap the shared tool list in the shape the backend expects
def session_tools():
    # Add the function type to each tool
    return [{'type': 'function', **tool} for tool in voice.tool_definitions(VOICE_EXAMPLES)]

# Run the conversation on the shared loop, talk.py calls every provider the same way
def run_conversation(session, microphone, first_question, on_turn, greet_text):
    # Run the shared loop
    return voice.run_conversation(session, microphone, first_question, on_turn, greet_text)

# Hang up and wait for the final usage, the session is finished
def close_session(session):
    # Stop the session, and skip a second close
    session.stop()
    if session.closed:
        return
    session.closed = True

    # Ask the server to finish, then wait for the usage it reports
    session.send_raw({'type': 'session.close'})
    session.finished.wait(CLOSE_TIMEOUT)
    if session.usage_seconds:
        print(f'Live usage: {session.usage_seconds:.0f} seconds.', flush=True)

    # Close the socket
    try:
        session.socket.close()
    except OSError:
        pass

# Say whether the last conversation ended because OpenAI turned it down
def turn_failed():
    # Return the shared failure flag
    return voice.turn_failed()

# Speak one line in the live voice, the model may word it a little differently
def speak_line(text):
    # Speak the line
    return speak_instruction(f'Say exactly this, and nothing else: {text}')

# Let the model write its own line and speak it, and return the words it said
def speak_answer(question):
    # Speak an answer the model writes
    return speak_instruction(f'{question} Say the line out loud, and say nothing else.')

# Open a session for one spoken turn with no microphone, say it, and hang up
def speak_instruction(instruction):
    # Open a session, the same one a conversation uses
    config = load_config()
    session = open_session(config)
    session.say(f'{config["realtime_accent"]} {instruction}')

    # Play it and hang up, there is no conversation to keep open yet
    speaker = voice.Speaker()
    try:
        return play_reply(session, speaker)
    finally:
        speaker.stop()
        close_session(session)

# Play one spoken reply as the audio arrives, and return the words it said
def play_reply(session, speaker):
    # Start with nothing said
    said = ''
    spoken = False
    quiet_seconds = 0.0

    # Give up rather than hang, this runs during startup
    deadline = time.time() + voice.SPEAK_LINE_SECONDS
    while time.time() < deadline:
        event = session.receive()
        if event is None:
            continue
        kind = event['type']

        # Play the speech and the short pauses inside it, and stop once the audio has been quiet long enough
        if kind == 'audio':
            if is_speech(event['data']):
                spoken = True
                quiet_seconds = 0.0
            elif spoken:
                quiet_seconds += len(event['data']) / BYTES_PER_SECOND
            if spoken:
                speaker.play(event['data'])
            if spoken and quiet_seconds >= SPEECH_END_SECONDS:
                speaker.drain()
                if said:
                    robot_move.clear_warning()
                return said.strip()

        # Keep the words, a caller that did not write the line needs to know what was said
        elif kind == 'reply':
            said += event['text']

        # Show why the server gave up
        elif kind == 'error' or kind == 'closed':
            report_error(session, event)
            return said.strip()
    return said.strip()

# True when a chunk of reply audio is speech rather than the silence streamed between replies
def is_speech(chunk):
    # Compare the loudness with the speech level
    samples = numpy.frombuffer(chunk, dtype=numpy.int16).astype(numpy.float32)
    return len(samples) > 0 and float(numpy.sqrt(numpy.mean(samples ** 2))) > SPEECH_LEVEL

# Log the server error, put its short name on the face, and hang up when retrying cannot help
def report_error(session, event):
    # Log the error
    error = event.get('error') or {}
    message = error.get('message', '') if isinstance(error, dict) else str(error)
    print(f'GPT Live error: {message or event.get("type")}', flush=True)
    session.running = False

    # Put its short name on the face
    code = error.get('code', '') if isinstance(error, dict) else ''
    name, page = client.openai_warning(f'{code} {message}')
    robot_move.print_warning(name, page)

    # Hang up when retrying cannot help
    if client.openai_error_is_fatal(name):
        voice.fail_turn(session)

# Turn one server message into a plain event, or None when nothing here needs acting on
def message_event(message):
    # Get the message type
    kind = message.get('type', '')

    # Hand over audio as bytes and the transcripts as text
    if kind == 'session.output_audio.delta':
        return {'type': 'audio', 'data': base64.b64decode(message['delta'])}
    if kind == 'session.input_transcript.delta':
        return {'type': 'heard', 'text': message.get('delta', '')}
    if kind == 'session.output_transcript.delta':
        return {'type': 'reply', 'text': message.get('delta', '')}

    # Hand over work given to the backend, and the final usage when the session ends
    if kind == 'session.delegation.created':
        return {'type': 'delegation'}
    if kind == 'session.closed':
        return {'type': 'closed', 'usage': message.get('usage', {}).get('seconds', 0.0)}
    if kind == 'error':
        return {'type': 'error', 'error': message.get('error', {})}

    # Hand over what the backend asked for or finished
    if kind == 'response.event':
        return backend_event(message.get('event', {}))
    return None

# Turn one event from the backend into a tool call or its end
def backend_event(inner):
    # Get the nested type
    kind = inner.get('type', '')

    # Hand over a finished tool call with its arguments
    item = inner.get('item') or {}
    if kind == 'response.output_item.done' and item.get('type') == 'function_call':
        return {'type': 'tool', 'id': item.get('call_id'), 'name': item.get('name'), 'arguments': item.get('arguments', '{}')}

    # Hand over the end of one backend response, whether it finished or failed
    if kind in ('response.completed', 'response.failed', 'response.incomplete'):
        return {'type': 'backend_done'}
    return None


# A GPT Live socket read on a worker thread, safe to use from the threaded conversation loop
class LiveSession(voice.Session):
    def __init__(self, socket):
        super().__init__()
        self.socket = socket
        self.lock = threading.Lock()
        self.events = queue.Queue()
        self.finished = threading.Event()
        self.closed = False
        self.voices = LIVE_VOICES
        self.input_rate = LIVE_RATE
        self.heard_pieces = []
        self.reply_pieces = []
        self.pending_outputs = []
        self.speaking = False
        self.quiet_seconds = 0.0
        self.backend_pending = False
        self.question = ''
        self.usage_seconds = 0.0
        self.started_at = time.time()
        self.sent_seconds = 0.0

    # Send one event as JSON, only while the session runs
    def send(self, event):
        if not self.running:
            return
        self.send_raw(event)

    # Send one event as JSON even while closing
    def send_raw(self, event):
        with self.lock:
            try:
                self.socket.send(json.dumps(event))
            except (OSError, websocket.WebSocketException):
                self.running = False

    # Read events until the server confirms the session started
    def wait_started(self):
        # Give up rather than hang
        self.socket.settimeout(CONNECT_TIMEOUT)
        try:
            while True:
                message = json.loads(self.socket.recv())
                if message.get('type') == 'session.started':
                    return
                if message.get('type') == 'error':
                    report_error(self, message)
                    raise SystemExit('GPT Live refused the session.')
        except (OSError, ValueError, websocket.WebSocketException) as error:
            raise SystemExit(f'GPT Live connection failed: {error!r}')
        finally:
            self.socket.settimeout(None)

    # Start the reader and the silence sender
    def start_threads(self):
        self.started_at = time.time()
        threading.Thread(target=self.read_events, daemon=True).start()
        threading.Thread(target=self.send_silence, daemon=True).start()

    # Turn server messages into events until the connection ends
    def read_events(self):
        try:
            while True:
                event = message_event(json.loads(self.socket.recv()))
                if event is None:
                    continue
                if event['type'] == 'closed':
                    self.usage_seconds = event['usage']
                    self.finished.set()
                self.events.put(event)
        except (OSError, ValueError, websocket.WebSocketException) as error:
            if not self.closed:
                self.events.put({'type': 'closed', 'usage': 0.0, 'error': {'message': f'connection lost {error!r}'}})
            self.finished.set()

    # Keep the input timeline moving with silence while the microphone is muted, the model needs audio to keep time
    def send_silence(self):
        silence = base64.b64encode(bytes(int(BYTES_PER_SECOND * SILENCE_SECONDS))).decode()
        while self.running:
            time.sleep(SILENCE_POLL_SECONDS)
            if self.sent_seconds + SILENCE_LAG_SECONDS < time.time() - self.started_at:
                self.sent_seconds += SILENCE_SECONDS
                self.send({'type': 'session.input_audio.append', 'audio': silence})

    # Return the next event, or None when nothing arrived before the timeout
    def receive(self):
        try:
            return self.events.get(timeout=voice.RECEIVE_TIMEOUT)
        except queue.Empty:
            return None

    # Close the connection
    def close(self):
        close_session(self)

    # Open a new session on the settings given, with the recent conversation seeded into it
    def reopen(self, config):
        return open_session(dict(config, earlier=self.history[-voice.REPLAY_TURNS:]))

    # Send microphone audio up, GPT Live finds the ends of sentences itself
    def send_audio(self, audio):
        self.sent_seconds += len(audio) / BYTES_PER_SECOND
        self.send({'type': 'session.input_audio.append', 'audio': base64.b64encode(audio).decode()})

    # Do nothing, there is no input buffer to clear and the model hears only what was sent
    def clear_input(self):
        return

    # Do nothing, there is no cancel and every caller closes the session or stops the speaker
    def cancel_response(self):
        return

    # Give the backend what was asked as a user message and have it answer, the live model then speaks the result
    def ask(self, question):
        self.question = question
        self.send({'type': 'response.item.create', 'event_id': f'question_{time.time()}', 'item': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': question}]}})
        self.send({'type': 'response.create', 'event_id': f'answer_{time.time()}'})

    # Do nothing, the backend carries on once the tool results are in
    def answer(self):
        return

    # Speak with instructions sent to the model
    def say(self, instructions):
        self.send({'type': 'session.commentary.append', 'event_id': f'say_{time.time()}', 'delegation_id': None, 'content': instructions})

    # Keep one tool result, they all go back together when the backend finishes asking, unless a voice change is about to replace this session
    def send_tool_result(self, call_id, output):
        if self.voice_request:
            return
        self.pending_outputs.append((call_id, output))

    # Do nothing, the recent conversation went in when the session started
    def replay(self, history):
        return

    # Act on one event, return true when it counted as activity
    def handle_event(self, microphone, speaker, event):
        # Get the event type
        kind = event['type']

        # Print the heard words as they arrive, ignoring what the robot's own speech leaks in
        if kind == 'heard':
            if microphone.muted:
                return False
            self.heard_pieces.append(event['text'])
            return voice.heard_delta(self, microphone, event['text'])

        # Show why the connection closed or failed
        if kind in ('closed', 'error'):
            voice.close_stream_lines(self)
            if kind == 'error' or 'error' in event:
                report_error(self, event)
            self.running = False
            return False

        # Act on the heard words before the model answers, there is no event for them being finished
        if kind in ('audio', 'reply', 'tool', 'delegation'):
            if kind != 'audio' or is_speech(event['data']):
                self.finish_heard(microphone, speaker)
            if not self.running:
                return False

        # Play the reply as it arrives, and finish the turn once the speech has ended
        if kind == 'audio':
            return self.handle_audio(microphone, speaker, event['data'])

        # Print the model's words as it speaks
        if kind == 'reply':
            self.reply_pieces.append(event['text'])
            return voice.reply_delta(self, event['text'])

        # Note the backend is working, the spoken answer comes after it
        if kind == 'delegation':
            self.backend_pending = True
            self.explain_question()
            return True

        # Run the tool here, the result goes back when the backend finishes asking, a voice change reopens the session at once
        if kind == 'tool':
            voice.run_function_call(self, event['name'], event['arguments'], event['id'])
            if self.voice_request:
                self.voice_ready = True
            return True

        # Hand the results back so the backend carries on, or note the backend is done
        if kind == 'backend_done':
            return self.finish_backend()
        return False

    # Tell the model what the backend was asked, a written question gives it nothing to go on and it babbles
    def explain_question(self):
        # Skip a delegation the model started from speech it heard
        if not self.question:
            return

        # Say what was asked, which also holds back the spoken acknowledgement until there is a result
        self.send({'type': 'session.thinking.append', 'event_id': f'explain_{time.time()}', 'delegation_id': None, 'content': f'The person asked: "{self.question}" The lookup is in progress.'})
        self.question = ''

    # Play one chunk of reply audio, return true when it was speech
    def handle_audio(self, microphone, speaker, chunk):
        # Play speech, and note the model is talking
        if is_speech(chunk):
            self.speaking = True
            self.quiet_seconds = 0.0
            return voice.play_audio(self, microphone, speaker, chunk)

        # Drop the silence streamed between replies
        if not self.speaking:
            return False

        # Keep the pauses inside speech, and end the turn once the audio has been quiet long enough
        self.quiet_seconds += len(chunk) / BYTES_PER_SECOND
        voice.play_audio(self, microphone, speaker, chunk)
        if self.quiet_seconds >= SPEECH_END_SECONDS:
            self.end_speech(microphone, speaker)
        return False

    # Finish the reply, let the microphone back in, and finish the turn
    def end_speech(self, microphone, speaker):
        # Stop speaking
        self.speaking = False
        self.quiet_seconds = 0.0

        # Log the reply, unless the backend is still working and the real answer is yet to come
        reply = ''.join(self.reply_pieces)
        if reply and not self.backend_pending:
            self.reply_pieces = []
            voice.reply_done(self, reply)

        # Let the microphone back in, and finish the turn
        if microphone.muted:
            voice.finish_audio(self, microphone, speaker)
        voice.finish_turn(self, speaker)

    # Send the tool results back so the backend carries on, or note the backend has finished
    def finish_backend(self):
        # Hand every result over, then ask the backend to continue
        if self.pending_outputs:
            for call_id, output in self.pending_outputs:
                self.send({'type': 'response.item.create', 'event_id': f'result_{call_id}', 'item': {'type': 'function_call_output', 'call_id': call_id, 'output': output}})
            self.pending_outputs = []
            self.send({'type': 'response.create', 'event_id': f'continue_{time.time()}'})
            return True

        # The backend has nothing more to ask, the model speaks the answer
        self.backend_pending = False
        return True

    # Give the shared code the heard words, return true when they counted as activity
    def finish_heard(self, microphone, speaker):
        # Skip when nothing was heard since the last turn
        if not self.heard_pieces:
            return False

        # Hand over the whole sentence
        transcript = ''.join(self.heard_pieces)
        self.heard_pieces = []
        return voice.heard_done(self, microphone, speaker, transcript)


# Run main
if __name__ == '__main__':
    main()
