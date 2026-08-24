#!/usr/bin/env python3

# Long-term remembered facts stored in memory.json

# Imports
import json
import os
import re
from datetime import datetime

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEAK_DIR = os.path.dirname(SCRIPT_DIR)
MEMORY_PATH = os.path.join(SPEAK_DIR, "memory.json")
MAX_MEMORY_FACTS = 50
MEMORY_RETRY_PROMPT = "Do not guess. Call remember with the fact the user asked you to keep, then answer using only the tool result."
FORGET_RETRY_PROMPT = "Do not guess. Call forget with the fact the user asked you to drop, then answer using only the tool result."

# Tools the local model can call for memory
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Save a long-term fact the user asked you to remember. Survives reboot. Required when the user says remember.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The fact to remember, for example Dinner time is 6:30 PM.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            "description": "Remove a long-term remembered fact. Required when the user says forget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text matching the fact to forget.",
                    },
                    "id": {
                        "type": "number",
                        "description": "Optional fact id to forget.",
                    },
                },
            },
        },
    },
]

# Main
def main():
    # List remembered facts by default
    print(format_memories_for_prompt() or "No long-term memories saved.")

# Format saved facts for the system prompt
def format_memories_for_prompt():
    facts = load_memory_facts()
    if not facts:
        return ""
    lines = [f"- {fact.get('text', '').strip()}" for fact in facts if str(fact.get("text") or "").strip()]
    if not lines:
        return ""
    return "Long-term memories:\n" + "\n".join(lines)

# Return true when the user asked to save a long-term fact
def needs_remember(prompt):
    text = prompt.lower()
    if needs_forget(prompt):
        return False
    return bool(re.search(r"\bremember\b", text))

# Return true when the user asked to drop a long-term fact
def needs_forget(prompt):
    return bool(re.search(r"\bforget\b", prompt.lower()))

# Retry remember once, then save it in Python if still missing
def force_remember(prompt, messages, message, already_retried, record_tool):
    text = parse_remember_text(prompt)

    # First miss, ask the model again with an explicit remember order
    if not already_retried:
        print("[memory] missing remember, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if text is None:
            retry = MEMORY_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call remember with text {text} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, save it directly in Python
    if text is None:
        return None
    arguments = {"text": text}
    result = run_remember(arguments)
    record_tool("remember", arguments, result)
    print(f"[memory] forced remember -> {result}", flush=True)
    return result

# Retry forget once, then drop it in Python if still missing
def force_forget(prompt, messages, message, already_retried, record_tool):
    text = parse_forget_text(prompt)

    # First miss, ask the model again with an explicit forget order
    if not already_retried:
        print("[memory] missing forget, retrying", flush=True)
        if message is not None:
            messages.append(message)
        if text is None:
            retry = FORGET_RETRY_PROMPT
        else:
            retry = f"Do not guess. Call forget with text {text} now, then answer using only the tool result."
        messages.append({"role": "user", "content": retry})
        return True

    # Second miss, forget it directly in Python
    if text is None:
        return None
    arguments = {"text": text}
    result = run_forget(arguments)
    record_tool("forget", arguments, result)
    print(f"[memory] forced forget -> {result}", flush=True)
    return result

# Save one long-term fact from tool arguments
def run_remember(arguments):
    text = str(arguments.get("text") or "").strip()
    if not text:
        return "Remember text is required."
    facts = load_memory_facts()

    # Replace an existing similar fact, or append a new one
    lowered = text.lower()
    for fact in facts:
        if str(fact.get("text") or "").strip().lower() == lowered:
            fact["text"] = text
            fact["saved_at"] = datetime.now().astimezone().isoformat()
            save_memory_facts(facts)
            return f"I have remembered that {text}"
    facts.append({"id": next_memory_id(facts), "text": text, "saved_at": datetime.now().astimezone().isoformat()})

    # Drop the oldest facts when over the cap
    while len(facts) > MAX_MEMORY_FACTS:
        facts.pop(0)
    save_memory_facts(facts)
    return f"I have remembered that {text}"

# Remove matching long-term facts from tool arguments
def run_forget(arguments):
    facts = load_memory_facts()
    if not facts:
        return "I do not have any long-term memories saved."
    fact_id = arguments.get("id")
    text = str(arguments.get("text") or "").strip().lower()
    kept = []
    removed = []
    for fact in facts:
        fact_text = str(fact.get("text") or "").strip()
        if fact_id is not None and int(fact.get("id") or -1) == int(fact_id):
            removed.append(fact_text)
            continue
        if text and text in fact_text.lower():
            removed.append(fact_text)
            continue
        kept.append(fact)
    if not removed:
        return "I could not find that memory to forget."
    save_memory_facts(kept)
    if len(removed) == 1:
        return f"I have forgotten that {removed[0]}"
    return f"I have forgotten {len(removed)} memories."

# Read the fact to remember from the user text
def parse_remember_text(prompt):
    text = prompt.strip()
    text = re.sub(r"[,.]?\s*(okay[,.]?\s*)?remember\s+(it|that|this)\s*[.!]?\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*remember\s+(that|this)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(okay[,.]?\s*)?(please\s+)?(i'?m gonna tell you[,.]?\s*)?", "", text, flags=re.IGNORECASE)
    text = text.strip(" ,.!")
    if not text:
        return None
    return text[0].upper() + text[1:]

# Read what to forget from the user text
def parse_forget_text(prompt):
    text = prompt.strip()
    match = re.search(r"\bforget\s+(?:that\s+|about\s+)?(.+?)\s*[.!]?\s*$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(" ,.!")
    return None

# Load remembered facts from disk
def load_memory_facts():
    if not os.path.isfile(MEMORY_PATH):
        return []
    try:
        with open(MEMORY_PATH, encoding="utf-8") as memory_file:
            data = json.load(memory_file)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    facts = data.get("facts")
    if not isinstance(facts, list):
        return []
    return [fact for fact in facts if isinstance(fact, dict) and str(fact.get("text") or "").strip()]

# Save remembered facts to disk atomically
def save_memory_facts(facts):
    payload = {"facts": facts}
    temporary_path = MEMORY_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as memory_file:
        json.dump(payload, memory_file, indent=2)
        memory_file.write("\n")
    os.replace(temporary_path, MEMORY_PATH)

# Next numeric id for a new memory fact
def next_memory_id(facts):
    if not facts:
        return 1
    return max(int(fact.get("id") or 0) for fact in facts) + 1

# Main
if __name__ == "__main__":
    main()
