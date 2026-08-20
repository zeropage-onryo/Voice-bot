"""
Post-call retrieval: pull one conversation's transcript + audio from
ElevenLabs and write them into recordings/ and transcripts/ in exactly
the shape the old live bridge produced, so nothing downstream (the bug
report, the submission folder) changes.

This replaces the whole live recording pipeline (bridge/recorder.py):
ElevenLabs records the call platform-side now, so "recording" went from
buffering and mixing live mulaw frames to downloading a finished file.

Speaker mapping - easy to get backwards: in ElevenLabs' transcript,
`agent` is THE ELEVENLABS AGENT, i.e. our patient bot, and `user` is
whoever it's talking to, i.e. PGA's receptionist agent. The labels
below keep the exact strings the old CallRecorder wrote.

Usage (also called directly by place_call.py after placing a call):
    python fetch_conversation.py <conversation_id> [--name <file_stem>]
"""

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs import ElevenLabs

load_dotenv(Path(__file__).resolve().parent / ".env")

REPO_ROOT = Path(__file__).resolve().parent
RECORDINGS_DIR = REPO_ROOT / "recordings"
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"

SPEAKER_LABELS = {
    "agent": "PATIENT (our bot)",  # the ElevenLabs agent IS our patient
    "user": "PGA AGENT",           # the "user" it hears is PGA's agent
}

# `processing` means the call ended and ElevenLabs is finalizing the
# transcript/audio; `done` means everything is fetchable.
TERMINAL_STATUSES = {"done", "failed"}
POLL_SECONDS = 5


def wait_for_conversation(client: ElevenLabs, conversation_id: str, timeout: int = 900):
    """Poll until the conversation reaches a terminal state. Calls run
    minutes, not hours, so simple polling beats webhook plumbing here."""
    started = time.monotonic()
    last_status = None
    while True:
        conversation = client.conversational_ai.conversations.get(conversation_id)
        if conversation.status != last_status:
            print(f"  conversation {conversation_id}: {conversation.status}")
            last_status = conversation.status
        if conversation.status in TERMINAL_STATUSES:
            return conversation
        if time.monotonic() - started > timeout:
            raise SystemExit(
                f"Timed out after {timeout}s waiting for {conversation_id} "
                f"(last status: {conversation.status}). Re-run: "
                f"python fetch_conversation.py {conversation_id}"
            )
        time.sleep(POLL_SECONDS)


def write_transcript(conversation, path: Path) -> None:
    lines = []
    for entry in conversation.transcript or []:
        text = (entry.message or "").strip()
        if not text:
            continue  # tool calls / empty rows aren't dialogue
        label = SPEAKER_LABELS.get(entry.role, entry.role.upper())
        lines.append(f"[{label}] {text}")
    path.write_text("\n".join(lines))


def write_audio(client: ElevenLabs, conversation_id: str, path: Path) -> Path:
    audio = b"".join(client.conversational_ai.conversations.audio.get(conversation_id))
    # The endpoint serves MP3 today; sniff rather than trust, so a
    # format change on their side shows up as a renamed file, not a
    # corrupt ".mp3" nobody can play.
    if not (audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")):
        if audio[:4] == b"RIFF":
            path = path.with_suffix(".wav")
        else:
            path = path.with_suffix(".bin")
            print(f"  WARNING: unrecognized audio container, saved as {path.name}")
    path.write_bytes(audio)
    return path


def fetch(conversation_id: str, file_stem: str | None = None) -> dict:
    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    conversation = wait_for_conversation(client, conversation_id)

    if conversation.status == "failed":
        raise SystemExit(f"Conversation {conversation_id} failed on ElevenLabs' side.")

    RECORDINGS_DIR.mkdir(exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(exist_ok=True)
    stem = file_stem or conversation_id

    transcript_path = TRANSCRIPTS_DIR / f"{stem}.txt"
    write_transcript(conversation, transcript_path)

    audio_path = RECORDINGS_DIR / f"{stem}.mp3"
    if conversation.has_audio:
        audio_path = write_audio(client, conversation_id, audio_path)
    else:
        print("  WARNING: ElevenLabs reports no audio for this conversation.")

    print(f"Saved: {audio_path}")
    print(f"Saved: {transcript_path}")
    return {"audio_path": str(audio_path), "transcript_path": str(transcript_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch a finished call's recording + transcript.")
    parser.add_argument("conversation_id")
    parser.add_argument("--name", help="File stem to save under (default: the conversation id).")
    args = parser.parse_args()
    sys.exit(0 if fetch(args.conversation_id, args.name) else 1)
