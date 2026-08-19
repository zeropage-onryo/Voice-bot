"""
The voice layer: ElevenLabs streaming text-to-speech.

Kept separate from server.py for the same reason recorder.py is:
server.py stays "the plumbing between Twilio and the brain," and the
question "how does text become telephone audio" lives entirely here.

Two things in this file exist because of measurements, not guesses
(spiked 2026-08-19, see ARCHITECTURE.md):

- `output_format=ulaw_8000` comes back byte-for-byte ready for Twilio
  Media Streams - mu-law, 8kHz, one byte per sample. No resampling,
  which is the property that ruled out Gemini Live back when this
  project chose its first voice stack.
- The client holds one warm connection pool. A cold TLS handshake to
  api.elevenlabs.io cost ~1.1s extra on the first request (1430ms vs a
  ~280ms warm median for the same request) - on a phone call that is a
  full second of dead air on the bot's first line. `warm_up()` pays
  that cost at call start, before the first real turn needs it.
"""

import os

import httpx

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

# Flash v2.5: fastest ElevenLabs model measured (median 278ms to first
# byte, warm, vs 338ms for turbo_v2_5) and the one their own telephony
# docs point at. Quality tradeoff vs the bigger models is acceptable
# here: the audio ends up 8kHz mu-law phone audio anyway.
MODEL_ID = "eleven_flash_v2_5"


class ElevenLabsTTS:
    """One instance per server process, shared across calls, so the
    connection pool stays warm between turns and between calls."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ["ELEVENLABS_API_KEY"]
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def warm_up(self) -> None:
        """Open the TLS connection before the first turn needs it.
        Failure here is non-fatal - the first real request just pays
        the cold-start cost instead."""
        try:
            await self._client.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": self.api_key},
            )
        except httpx.HTTPError:
            pass

    async def stream(self, text: str, voice_id: str):
        """Yield mu-law 8kHz audio chunks for `text` as they arrive.
        Raises httpx.HTTPStatusError on a non-200, letting the caller
        decide what a voiceless turn should do."""
        async with self._client.stream(
            "POST",
            ELEVENLABS_TTS_URL.format(voice_id=voice_id),
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            params={"output_format": "ulaw_8000"},
            json={"text": text, "model_id": MODEL_ID},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()
