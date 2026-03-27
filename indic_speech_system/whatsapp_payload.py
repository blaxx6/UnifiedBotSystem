"""
whatsapp_payload.py — Normalize and validate Evolution API webhook payloads.

Provides a structured NormalizedMessage dataclass instead of raw dict access,
a JID classifier, and event normalization. Replaces fragile .get() chains in
whatsapp_evolution.py with typed, validated attribute access.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JID CLASSIFICATION
# ---------------------------------------------------------------------------
# Evolution API sends different JID suffixes depending on context.

_JID_PATTERNS = {
    "phone": re.compile(r'^\d+@s\.whatsapp\.net$'),
    "lid":   re.compile(r'^\d+@lid$'),
    "group": re.compile(r'^[\d\-]+@g\.us$'),
    "broadcast": re.compile(r'^status@broadcast$'),
}


def classify_jid(jid: str) -> str:
    """
    Classify a WhatsApp JID into one of:
      "phone"     — normal user (e.g. 919876543210@s.whatsapp.net)
      "lid"       — linked-device ID (e.g. 33299267367051@lid)
      "group"     — group chat (e.g. 120363048788976@g.us)
      "broadcast" — status broadcast
      "unknown"   — unrecognised format
    """
    if not jid:
        return "unknown"
    for kind, pattern in _JID_PATTERNS.items():
        if pattern.match(jid):
            return kind
    return "unknown"


# ---------------------------------------------------------------------------
# EVENT NORMALIZATION
# ---------------------------------------------------------------------------
# Evolution API versions differ: some send "messages.upsert", others
# "MESSAGES_UPSERT", etc. Normalise to a single canonical form.

_CANONICAL_EVENTS = {
    "messages.upsert":  "MESSAGES_UPSERT",
    "messages_upsert":  "MESSAGES_UPSERT",
    "messages.update":  "MESSAGES_UPDATE",
    "messages_update":  "MESSAGES_UPDATE",
}


def normalize_event(raw_event: str) -> str:
    """Return canonical event name (uppercase, underscore-separated)."""
    return _CANONICAL_EVENTS.get(raw_event.lower().strip(), raw_event.upper())


# ---------------------------------------------------------------------------
# NORMALIZED MESSAGE DATACLASS
# ---------------------------------------------------------------------------

@dataclass
class NormalizedMessage:
    """
    A validated, normalized representation of an Evolution API webhook payload.
    All fields are guaranteed to have safe defaults if the raw payload was
    missing keys.
    """
    event_type: str             # Always uppercase, e.g. "MESSAGES_UPSERT"
    message_id: str             # Unique message ID from key.id
    remote_jid: str             # Raw JID from payload (key.remoteJid)
    jid_type: str               # "phone" | "lid" | "group" | "broadcast" | "unknown"
    push_name: str              # Sender display name
    is_from_me: bool            # True if sent by the bot itself
    text_content: str           # Extracted text (from conversation/extendedText/caption)
    media_type: str | None      # "audio" | "image" | "document" | None
    media_message: dict | None  # Raw media sub-message dict
    raw_key: dict               # Original key dict for quote construction
    raw_message: dict           # Original message body dict
    message_timestamp: int = 0  # Unix timestamp


def _extract_text_and_media(message_body: dict) -> tuple[str, str | None, dict | None]:
    """
    Walk the Evolution API message body and extract:
      (text_content, media_type, media_message_dict)

    Handles all known message shapes in priority order.
    """
    text = ""
    media_type = None
    media_msg = None

    if "conversation" in message_body:
        text = message_body["conversation"]

    elif "extendedTextMessage" in message_body:
        text = message_body["extendedTextMessage"].get("text", "")

    elif "audioMessage" in message_body:
        media_type = "audio"
        media_msg = message_body["audioMessage"]

    elif "imageMessage" in message_body:
        media_type = "image"
        media_msg = message_body["imageMessage"]
        text = media_msg.get("caption", "")

    elif "documentMessage" in message_body:
        media_type = "document"
        media_msg = message_body["documentMessage"]
        text = media_msg.get("caption", "")

    elif "videoMessage" in message_body:
        media_type = "video"
        media_msg = message_body["videoMessage"]
        text = media_msg.get("caption", "")

    elif "stickerMessage" in message_body:
        media_type = "sticker"
        media_msg = message_body["stickerMessage"]

    return text, media_type, media_msg


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def parse_webhook_payload(data: dict | None) -> NormalizedMessage | None:
    """
    Parse and validate an Evolution API webhook payload.

    Returns a NormalizedMessage on success, or None if the payload should be
    silently ignored (e.g. wrong event type, sent by self, malformed).
    """
    if not data or not isinstance(data, dict):
        logger.debug("Ignoring empty/non-dict payload")
        return None

    # --- Event type ---
    raw_event = data.get("event", "")
    event_type = normalize_event(raw_event)
    if event_type not in ("MESSAGES_UPSERT", "MESSAGES_UPDATE"):
        logger.debug("Ignoring event: %s", event_type)
        return None

    # --- Key extraction ---
    msg_content = data.get("data", {})
    if not isinstance(msg_content, dict):
        logger.warning("Malformed payload: 'data' is not a dict")
        return None

    key = msg_content.get("key", {})
    if not isinstance(key, dict):
        logger.warning("Malformed payload: 'key' is not a dict")
        return None

    # --- Self-message filter ---
    if key.get("fromMe", False):
        return None

    # --- Core fields ---
    message_id = key.get("id", "")
    remote_jid = key.get("remoteJid", "")
    push_name = msg_content.get("pushName", "User")
    jid_type = classify_jid(remote_jid)
    timestamp = msg_content.get("messageTimestamp", 0)

    # --- Message body ---
    message_body = msg_content.get("message", {})
    if not isinstance(message_body, dict):
        message_body = {}

    text_content, media_type, media_msg = _extract_text_and_media(message_body)

    # --- Construct normalised message ---
    msg = NormalizedMessage(
        event_type=event_type,
        message_id=message_id,
        remote_jid=remote_jid,
        jid_type=jid_type,
        push_name=push_name,
        is_from_me=False,
        text_content=text_content,
        media_type=media_type,
        media_message=media_msg,
        raw_key=key,
        raw_message=message_body,
        message_timestamp=int(timestamp) if timestamp else 0,
    )

    logger.debug(
        "Parsed message: id=%s jid=%s(%s) text_len=%d media=%s",
        msg.message_id[:12],
        msg.remote_jid,
        msg.jid_type,
        len(msg.text_content),
        msg.media_type or "none",
    )

    return msg


def build_quoted_payload(msg: NormalizedMessage) -> dict | None:
    """Build quoted message payload for reply-quoting."""
    if not msg.message_id:
        return None
    return {
        "key": {
            "remoteJid": msg.remote_jid,
            "fromMe": False,
            "id": msg.message_id,
        },
        "message": msg.raw_message,
    }
