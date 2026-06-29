"""One-line trace logging. Plain text, not JSON dumps."""

import datetime

_LOG_PATH = "scenario_trace.log"
_capture_buffer = None


def log(variant: str, account: str, action: str, outcome: str, why: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} | {variant} | {account} | {action} | {outcome} | {why}"
    print(line)
    with open(_LOG_PATH, "a") as f:
        f.write(line + "\n")
    if _capture_buffer is not None:
        _capture_buffer.append(line)


def begin_capture() -> None:
    """Start collecting log lines in memory, in addition to the usual file write."""
    global _capture_buffer
    _capture_buffer = []


def end_capture() -> list:
    """Stop collecting and return the lines logged since begin_capture()."""
    global _capture_buffer
    buf = _capture_buffer or []
    _capture_buffer = None
    return buf
