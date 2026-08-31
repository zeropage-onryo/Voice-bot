"""
Tests for eval/extract.py against the actual messy-speech shapes this
project's transcripts contain: spoken month-and-word-year dates,
letter-by-letter spelled names, and grouped spoken digit phone numbers -
plus the filler words and false starts real callers produce around them.
"""

from eval.extract import (
    count_phi_items,
    mentions_dob,
    mentions_phone_number,
    mentions_spelled_name,
)


def test_mentions_dob_spoken_word_year():
    assert mentions_dob("Sure, it's March fifteenth, nineteen eighty-five.")
    assert mentions_dob("My birthday is October twenty-sixth, nineteen ninety-two.")


def test_mentions_dob_written_digit_year():
    assert mentions_dob("Date of birth: March 15, 1985")


def test_mentions_dob_false_positive_guard_month_alone():
    # A month name with no year attached isn't a date of birth.
    assert not mentions_dob("I'd like an appointment sometime next week, maybe March.")


def test_mentions_dob_with_filler_words():
    assert mentions_dob("Now... let me see... I was born on March twenty-third, nineteen thirty-nine.")


def test_mentions_spelled_name_hyphenated():
    assert mentions_spelled_name("My first name is Alex, A-L-E-X, and my last name is Johnson, J-O-H-N-S-O-N.")


def test_mentions_spelled_name_three_letters():
    # Regression: a 3-letter name is only 2 "letter+punct" units, not 3 -
    # this failed under an earlier, stricter version of the regex.
    assert mentions_spelled_name("Yeah, it's Sam, S-A-M, and Porter, P-O-R-T-E-R.")


def test_mentions_spelled_name_absent():
    assert not mentions_spelled_name("Hi, I'm Alex Johnson, calling about an appointment.")


def test_mentions_phone_number_spoken_grouped_digits():
    assert mentions_phone_number("Sure, my phone number is five five five, one two three, four five six seven.")


def test_mentions_phone_number_written():
    assert mentions_phone_number("You can reach me at 555-123-4567.")


def test_mentions_phone_number_absent():
    assert not mentions_phone_number("I've been dizzy for about three weeks now.")


def test_count_phi_items_all_three_in_one_turn():
    text = (
        "It's Alex, A-L-E-X, born March fifteenth, nineteen eighty-five, "
        "phone five five five, one two three, four five six seven."
    )
    assert count_phi_items(text) == 3


def test_count_phi_items_none():
    assert count_phi_items("I'd like to book an appointment for next week please.") == 0
