"""Event collection for bounded leaf reviews; never persist reasoning or tool payloads."""

from __future__ import annotations

import json
import threading
import time


class ReviewActivity:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()
        self.last_event_at = time.time()
        self.phase = "starting"
        self.events = 0
        self.session_id = None
        self.models = set()
        self.completed = False
        self.protocol_error = None

    def touch(self, phase=None):
        with self.lock:
            self.last_activity = time.monotonic()
            self.last_event_at = time.time()
            if phase:
                self.phase = phase

    def observe_model(self, model):
        if isinstance(model, str) and model != "<synthetic>":
            with self.lock:
                if len(self.models) < 16:
                    self.models.add(model[:256])
                elif model[:256] not in self.models:
                    self.protocol_error = "too_many_response_models"

    def snapshot(self):
        with self.lock:
            return {
                "phase": self.phase,
                "last_event_at_epoch": self.last_event_at,
                "idle_seconds": round(time.monotonic() - self.last_activity, 3),
                "event_count": self.events,
                "session_id": self.session_id,
                "actual_models": sorted(self.models),
                "completion_received": self.completed,
                "protocol_error": self.protocol_error,
            }


def observe_pipe(source, spool, activity, forward=None):
    try:
        while chunk := getattr(source, "read1", source.read)(8192):
            activity.touch()
            spool.write(chunk)
            if forward:
                forward(chunk)
    except Exception as exc:
        with activity.lock:
            activity.protocol_error = type(exc).__name__


def collect_claude_events(source, report, partial, activity, event_limit=2 * 1024 * 1024):
    """Final result alone is authoritative. Partial text survives cancellation separately.

    A complete assistant message duplicates stream deltas, so use it as a
    fallback only when that message had no text deltas. Error/result events
    never become successful reports. The line cap bounds malformed streams.
    """
    buffer = bytearray()
    discard_line = False
    streamed_text = False

    def consume(raw):
        nonlocal streamed_text
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("event is not an object")
            kind = event.get("type")
            with activity.lock:
                activity.events += 1
                sid = event.get("session_id")
                if isinstance(sid, str):
                    activity.session_id = sid[:128]
            if kind == "system" and event.get("subtype") == "init":
                # An init model can be an alias; only assistant messages prove
                # the model reported by the provider for an actual response.
                activity.touch("initialized")
            elif kind == "stream_event":
                inner = event.get("event", {})
                typ = inner.get("type")
                if typ == "message_start":
                    streamed_text = False
                    message = inner.get("message", {})
                    model = message.get("model")
                    activity.observe_model(model)
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if isinstance(text, str):
                        partial.write(text.encode())
                        streamed_text = True
                    activity.touch("answering")
                elif delta.get("type") == "thinking_delta":
                    activity.touch("thinking")
                elif typ == "content_block_start" and inner.get("content_block", {}).get("type") == "tool_use":
                    activity.touch("tool_use")
            elif kind == "assistant":
                message = event.get("message", {})
                model = message.get("model")
                activity.observe_model(model)
                for block in message.get("content", []):
                    if block.get("type") == "text" and not streamed_text:
                        text = block.get("text", "")
                        if not isinstance(text, str):
                            raise ValueError("assistant text is not a string")
                        partial.write(text.encode())
                    elif block.get("type") == "tool_use":
                        activity.touch("tool_use")
                streamed_text = False
            elif kind == "result":
                with activity.lock:
                    if activity.completed:
                        raise ValueError("duplicate result")
                    activity.completed = True
                    if event.get("is_error") or event.get("subtype") != "success":
                        activity.protocol_error = "upstream_result_error"
                if not event.get("is_error") and event.get("subtype") == "success":
                    result = event.get("result")
                    if not isinstance(result, str):
                        raise ValueError("result text missing")
                    report.write(result.encode())
                activity.touch("completed")
        except (ValueError, TypeError, AttributeError) as exc:
            with activity.lock:
                activity.protocol_error = "invalid_stream_event:" + type(exc).__name__

    try:
        while chunk := getattr(source, "read1", source.read)(8192):
            activity.touch()
            # Split before buffering: even a newline-free hostile stream must
            # not allocate unbounded memory or prevent timeout supervision.
            for index, piece in enumerate(chunk.split(b"\n")):
                if index:
                    if buffer and not discard_line:
                        consume(bytes(buffer))
                    buffer.clear()
                    discard_line = False
                if not discard_line:
                    buffer.extend(piece)
                    if len(buffer) > event_limit:
                        buffer.clear()
                        discard_line = True
                        with activity.lock:
                            activity.protocol_error = "stream_event_too_large"
        if buffer and not discard_line:
            consume(bytes(buffer))
    except Exception as exc:
        with activity.lock:
            activity.protocol_error = "stream_reader:" + type(exc).__name__
