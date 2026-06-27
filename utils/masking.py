"""Helpers for masking secret-bearing values.

Apprise notification URLs embed tokens, passwords, and webhook secrets. Use
:func:`mask_secret` whenever such a value is surfaced to a user — in logs,
dashboard output, notifications, or API responses. Raw values are kept only in
configuration storage (the .env file and the in-memory URL list).
"""


def mask_secret(value: str) -> str:
    """Mask credentials in a secret-bearing URL for safe display/logging.

    Keeps the scheme (so the service stays identifiable) and the last few
    characters (so entries remain distinguishable), masking everything in
    between. Plain (non-URL) text is returned unchanged.

    Examples:
        discord://id/token         -> discord://******oken
        tgram://bot_token/chat_id  -> tgram://******t_id
        https://host/hook/secret   -> https://******cret
        "Beta is now OPEN"         -> "Beta is now OPEN"   (unchanged)
    """
    if not isinstance(value, str) or "://" not in value:
        return value
    scheme, sep, rest = value.partition("://")
    if not rest:
        return value
    tail = rest[-4:] if len(rest) > 4 else ""
    return f"{scheme}{sep}{'*' * 6}{tail}"
