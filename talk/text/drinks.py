#!/usr/bin/env python3

# Drink ordering mode, Drinkman takes drink orders with its own prompt in place of the Deskman prompt

# Imports
import re

# Config the drinks on offer
DRINKS = ["Waterloo lemon lime", "Kirkland lime"]

# Config the spoken lines
GREETING = "What drink would you like?"
STOP_MESSAGE = "Drink ordering is off."
START_EXCHANGE = "Start drink ordering."

# Config the words that name the app and the words that start or stop it
APP_PATTERN = re.compile(r"\b(drinks?\s+(ordering|order|app|mode)|drinkman)\b")
START_PATTERN = re.compile(r"\b(start|begin|open|launch|run|enable|activate|turn on|switch to)\b")
STOP_PATTERN = re.compile(r"\b(stop|end|close|leave|disable|cancel|turn off|switch off)\b")

# Prompt that replaces the Deskman prompt while ordering
PROMPT = (
    "You are a drink ordering robot, Drinkman. "
    f'You have already asked "{GREETING}" '
    "Take the order for the drink the person wants. "
    "Available drinks are:\n" + "\n".join(DRINKS) + "\n"
    "Confirm the drink in one short sentence. "
    "If they ask for a drink that is not available, say sorry, then say we offer a " + " and a ".join(DRINKS) + ". "
    "Answer in one or two short sentences suitable for speaking aloud."
)

# Mode state, true while drink ordering is on
ordering_on = False

# Main
def main():
    # Print the drink ordering prompt
    print(PROMPT)

# True while drink ordering mode is on
def is_on():
    return ordering_on

# Turn drink ordering mode on or off
def set_on(value):
    global ordering_on
    ordering_on = value

# Lowercase the spoken words with punctuation removed
def clean_words(prompt):
    text = re.sub(r"[^\w\s]", " ", str(prompt or "").lower())
    return " ".join(text.split())

# Return true when the person asked to start drink ordering
def needs_start(prompt):
    text = clean_words(prompt)
    return bool(APP_PATTERN.search(text) and START_PATTERN.search(text) and not STOP_PATTERN.search(text))

# Return true when the person asked to stop drink ordering
def needs_stop(prompt):
    text = clean_words(prompt)
    return bool(APP_PATTERN.search(text) and STOP_PATTERN.search(text))

# Main
if __name__ == "__main__":
    main()
