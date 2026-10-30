# Realtime.py holds what every speech to speech provider shares, the microphone, the speaker, the conversation loop, and the tool handling.

# Imports
import sys
import json
import time
import numpy
import queue
import threading
import subprocess

# Imports from the text helper
SCRIPT_DIR = __file__.rsplit('/', 1)[0]
sys.path.insert(0, f'{SCRIPT_DIR}/text')
import ask
import move
import robot_move
import utils
import volume

# Config the reply instructions added to the robot system prompt
SPOKEN_STYLE = 'You are speaking out loud, so keep replies to one or two short sentences.'

# Config the tool names answered here rather than by the text tools, those pick a kokoro voice instead
SKIP_TOOLS = ('set_voice', 'list_voices')

# Config what the model is told when it asks to quit and nobody said quit or exit
QUIT_NOT_ASKED = 'Not a quit request, carry on talking and do not mention this.'

# Config what the model is told about the voice tools
SET_VOICE_DESCRIPTION = 'Change the voice you speak with. Required when the user asks for a different voice. Takes one name from list_voices, matching the sound they asked for.'
LIST_VOICES_DESCRIPTION = 'List the voices you can speak with. Required when the user asks which voices are available.'

# Config how many past turns are replayed into a session reopened on a new voice
REPLAY_TURNS = 10

# Config the timing
RECEIVE_TIMEOUT = 0.2
IDLE_SECONDS = 20.0
SPEAK_LINE_SECONDS = 15.0
ECHO_SECONDS = 0.8
BLOCKS_PER_SECOND = 10

# Config the audio the speaker plays, every provider sends replies in this shape
OUTPUT_RATE = 24000
OUTPUT_CHANNELS = 1

# State, the voice a set_voice call picked, it holds for the rest of the run but not past a restart
CHOSEN_VOICE = ''

# State, set when the provider turned the conversation down for a reason retrying cannot fix, like no credits
TURN_FAILED = False

# Read config.json, falling back to the provider defaults for anything missing
def load_config(defaults):
    # Read the file
    config = utils.load_config(defaults)

    # Prefer a voice a set_voice call picked, later conversations should keep speaking with it
    if CHOSEN_VOICE:
        config['realtime_voice'] = CHOSEN_VOICE

    # Return the settings
    return config

# Build the system prompt, the robot personality plus a reminder that this reply is spoken
def session_instructions(accent):
    # Lead with the accent, the server makes most turns itself so this is the only place it can be said
    instructions = (accent + ' ' + SPOKEN_STYLE).strip()

    # Add the robot system prompt
    instructions = instructions + '\n\n' + ask.load_system_prompt()

    # Append memories and reminders, the same facts the text path sees
    extras = ask.prompt_extras()
    if extras:
        instructions = instructions + '\n\n' + extras

    # Say the accent again last, everything in between pulls the voice back to American
    if accent:
        instructions = instructions + '\n\n' + accent

    # Return the instructions
    return instructions

# List the tools as plain name, description, and parameters, each provider wraps them in its own shape
def tool_definitions(voice_examples):
    # Build the tool list from the chat style tools
    tools = []
    for tool in ask.active_tools():
        function = tool['function']
        if function['name'] in SKIP_TOOLS:
            continue
        tools.append({'name': function['name'], 'description': function.get('description', ''), 'parameters': function.get('parameters', {})})

    # Offer the voice tools that drive the provider voice, without them the model hunts through the other tools
    voice_property = {'type': 'string', 'description': f'Voice name, for example {voice_examples}.'}
    set_voice = {'name': 'set_voice', 'description': SET_VOICE_DESCRIPTION, 'parameters': {'type': 'object', 'properties': {'voice': voice_property}, 'required': ['voice']}}
    list_voices = {'name': 'list_voices', 'description': LIST_VOICES_DESCRIPTION, 'parameters': {'type': 'object', 'properties': {}}}
    return tools + [set_voice, list_voices]

# Stream microphone audio up and play replies back until the room goes quiet
def run_conversation(session, microphone, first_question, on_turn, greet_text):
    global TURN_FAILED

    # Keep the session handed in, the caller closes that one itself, and start with no failure
    opened = session
    TURN_FAILED = False

    # Talk until the room goes quiet, a voice change carries the conversation into a new session
    expect_audio = bool(first_question or greet_text)
    silenced = False
    while True:
        # Talk in this session
        voice = run_session(session, microphone, first_question, on_turn, greet_text, expect_audio)
        silenced = session.silence_requested
        if silenced or not voice:
            break

        # Reopen on the chosen voice, the voice is fixed once a session has spoken
        microphone.mute()
        session = reopen_session(session, voice)

        # Drop the opening question and greeting, the session that just closed already said them
        first_question = None
        greet_text = ''
        expect_audio = True

    # Close a session opened here, the caller only knows about the one it handed in
    if session is not opened:
        session.close()

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
                print('Quiet requested.', flush=True)
                speaker.stop()
                session.cancel_response()
                session.silence_requested = True
                session.running = False
                return ''

            # Receive an event
            event = session.receive()
            if event is None:
                continue

            # Any traffic means someone is still talking, so push the idle clock out
            if session.handle_event(microphone, speaker, event):
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
    print('Conversation idle.', flush=True)
    return ''

# Send one written question as a user turn and ask for a spoken answer
def ask_question(session, question):
    # Hide the model chatter when the question sets the volume
    hide_volume_chatter(session, question)

    # Send the question
    session.ask(question)

# Say a fixed line on this session, then keep it open for the person to talk
def speak_greeting(session, text):
    # Say the line in the accent of this session
    accent = session.config['realtime_accent']
    session.say(f'{accent} Say exactly this, and nothing else: {text}')

# Drop the model's set-volume chatter, the confirmation line is spoken after the tool
def hide_volume_chatter(session, text):
    # Mark the reply as chatter when the words ask for a volume change
    if volume.needs_set_volume(text):
        session.hide_volume_chatter = True

# Read microphone blocks, resample them, and send them up
def send_microphone(session, microphone):
    # While running
    while session.running:
        # Get the next block
        block = microphone.next_block()
        if block is None:
            continue
        if microphone.muted:
            continue

        # Resample to the only rate the provider takes, then send as signed 16 bit integer bytes
        samples = resample(block, utils.SAMPLE_RATE, session.input_rate)
        session.send_audio((numpy.clip(samples, -1.0, 1.0) * 32767).astype(numpy.int16).tobytes())

# Stretch samples from the microphone rate onto the provider rate
def resample(samples, source_rate, target_rate):
    # If the rates are the same, return the samples
    if source_rate == target_rate:
        return samples

    # Interpolate onto the new sample count, the microphone is already band limited
    count = int(round(len(samples) * target_rate / source_rate))
    source_points = numpy.arange(len(samples))
    target_points = numpy.linspace(0, len(samples) - 1, count)
    return numpy.interp(target_points, source_points, samples)

# Play a chunk of the reply, with the microphone off so it does not hear itself
def play_audio(session, microphone, speaker, chunk):
    # Close the microphone and drop what it heard so far
    if not microphone.muted:
        microphone.mute()
        session.clear_input()

    # Skip the audio of a volume change, the confirmation is spoken after the tool
    if session.hide_volume_chatter:
        return True

    # Play the chunk
    speaker.play(chunk)
    return True

# Let the microphone back in once the speaker has drained and the room echo has died
def finish_audio(session, microphone, speaker):
    # Wait for the speaker to empty, then for the room echo to die
    speaker.drain()
    session.clear_input()
    time.sleep(ECHO_SECONDS)
    session.clear_input()

    # Listen again
    microphone.unmute()
    return True

# Print the heard words as they arrive, return true when they counted as activity
def heard_delta(session, microphone, delta):
    # Ignore words picked up while the robot is speaking
    if microphone.muted:
        return False
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

# Keep the finished heard text, print it only when it was not already streamed, and act on spoken commands
def heard_done(session, microphone, speaker, transcript):
    # Ignore words picked up while the robot is speaking
    if microphone.muted:
        return False

    # Keep the heard text and act on it before the model answers
    session.heard = transcript.strip()
    hide_volume_chatter(session, session.heard)
    if start_restart(session):
        return True
    if start_quit(session):
        return True
    if go_silent(session, speaker):
        return True
    start_look(session)

    # End the streamed line, or print the heard text when nothing streamed
    streamed = session.heard_streamed or session.heard_open
    if session.heard_open:
        session.heard_open = False
        print(flush=True)
    session.heard_streamed = False
    if not streamed and session.heard and not session.reply_open:
        print(f'Heard: {session.heard}', flush=True)
    return bool(session.heard)

# Print the model's words as it speaks, return true when they counted as activity
def reply_delta(session, delta):
    # Hide the words of a volume change
    if session.hide_volume_chatter:
        return True
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

# Print the finished reply, then log the turn
def reply_done(session, transcript):
    # Hide the words of a volume change
    if session.hide_volume_chatter:
        close_stream_lines(session)
        session.reply_streamed = False
        return True

    # Get the reply
    reply = transcript.strip()

    # End the line the words streamed onto, they are on screen already
    streamed = session.reply_streamed
    close_stream_lines(session)
    session.reply_streamed = False
    if not streamed:
        print(f'Reply: {reply}', flush=True)

    # The reply got through, so a warning on the face is stale
    robot_move.clear_warning()

    # Call the callback when this reply was to something heard, skip the greeting
    if session.heard:
        session.on_turn(session.heard, reply)
        session.history.append((session.heard, reply))

    # Clear the heard text and the look this turn made
    session.heard = ''
    session.look_result = ''
    return True

# Ask for the spoken answer once the turn that called the tools has finished
def finish_turn(session, speaker):
    # Stay quiet when a voice was chosen, this session can only answer in the old one
    if session.voice_request:
        session.tool_pending = False
        session.voice_ready = True
        return True

    # Speak the fixed volume confirmation instead of a model reply
    if session.volume_confirm:
        text = session.volume_confirm
        session.volume_confirm = ''
        session.tool_pending = False
        session.hide_volume_chatter = False
        speaker.stop()
        speak_greeting(session, text)
        return True

    # Ask for the answer now the tool results are in
    if session.tool_pending:
        session.tool_pending = False
        session.answer()
    return True

# Restart as soon as the person asks, do not wait for the model to call the tool
def start_restart(session):
    # Skip unless the person asked to restart
    if not move.needs_restart(session.heard):
        return False

    # Say it and restart
    print(f'Reply: {move.RESTART_MESSAGE}', flush=True)
    move.run_restart()
    session.running = False
    return True

# Exit as soon as the person asks, do not wait for the model to call the tool
def start_quit(session):
    # Skip unless the person asked to quit
    if not move.needs_quit(session.heard):
        return False

    # Say it and quit
    print(f'Reply: {move.QUIT_MESSAGE}', flush=True)
    move.run_quit()
    session.running = False
    return True

# Move as soon as the person asks, so the head goes before the model speaks
def start_look(session):
    # Skip when the model already looked this turn
    if session.look_result:
        return

    # Skip unless the person asked to look somewhere
    arguments = move.look_arguments(session.heard)
    if arguments is None:
        return

    # Move the head and keep the result for the model
    result = move.run_look(arguments)
    ask.record_tool('look', arguments, result)
    session.look_result = str(result)

# Stop speech and leave the realtime session, talk.py then goes ready
def go_silent(session, speaker):
    # Skip unless the person asked for quiet
    if not move.needs_silence(session.heard):
        return False

    # Stop the speech and leave the session
    speaker.stop()
    session.cancel_response()
    session.silence_requested = True
    session.running = False
    return True

# End an open streamed line, so the next print does not land on top of the words
def close_stream_lines(session):
    # Skip when no streamed line is open
    if not session.heard_open and not session.reply_open:
        return

    # Close the lines and move to a new one
    session.heard_open = False
    session.reply_open = False
    print(flush=True)

# Run one tool locally and return its result to the model
def run_function_call(session, name, raw_arguments, call_id):
    # Finish any streamed words first, a tool can run before the transcript arrives
    close_stream_lines(session)

    # Only the words quit or exit end the program, the model reads a goodbye as one too easily
    if name == 'quit' and not move.needs_quit(session.heard):
        session.send_tool_result(call_id, QUIT_NOT_ASKED)
        session.tool_pending = True
        return

    # Answer a look already done this turn, moving again would log and move twice
    if name == 'look' and session.look_result:
        session.send_tool_result(call_id, session.look_result)
        session.look_result = ''
        session.tool_pending = True
        return

    # Answer the voice tools here, the text tools behind these names speak with kokoro instead
    if name in SKIP_TOOLS:
        arguments = parse_arguments(raw_arguments)
        result = run_voice_tool(session, name, arguments)
        ask.record_tool(name, arguments, result)
    else:
        # Build the call, run_tool prints it and keeps it for the log itself
        call = {'function': {'name': name, 'arguments': raw_arguments}}
        result = ask.run_tool(call)

        # Keep a look so the spoken shortcut does not move the head a second time
        if name == 'look':
            session.look_result = str(result)

        # Set the volume as soon as the person asked, even if the model called get_volume first
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
        session.cancel_response()
        session.running = False
        return

    # Stop talking and leave this session
    if name == 'silence':
        session.cancel_response()
        session.silence_requested = True
        session.running = False
        return

    # Hand the result back, the answer is asked for once the whole turn is done
    session.send_tool_result(call_id, str(result))
    session.tool_pending = True

# Load tool arguments, the model writes them as a JSON string
def parse_arguments(raw_arguments):
    # Parse the text, a broken one counts as no arguments
    try:
        return json.loads(raw_arguments or '{}')
    except ValueError:
        return {}

# List the voices, or hold a chosen one until the session can be reopened on it
def run_voice_tool(session, name, arguments):
    # List what this provider can speak with, the model has no other way to learn these names
    if name == 'list_voices':
        return f'Voices available: {voice_list(session)}.'

    # Read the asked for voice
    voice = str(arguments.get('voice', '')).strip().lower()

    # Ask which one when the name came through empty
    if not voice:
        return f'Which voice? Choose from: {voice_list(session)}.'

    # Turn down a name the provider does not speak with, a session opened on it would fail
    if voice not in session.voices:
        return f'There is no {voice} voice. Choose from: {voice_list(session)}.'

    # Say nothing changed when that is the voice already speaking
    if voice == session.config.get('realtime_voice'):
        return f'Already speaking with {voice}.'

    # Hold the request, the session is reopened on it as soon as this turn ends
    session.voice_request = voice
    return f'Voice changed to {voice}.'

# Name the voices grouped by how they sound, people ask for a man or a woman rather than a name
def voice_list(session):
    # Gather the names under each sound
    groups = {}
    for name, sound in session.voices.items():
        groups.setdefault(sound, []).append(name)

    # Read it back as a sentence
    return ', '.join(f'{join_names(names)} sound {sound}' for sound, names in groups.items())

# Join names so a list reads aloud naturally
def join_names(names):
    # Use a single name as it is
    if len(names) == 1:
        return names[0]

    # Put and before the last name
    return ', '.join(names[:-1]) + ' and ' + names[-1]

# Close a session and open a new one on the chosen voice, carrying the conversation across
def reopen_session(session, voice):
    # Remember the voice for the rest of the run, later conversations should keep speaking with it
    global CHOSEN_VOICE
    CHOSEN_VOICE = voice

    # Keep what was said, the new session opens knowing none of it
    history = session.history

    # Reuse the settings this session opened on, so a model named on the command line still applies
    config = dict(session.config)
    config['realtime_voice'] = voice

    # Drop the old session, its voice cannot be changed now that it has spoken
    session.close()

    # Open a new one on the chosen voice
    session = session.reopen(config)
    session.history = history

    # Replay the conversation so the new voice picks up where the old one left off
    session.replay(history[-REPLAY_TURNS:])

    # Speak straight away, the old session stayed quiet to leave the change to this one
    introduce_voice(session, voice, config['realtime_accent'])
    return session

# Ask for one short line so the new voice is heard as soon as it is picked
def introduce_voice(session, voice, accent):
    # Say the change is done, otherwise it reaches for set_voice and changes voice a second time
    instruction = f'{accent} You are already speaking with the {voice} voice, the change is done. Say one short line so they can hear it. Say nothing else and call no tools.'

    # Response instructions replace the session ones, so the accent went in above
    session.say(instruction)

# Drop a finished turn, running standalone there is nowhere to log it
def ignore_turn(heard, reply):
    # Do nothing with the turn
    return

# Mark the turn failed and hang up, retrying cannot help
def fail_turn(session):
    global TURN_FAILED

    # Hang up and remember why
    session.running = False
    TURN_FAILED = True

# True when the last conversation ended because the provider turned it down
def turn_failed():
    # Return the failure flag
    return TURN_FAILED

# The state of one realtime conversation, each provider adds its own connection and events
class Session:
    def __init__(self):
        self.running = True
        self.tool_pending = False
        self.heard = ''
        self.look_result = ''
        self.heard_open = False
        self.heard_streamed = False
        self.reply_open = False
        self.reply_streamed = False
        self.on_turn = ignore_turn
        self.config = {}
        self.voices = {}
        self.input_rate = utils.SAMPLE_RATE
        self.voice_request = ''
        self.voice_ready = False
        self.history = []
        self.hide_volume_chatter = False
        self.volume_confirm = ''
        self.silence_requested = False

    # Stop sending, the conversation is over
    def stop(self):
        self.running = False


# The microphone read in the background, so no words are lost between turns
class Microphone:
    def __init__(self):
        self.blocks = queue.Queue()
        self.muted = False
        self.record = utils.record_command()
        self.recorder = utils.start_recorder(self.record)
        self.reader = threading.Thread(target=self.read_blocks, daemon=True)
        self.reader.start()

    # Read blocks until the recorder keeps failing, dropping them while muted
    def read_blocks(self):
        block_bytes = utils.SAMPLE_RATE * 2 // BLOCKS_PER_SECOND
        while True:
            data = utils.read_capture_block(self, block_bytes)
            if data is None:
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

    # Start a player on the first chunk, then keep feeding the same one, nothing plays at bedtime
    def play(self, chunk):
        if utils.is_bedtime():
            return
        if self.player is None:
            self.player = subprocess.Popen(utils.play_raw_command(OUTPUT_RATE, OUTPUT_CHANNELS), stdin=subprocess.PIPE)
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
