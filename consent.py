"""Normalize reported consent without inferring authorization."""


def affirmative_consent(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return isinstance(value, str) and value.strip().casefold() in {'yes', 'true', '1', 'tick', 'selected'}
