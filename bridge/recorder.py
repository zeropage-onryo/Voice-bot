"""
Per-call recording + transcript capture.

Kept separate from server.py (which only relays audio) so each piece
stays readable on its own: server.py handles the live plumbing, this
file handles turning that live audio into the two things PGA's
submission actually requires - an audio file and a transcript.

How the audio recording works
------------------------------
Twilio and OpenAI both speak mulaw (g711_ulaw) at 8kHz - the same
format used for the live relay in server.py, so no extra conversion is
needed to *play* the call. But to save a single listenable recording of
*both sides* of the conversation, the two directions (PGA's agent
speaking, and our patient bot speaking) need to be decoded to linear
PCM and mixed into one track, since they're two separate audio streams
arriving at different times, not naturally interleaved.

Python's stdlib used to ship `audioop` for exactly this kind of mulaw
<-> PCM conversion, but it was removed in Python 3.13. `audioop-lts` is
the maintained drop-in replacement the community moved to; the import
below falls back to it automatically.
"""

import subprocess
import wave
from pathlib import Path

try:
    # stdlib on Python <= 3.12; on 3.13+ the audioop-lts backport
    # installs itself under this same name, so one import covers both.
    # (An earlier version fell back to `import audioop_lts` - a module
    # name that has never existed; the package is audioop-lts but the
    # module it provides is plain `audioop`. See ITERATION_LOG.md
    # 2026-08-19.)
    import audioop
except ImportError as exc:
    raise ImportError(
        "No audioop module. On Python 3.13+ install the backport: "
        "venv/bin/pip install -r requirements.txt (provides audioop-lts)."
    ) from exc

SAMPLE_WIDTH = 2  # 16-bit PCM once decoded from mulaw
FRAME_RATE = 8000  # matches Twilio's telephony audio


class CallRecorder:
    """Buffers one call's audio (both directions) and transcript in
    memory, then writes them out when the call ends. Call volume for
    this project is small (short test calls), so buffering in memory
    instead of streaming to disk keeps this simple and easy to read."""

    def __init__(self, call_sid: str):
        self.call_sid = call_sid
        self._agent_audio = bytearray()   # PGA's agent - what we hear
        self._patient_audio = bytearray()  # our bot - what we say
        self.transcript: list[tuple[str, str]] = []  # (speaker, text)

    def add_agent_audio(self, mulaw_chunk: bytes) -> None:
        self._agent_audio += mulaw_chunk

    def add_patient_audio(self, mulaw_chunk: bytes) -> None:
        self._patient_audio += mulaw_chunk

    def add_transcript_line(self, speaker: str, text: str) -> None:
        if text.strip():
            self.transcript.append((speaker, text.strip()))

    def _mixed_pcm(self) -> bytes:
        """Decode both mulaw tracks to linear PCM and mix them into one
        track. Pads the shorter side with silence first so the two
        stay aligned instead of one just cutting off early."""
        agent_pcm = audioop.ulaw2lin(bytes(self._agent_audio), SAMPLE_WIDTH)
        patient_pcm = audioop.ulaw2lin(bytes(self._patient_audio), SAMPLE_WIDTH)

        longest = max(len(agent_pcm), len(patient_pcm))
        agent_pcm += b"\x00" * (longest - len(agent_pcm))
        patient_pcm += b"\x00" * (longest - len(patient_pcm))

        return audioop.add(agent_pcm, patient_pcm, SAMPLE_WIDTH)

    def save(self, recordings_dir: Path, transcripts_dir: Path) -> dict:
        recordings_dir.mkdir(parents=True, exist_ok=True)
        transcripts_dir.mkdir(parents=True, exist_ok=True)

        wav_path = recordings_dir / f"{self.call_sid}.wav"
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(SAMPLE_WIDTH)
            wav_file.setframerate(FRAME_RATE)
            wav_file.writeframes(self._mixed_pcm())

        audio_path = self._convert_to_mp3(wav_path)

        transcript_path = transcripts_dir / f"{self.call_sid}.txt"
        transcript_path.write_text(
            "\n".join(f"[{speaker}] {text}" for speaker, text in self.transcript)
        )

        return {"audio_path": str(audio_path), "transcript_path": str(transcript_path)}

    def _convert_to_mp3(self, wav_path: Path) -> Path:
        """PGA requires OGG or MP3, not WAV. Shells out to ffmpeg since
        it's the standard tool for this and avoids pulling in an extra
        Python audio-encoding dependency. Falls back to keeping the WAV
        (with a clear warning) if ffmpeg isn't installed, rather than
        failing the whole call recording over a missing conversion."""
        mp3_path = wav_path.with_suffix(".mp3")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", str(mp3_path)],
                check=True, capture_output=True,
            )
            wav_path.unlink()
            return mp3_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(
                f"WARNING: ffmpeg not available - kept {wav_path.name} as WAV. "
                "Install ffmpeg (`brew install ffmpeg`) and re-run to get MP3, "
                "since the submission requires OGG or MP3."
            )
            return wav_path
