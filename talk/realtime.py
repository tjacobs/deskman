#!.venv/bin/python

# Realtime.py streams microphone audio up to OpenAI and plays the spoken audio reply.

# Imports
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
import move
import robot_move
import utils
import volume

# Config the session, config.json overrides these
REALTIME_MODEL = 'gpt-realtime-2.1-mini'
REALTIME_VOICE = 'echo'
REALTIME_ACCENT = 'You are a British man from London. Speak with a natural British accent.'

# Config the connection
REALTIME_URL = 'wss://api.openai.com/v1/realtime'
TURN_DETECTION = 'semantic_vad'

# Config the reply instructions added to the robot system prompt
SPOKEN_STYLE = 'You are speaking out loud, so keep replies to one or two short sentences.'

# Config the tool names answered here rather than by the text tools, those pick a kokoro voice instead
SKIP_TOOLS = ('set_voice', 'list_voices')

# Config the voices OpenAI speaks with and how each comes across, OpenAI publishes no gender so these are by ear
REALTIME_VOICES = {
    'cedar': 'male', 'echo': 'male',
    'ballad': 'female', 'coral': 'female', 'marin': 'female', 'shimmer': 'female',
    'alloy': 'neutral', 'ash': 'neutral', 'sage': 'neutral', 'verse': 'neutral'}

# Config the voice tools offered in place of the kokoro ones, these drive the voice OpenAI speaks with
REALTIME_VOICE_TOOLS = [
    {'type': 'function',
     'name': 'set_voice',
     'description': 'Change the voice you speak with. Required when the user asks for a different voice. Takes one name from list_voices, matching the sound they asked for.',
     'parameters': {'type': 'object',
                    'properties': {'voice': {'type': 'string', 'description': 'Voice name, for example marin, cedar, or echo.'}},
                    'required': ['voice']}},
    {'type': 'function',
     'name': 'list_voices',
     'description': 'List the voices you can speak with. Required when the user asks which voices are available.',
     'parameters': {'type': 'object', 'properties': {}}}]

# Config how many past turns are replayed into a session reopened on a new voice
REPLAY_TURNS = 10

# Config the transcribers, the model hears the audio itself, these only write the heard lines to the log
LIVE_TRANSCRIPTION_MODEL = 'gpt-live-transcribe'
TURN_TRANSCRIPTION_MODEL = 'gpt-4o-mini-transcribe'

# Config which transcriber runs, live sends words during the sentence, the turn one waits for the end of it
TRANSCRIBE_LIVE = True

# Config whether the model's own words print as it speaks, the same live switch, it is not a second transcriber
PRINT_REPLY_LIVE = TRANSCRIBE_LIVE

# Config the timing
CONNECT_TIMEOUT = 30
RECEIVE_TIMEOUT = 0.2
IDLE_SECONDS = 20.0
SPEAK_LINE_SECONDS = 15.0
ECHO_SECONDS = 0.8
APPEND_BYTES = 32000
BLOCKS_PER_SECOND = 10
REALTIME_RATE = 24000
REALTIME_CHANNELS = 1

# State, the voice a set_voice call picked, it holds for the rest of the run but not past a restart
CHOSEN_VOICE = ''

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

    # Open the microphone before the socket, muted so connect time is not sent as speech
    microphone = Microphone()
    microphone.mute()

    # Open the session
    session = open_session(config)

    # Talk until the room goes quiet
    try:
        # Run the conversation
        run_conversation(session, microphone, arguments.ask, ignore_turn, '')
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
    # Read the file
    config = utils.load_config({'realtime_model': REALTIME_MODEL, 'realtime_voice': REALTIME_VOICE, 'realtime_accent': REALTIME_ACCENT})

    # Prefer a voice a set_voice call picked, later conversations should keep speaking with it
    if CHOSEN_VOICE:
        config['realtime_voice'] = CHOSEN_VOICE

    # Return the settings
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
    session.config = config
    print('Connected.', flush=True)

    # Describe the voice, the turn taking, and the tools before any audio moves
    session.send({'type': 'session.update', 'session': {
        'type': 'realtime',
        'output_modalities': ['audio'],
        'audio': {
            'input': {'format': {'type': 'audio/pcm', 'rate': REALTIME_RATE},
                      'turn_detection': {'type': TURN_DETECTION},
                      'transcription': {'model': transcription_model()}},
            'output': {'format': {'type': 'audio/pcm', 'rate': REALTIME_RATE},
                       'voice': config['realtime_voice']}},
        'instructions': session_instructions(config['realtime_accent']),
        'tools': session_realtime_tools(),
        'tool_choice': 'auto'}})
    return session

# Name the transcriber to write the heard lines with
def transcription_model():
    if TRANSCRIBE_LIVE:
        return LIVE_TRANSCRIPTION_MODEL
    return TURN_TRANSCRIPTION_MODEL

# Build the system prompt, the robot personality plus a reminder that this reply is spoken
def session_instructions(accent):
    # Lead with the accent, the server makes most turns itself so this is the only place it can be said
    instructions = accent + ' ' + SPOKEN_STYLE

    # Add the robot system prompt
    instructions = instructions + '\n\n' + ask.load_system_prompt()

    # Append memories and reminders, the same facts the text path sees
    extras = ask.prompt_extras()
    if extras:
        instructions = instructions + '\n\n' + extras

    # Say the accent again last, everything in between pulls the voice back to American
    instructions = instructions + '\n\n' + accent

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

    # Offer the voice tools that drive the OpenAI voice, without them the model hunts through the other tools
    return tools + REALTIME_VOICE_TOOLS

# Stream microphone audio up and play replies back until the room goes quiet
def run_conversation(session, microphone, first_question, on_turn, greet_text):
    # Keep the session handed in, the caller closes that one itself
    opened = session

    # Talk until the room goes quiet, a voice change carries the conversation into a new session
    expect_audio = bool(first_question or greet_text)
    silenced = False
    while True:
        # Talk in this session
        voice = run_session(session, microphone, first_question, on_turn, greet_text, expect_audio)
        silenced = session.silence_requested
        if silenced or not voice:
            break

        # Reopen on the chosen voice, OpenAI fixes the voice once a session has spoken
        microphone.mute()
        session = reopen_session(session, voice)

        # Drop the opening question and greeting, the session that just closed already said them
        first_question = None
        greet_text = ''
        expect_audio = True

    # Close a session opened here, the caller only knows about the one it handed in
    if session is not opened:
        close_session(session)

    # Print the conversation closed
    print('Conversation closed.', flush=True)
    return silenced

# Talk in one session, returning a voice when the conversation should carry on in a new one
def run_session(session, microphone, first_question, on_turn, greet_text, expect_audio):
    # Set the callback for when a turn is finished
    session.on_turn = on_turn

    # Drop leftover capture from before this session, it would go up as the person talking
    microphone.mute()

    # Send an opening question when one was passed, the wake word already heard it
    if first_question:
        # Set the heard text to the first question
        session.heard = first_question

        # Print the question
        print(f'Asking: {first_question}', flush=True)

        # Send the question as a user turn and ask for a spoken answer
        ask_question(session, first_question)

    # Say hello on this session, a one-shot greet hangs up before the person can answer
    elif greet_text:
        speak_greeting(session, greet_text)

    # Listen now when nothing is about to be spoken
    elif not expect_audio:
        microphone.unmute()

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
            if not session.running:
                return ''

            # Quiet on the face stops this session and goes back to ready
            if robot_move.consume_request("quiet"):
                speaker.stop()
                session.send({'type': 'response.cancel'})
                session.silence_requested = True
                session.running = False
                return ''

            # Receive an event
            event = session.receive()
            if event is None:
                continue

            # Any traffic means someone is still talking, so push the idle clock out
            if handle_event(session, microphone, speaker, event):
                idle_deadline = time.time() + IDLE_SECONDS

            # Hand the chosen voice back, the conversation carries on in a session opened on it
            if session.voice_ready:
                return session.voice_request
    finally:
        # Stop the session
        session.stop()
        speaker.stop()
        microphone.unmute()

    # Say the room went quiet, so there is no voice to carry over
    return ''

# Send one written question as a user turn and ask for a spoken answer
def ask_question(session, question):
    hide_volume_chatter(session, question)

    # Send the question as a user turn and ask for a spoken answer
    session.send({'type': 'conversation.item.create', 'item': { 'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': question}]}})
    session.send({'type': 'response.create'})

# Say a fixed line on this session, then keep it open for the person to talk
def speak_greeting(session, text):
    accent = session.config['realtime_accent']
    session.send({'type': 'response.create', 'response': {'instructions': f'{accent} Say exactly this, and nothing else: {text}'}})

# Drop the model's set-volume chatter, the confirmation line is spoken after the tool
def hide_volume_chatter(session, text):
    if volume.needs_set_volume(text):
        session.hide_volume_chatter = True

# Restart as soon as the person asks, do not wait for the model to call the tool
def start_restart(session):
    if not move.needs_restart(session.heard):
        return False
    print(f'Reply: {move.RESTART_MESSAGE}', flush=True)
    move.run_restart()
    session.running = False
    return True

# Exit as soon as the person asks, do not wait for the model to call the tool
def start_quit(session):
    if not move.needs_quit(session.heard):
        return False
    print(f'Reply: {move.QUIT_MESSAGE}', flush=True)
    move.run_quit()
    session.running = False
    return True

# Stop speech and leave the realtime session, talk.py then goes ready
def go_silent(session, speaker):
    if not move.needs_silence(session.heard):
        return False
    speaker.stop()
    session.send({'type': 'response.cancel'})
    session.silence_requested = True
    session.running = False
    return True

# Read microphone blocks, resample them, and append them to the input buffer
def send_microphone(session, microphone):
    # While running
    while session.running:
        # Get the next block
        block = microphone.next_block()
        if block is None:
            continue
        if microphone.muted:
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
        if not microphone.muted:
            microphone.mute()
            session.send({'type': 'input_audio_buffer.clear'})
        if session.hide_volume_chatter:
            return True
        speaker.play(base64.b64decode(event['delta']))
        return True

    # Let the microphone back in once the speaker has drained and the room echo has died
    if kind == 'response.output_audio.done':
        speaker.drain()
        session.send({'type': 'input_audio_buffer.clear'})
        time.sleep(ECHO_SECONDS)
        session.send({'type': 'input_audio_buffer.clear'})
        microphone.unmute()
        return True

    # Print the heard words as they arrive, the live transcriber sends them mid sentence
    if kind == 'conversation.item.input_audio_transcription.delta':
        if microphone.muted:
            return False
        delta = event.get('delta', '')
        if not delta:
            return False

        # Open the line on the first words, which arrive with a leading space of their own
        if not session.heard_open:
            close_stream_lines(session)
            session.heard_open = True
            print('Heard: ', end='', flush=True)
            delta = delta.lstrip()

        # Add the words to the line already on screen
        print(delta, end='', flush=True)
        session.heard_streamed = True
        return True

    # Keep the heard text, print it only when it was not already streamed
    if kind == 'conversation.item.input_audio_transcription.completed':
        if microphone.muted:
            return False
        session.heard = event.get('transcript', '').strip()
        hide_volume_chatter(session, session.heard)
        if start_restart(session):
            return True
        if start_quit(session):
            return True
        if go_silent(session, speaker):
            return True
        streamed = session.heard_streamed or session.heard_open
        if session.heard_open:
            session.heard_open = False
            print(flush=True)
        session.heard_streamed = False
        if not streamed and session.heard and not session.reply_open:
            print(f'Heard: {session.heard}', flush=True)
        return bool(session.heard)

    # Print the model's words as it speaks, these come from the realtime model itself not a transcriber
    if kind == 'response.output_audio_transcript.delta':
        if session.hide_volume_chatter:
            return True
        if not PRINT_REPLY_LIVE:
            return True
        delta = event.get('delta', '')
        if not delta:
            return False

        # Open the line on the first words
        if not session.reply_open:
            close_stream_lines(session)
            session.reply_open = True
            print('Reply: ', end='', flush=True)
            delta = delta.lstrip()

        # Add the words to the line already on screen
        print(delta, end='', flush=True)
        session.reply_streamed = True
        return True

    # If the reply is done, print it and call the callback
    if kind == 'response.output_audio_transcript.done':
        if session.hide_volume_chatter:
            close_stream_lines(session)
            session.reply_streamed = False
            return True

        # Get the reply
        reply = event.get('transcript', '').strip()

        # End the line the words streamed onto, they are on screen already
        streamed = session.reply_streamed
        close_stream_lines(session)
        session.reply_streamed = False
        if not streamed:
            print(f'Reply: {reply}', flush=True)

        # Call the callback when this reply was to something heard, skip the greeting
        if session.heard:
            session.on_turn(session.heard, reply)
            session.history.append((session.heard, reply))

        # Clear the heard text
        session.heard = ''
        return True

    # Run the tool here and hand the result back, the tools are all local
    if kind == 'response.function_call_arguments.done':
        run_function_call(session, event)
        return True

    # Ask for the spoken answer once the turn that called the tools has finished
    if kind == 'response.done':
        # Stay quiet when a voice was chosen, this session can only answer in the old one
        if session.voice_request:
            session.tool_pending = False
            session.voice_ready = True
            return True

        if session.volume_confirm:
            text = session.volume_confirm
            session.volume_confirm = ''
            session.tool_pending = False
            session.hide_volume_chatter = False
            speaker.stop()
            speak_greeting(session, text)
            return True

        if session.tool_pending:
            session.tool_pending = False
            session.send({'type': 'response.create'})
        return True

    # Show why the server gave up
    if kind == 'error':
        close_stream_lines(session)
        print(f'Realtime error: {event.get("error", {}).get("message", "")}', flush=True)
        return False
    return False

# End an open streamed line, so the next print does not land on top of the words
def close_stream_lines(session):
    if not session.heard_open and not session.reply_open:
        return
    session.heard_open = False
    session.reply_open = False
    print(flush=True)

# Run one tool locally and return its result to the model
def run_function_call(session, event):
    # Finish any streamed words first, a tool can run before the transcript arrives
    close_stream_lines(session)

    # Answer the voice tools here, the text tools behind these names speak with kokoro instead
    name = event.get('name')
    if name in SKIP_TOOLS:
        arguments = parse_arguments(event.get('arguments'))
        result = run_voice_tool(session, name, arguments)
        ask.record_tool(name, arguments, result)
    else:
        # Build the call, run_tool prints it and keeps it for the log itself
        call = {'function': {'name': name, 'arguments': event.get('arguments')}}
        result = ask.run_tool(call)

        # Set the pack as soon as the person asked, even if the model called get_volume first
        if name == 'get_volume' and volume.needs_set_volume(session.heard):
            percent = volume.parse_volume_percent(session.heard)
            if percent is not None:
                arguments = {'percent': percent}
                result = volume.run_set_volume(arguments)
                ask.record_tool('set_volume', arguments, result)
                name = 'set_volume'

    # Speak a fixed confirmation after a volume set, skip a second model reply
    if name == 'set_volume':
        session.volume_confirm = volume.confirm_volume_set() or str(result)

    # Bounce the services as soon as the restart tool returns
    if name == 'restart':
        session.running = False

    # Exit the robot and this program, with no further speech
    if name == 'quit':
        session.send({'type': 'response.cancel'})
        session.running = False
        return

    # Stop talking and leave this session
    if name == 'silence':
        session.send({'type': 'response.cancel'})
        session.silence_requested = True
        session.running = False
        return

    # Hand the result back, the answer is asked for once the whole turn is done
    session.send({'type': 'conversation.item.create', 'item': {
        'type': 'function_call_output', 'call_id': event.get('call_id'), 'output': str(result)}})
    session.tool_pending = True

# Load tool arguments, the model writes them as a JSON string
def parse_arguments(raw_arguments):
    try:
        return json.loads(raw_arguments or '{}')
    except ValueError:
        return {}

# List the voices, or hold a chosen one until the session can be reopened on it
def run_voice_tool(session, name, arguments):
    # List what OpenAI can speak with, the model has no other way to learn these names
    if name == 'list_voices':
        return f'Voices available: {voice_list()}.'

    # Read the asked for voice
    voice = str(arguments.get('voice', '')).strip().lower()

    # Ask which one when the name came through empty
    if not voice:
        return f'Which voice? Choose from: {voice_list()}.'

    # Turn down a name OpenAI does not speak with, a session opened on it would fail
    if voice not in REALTIME_VOICES:
        return f'There is no {voice} voice. Choose from: {voice_list()}.'

    # Say nothing changed when that is the voice already speaking
    if voice == session.config.get('realtime_voice'):
        return f'Already speaking with {voice}.'

    # Hold the request, the session is reopened on it as soon as this turn ends
    session.voice_request = voice
    return f'Voice changed to {voice}.'

# Name the voices grouped by how they sound, people ask for a man or a woman rather than a name
def voice_list():
    # Gather the names under each sound
    groups = {}
    for name, sound in REALTIME_VOICES.items():
        groups.setdefault(sound, []).append(name)

    # Read it back as a sentence
    return ', '.join(f'{join_names(names)} sound {sound}' for sound, names in groups.items())

# Join names so a list reads aloud naturally
def join_names(names):
    if len(names) == 1:
        return names[0]
    return ', '.join(names[:-1]) + ' and ' + names[-1]

# Close a session and open a new one on the chosen voice, carrying the conversation across
def reopen_session(session, voice):
    # Remember the voice for the rest of the run, later conversations should keep speaking with it
    global CHOSEN_VOICE
    CHOSEN_VOICE = voice

    # Keep what was said, the new session opens knowing none of it
    history = session.history

    # Leave out the line that asked for the voice, replaying it makes the new session switch all over again

    # Reuse the settings this session opened on, so a model named on the command line still applies
    config = dict(session.config)
    config['realtime_voice'] = voice

    # Drop the old socket, its voice cannot be changed now that it has spoken
    close_session(session)

    # Open a new one on the chosen voice
    session = open_session(config)
    session.history = history

    # Replay the conversation so the new voice picks up where the old one left off
    replay_history(session)

    # Speak straight away, the old session stayed quiet to leave the change to this one
    introduce_voice(session, voice, config['realtime_accent'])
    return session

# Put the recent conversation into a session that opened with no memory of it
def replay_history(session):
    for heard, reply in session.history[-REPLAY_TURNS:]:
        # Send what the person said
        if heard:
            session.send({'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': heard}]}})

        # Send what the robot answered, an assistant turn carries output text
        if reply:
            session.send({'type': 'conversation.item.create', 'item': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': reply}]}})

# Ask for one short line so the new voice is heard as soon as it is picked
def introduce_voice(session, voice, accent):
    # Say the change is done, otherwise it reaches for set_voice and changes voice a second time
    instruction = f'{accent} You are already speaking with the {voice} voice, the change is done. Say one short line so they can hear it. Say nothing else and call no tools.'

    # Response instructions replace the session ones, so the accent went in above
    session.send({'type': 'response.create', 'response': {'instructions': instruction}})

# Drop a finished turn, running standalone there is nowhere to log it
def ignore_turn(heard, reply):
    return

# Speak one line in the realtime voice, the greeting lands before any conversation opens
def speak_line(text):
    return speak_instruction(f'Say exactly this, and nothing else: {text}')

# Let the model write its own line and speak it, and return the words it said
def speak_answer(question):
    return speak_instruction(f'{question} Say the line out loud, and say nothing else.')

# Open a session for one spoken turn with no microphone, say it, and hang up
def speak_instruction(instruction):
    # Open a session, the same one a conversation uses
    config = load_config()
    session = open_session(config)

    # Response instructions replace the session ones, so carry the accent along
    session.send({'type': 'response.create', 'response': {'instructions': f'{config["realtime_accent"]} {instruction}'}})

    # Play it and hang up, there is no conversation to keep open yet
    speaker = Speaker()
    try:
        return play_reply(session, speaker)
    finally:
        speaker.stop()
        close_session(session)

# Play one spoken reply as the audio arrives, and return the words it said
def play_reply(session, speaker):
    said = ''

    # Give up rather than hang, this runs during startup
    deadline = time.time() + SPEAK_LINE_SECONDS
    while time.time() < deadline:
        event = session.receive()
        if event is None:
            continue
        kind = event.get('type', '')

        # Play each chunk the moment it lands
        if kind == 'response.output_audio.delta':
            speaker.play(base64.b64decode(event['delta']))

        # Keep the words, a caller that did not write the line needs to know what was said
        elif kind == 'response.output_audio_transcript.done':
            said = event.get('transcript', '').strip()

        # Let the speaker empty, the audio is complete
        elif kind == 'response.output_audio.done':
            speaker.drain()

        # Wait for the whole turn, the transcript can land after the audio
        elif kind == 'response.done':
            return said

        # Show why the server gave up
        elif kind == 'error':
            print(f'Realtime error: {event.get("error", {}).get("message", "")}', flush=True)
            return said
    return said

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
        self.heard_open = False
        self.heard_streamed = False
        self.reply_open = False
        self.reply_streamed = False
        self.on_turn = ignore_turn
        self.config = {}
        self.voice_request = ''
        self.voice_ready = False
        self.history = []
        self.hide_volume_chatter = False
        self.volume_confirm = ''
        self.silence_requested = False

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
            self.player = subprocess.Popen(utils.play_raw_command(REALTIME_RATE, REALTIME_CHANNELS), stdin=subprocess.PIPE)
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
