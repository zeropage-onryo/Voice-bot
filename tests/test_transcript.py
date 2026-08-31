"""
Tests for eval/transcript.py, runnable offline with no API key and no
network - same discipline BUILD_SPEC_EVAL_LAYER.md sec. 9 requires of
the full check suite, applied here first since every check depends on
this parser being right.
"""

from pathlib import Path

import pytest

from eval.transcript import Speaker, TranscriptError, parse_transcript

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = REPO_ROOT / "transcripts"


def test_parses_basic_two_speaker_transcript():
    text = (
        "[PGA AGENT] Thanks for calling. How can I help?\n"
        "[PATIENT (our bot)] I'd like to book an appointment.\n"
    )
    transcript = parse_transcript(text, stem="fixture", target="pga")
    assert [t.speaker for t in transcript.turns] == [
        Speaker.AGENT_UNDER_TEST,
        Speaker.CALLER,
    ]
    assert transcript.turns[1].text == "I'd like to book an appointment."
    assert transcript.stem == "fixture"
    assert transcript.target == "pga"


def test_turn_index_is_positional():
    text = "[PGA AGENT] One.\n[PATIENT (our bot)] Two.\n[PGA AGENT] Three.\n"
    transcript = parse_transcript(text, stem="fixture", target="pga")
    assert [t.index for t in transcript.turns] == [0, 1, 2]


def test_continuation_line_extends_previous_turn():
    text = "[PGA AGENT] First part\nsecond part on its own line\n"
    transcript = parse_transcript(text, stem="fixture", target="pga")
    assert len(transcript.turns) == 1
    assert transcript.turns[0].text == "First part second part on its own line"


def test_unrecognized_label_is_a_hard_error():
    text = "[SOME NEW LABEL] hello\n"
    with pytest.raises(TranscriptError, match="unrecognized speaker label"):
        parse_transcript(text, stem="fixture", target="pga")


def test_continuation_with_no_prior_turn_is_a_hard_error():
    text = "no label on this line at all\n"
    with pytest.raises(TranscriptError, match="no prior turn"):
        parse_transcript(text, stem="fixture", target="pga")


def test_blank_lines_are_ignored():
    text = "[PGA AGENT] Hello.\n\n\n[PATIENT (our bot)] Hi.\n"
    transcript = parse_transcript(text, stem="fixture", target="pga")
    assert len(transcript.turns) == 2


@pytest.mark.parametrize("path", sorted(TRANSCRIPTS_DIR.glob("*.txt")), ids=lambda p: p.stem)
def test_every_real_transcript_in_the_repo_parses_cleanly(path):
    """The acceptance bar: this parser must handle every call actually
    produced by fetch_conversation.py, not just hand-written fixtures."""
    from eval.transcript import load_transcript

    transcript = load_transcript(path, target="pga")
    assert len(transcript.turns) > 0
    # Every existing call alternates caller/agent-under-test with no
    # unknown speakers - if this ever fails on a real file, the fixture
    # tests above already isolate whether it's the parser or the data.
    speakers = {t.speaker for t in transcript.turns}
    assert speakers <= {Speaker.CALLER, Speaker.AGENT_UNDER_TEST}
