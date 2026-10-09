"""Small helpers for keeping per-session provider history bounded."""


def trim_messages(messages: list, max_turns: int) -> None:
    """Trim role-dict histories in place, retaining the system prompt and recent turns."""
    system = messages[:1] if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system" else []
    start = len(system)
    user_indexes = [i for i in range(start, len(messages))
                    if isinstance(messages[i], dict) and messages[i].get("role") == "user"]
    keep_from = user_indexes[max(0, len(user_indexes) - max(0, max_turns))] \
        if max_turns and len(user_indexes) > max_turns else len(system)
    if max_turns <= 0:
        keep_from = len(messages)
    messages[:] = system + messages[keep_from:]


def trim_gemini_contents(contents: list, max_turns: int) -> None:
    """Trim Gemini Content objects while not counting function responses as user turns."""
    def is_user_turn(item) -> bool:
        if getattr(item, "role", None) != "user":
            return False
        return any(getattr(part, "text", None) for part in (getattr(item, "parts", None) or []))

    user_indexes = [i for i, item in enumerate(contents) if is_user_turn(item)]
    if max_turns <= 0:
        contents.clear()
    elif len(user_indexes) > max_turns:
        del contents[:user_indexes[-max_turns]]
