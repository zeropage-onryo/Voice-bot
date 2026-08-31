"""
Tests for scenarios/matrix.py: composing intent + behavior primitives
into full scenario dicts. Runs offline, no network, no API key.

Every write test uses tmp_path as the scenarios_dir - never the real
scenarios/ directory, so this suite can never clobber or collide with
the 12 hand-written scenarios already in the repo.
"""

import json

import pytest

from scenarios import loader as scenario_loader
from scenarios.matrix import (
    MatrixError,
    available_behaviors,
    available_domains,
    available_intents,
    compose,
    full_matrix,
    load_behavior,
    load_intent,
    write_scenario,
)

KNOWN_DOMAIN = "healthcare_intake"


def test_at_least_one_domain_and_it_is_healthcare_intake():
    assert KNOWN_DOMAIN in available_domains()


def test_five_behaviors_are_seeded():
    assert set(available_behaviors()) == {
        "calm_baseline",
        "interrupting",
        "frustrated",
        "elderly_slow",
        "vague_unclear",
    }


def test_six_healthcare_intake_intents_are_seeded():
    assert set(available_intents(KNOWN_DOMAIN)) == {
        "schedule_appointment",
        "cancel_and_rebook",
        "refill_simple",
        "refill_controlled_substance",
        "insurance_question",
        "family_member_on_behalf",
    }


@pytest.mark.parametrize("behavior_id", available_behaviors())
def test_every_behavior_file_loads_and_validates(behavior_id):
    behavior = load_behavior(behavior_id)
    assert behavior["id"] == behavior_id
    assert behavior["description"]
    assert behavior["voice_direction"]


@pytest.mark.parametrize("intent_id", available_intents(KNOWN_DOMAIN))
def test_every_healthcare_intake_intent_file_loads_and_validates(intent_id):
    intent = load_intent(KNOWN_DOMAIN, intent_id)
    assert intent["id"] == intent_id
    assert intent["domain"] == KNOWN_DOMAIN
    assert intent["description"]
    assert intent["goal_template"]


def test_load_behavior_unknown_id_raises_with_available_list():
    with pytest.raises(MatrixError, match="Unknown behavior"):
        load_behavior("not_a_real_behavior")


def test_load_intent_unknown_domain_raises_with_available_list():
    with pytest.raises(MatrixError, match="Unknown domain"):
        load_intent("not_a_real_domain", "schedule_appointment")


def test_load_intent_unknown_id_raises_with_available_list():
    with pytest.raises(MatrixError, match="Unknown intent"):
        load_intent(KNOWN_DOMAIN, "not_a_real_intent")


def test_compose_produces_the_exact_fields_scenarios_loader_requires():
    scenario = compose(KNOWN_DOMAIN, "schedule_appointment", "interrupting")
    for field in scenario_loader.REQUIRED_FIELDS:
        assert scenario.get(field), f"composed scenario is missing {field!r}"
    assert scenario["id"] == "schedule_appointment__interrupting"


def test_compose_keeps_goal_and_voice_direction_independent():
    """The whole point of the split: an intent's goal_template is
    injected verbatim as `goal`, a behavior's voice_direction verbatim
    as `voice_direction` - swapping the behavior must never change the
    goal text, and swapping the intent must never change the voice
    direction text."""
    a = compose(KNOWN_DOMAIN, "schedule_appointment", "calm_baseline")
    b = compose(KNOWN_DOMAIN, "schedule_appointment", "frustrated")
    assert a["goal"] == b["goal"]
    assert a["voice_direction"] != b["voice_direction"]

    c = compose(KNOWN_DOMAIN, "refill_simple", "calm_baseline")
    assert a["voice_direction"] == c["voice_direction"]
    assert a["goal"] != c["goal"]


def test_compose_carries_the_behaviors_voice_and_speed():
    scenario = compose(KNOWN_DOMAIN, "schedule_appointment", "elderly_slow")
    behavior = load_behavior("elderly_slow")
    assert scenario["voice"] == behavior["voice"]
    assert scenario["speed"] == behavior["speed"]


def test_compose_omits_speed_when_the_behavior_does_not_specify_one():
    # calm_baseline.json has no "speed" key - the default (1.0) should
    # apply downstream in scenarios/loader.py, not get invented here.
    scenario = compose(KNOWN_DOMAIN, "schedule_appointment", "calm_baseline")
    assert "speed" not in scenario


def test_compose_validates_against_scenarios_loaders_own_rules():
    """A behavior with a bad voice id or an out-of-range speed must be
    caught here, before anything is ever written to disk."""
    import scenarios.matrix as matrix_module

    bad_behavior = {
        "id": "bad_voice_behavior",
        "description": "broken on purpose",
        "voice_direction": "irrelevant",
        "voice": "not-a-real-voice-id",
    }

    def fake_load_behavior(behavior_id):
        return bad_behavior

    original = matrix_module.load_behavior
    matrix_module.load_behavior = fake_load_behavior
    try:
        with pytest.raises(MatrixError, match="invalid"):
            compose(KNOWN_DOMAIN, "schedule_appointment", "bad_voice_behavior")
    finally:
        matrix_module.load_behavior = original


def test_full_matrix_is_the_full_cartesian_product():
    matrix = full_matrix(KNOWN_DOMAIN)
    expected = len(available_intents(KNOWN_DOMAIN)) * len(available_behaviors())
    assert len(matrix) == expected
    assert len({s["id"] for s in matrix}) == expected  # every id unique


def test_write_scenario_writes_a_file_loadable_by_scenarios_loaders_own_rules(tmp_path):
    scenario = compose(KNOWN_DOMAIN, "insurance_question", "vague_unclear")
    path = write_scenario(scenario, scenarios_dir=tmp_path)
    assert path.exists()

    on_disk = json.loads(path.read_text())
    assert on_disk == scenario
    missing = [f for f in scenario_loader.REQUIRED_FIELDS if not str(on_disk.get(f, "")).strip()]
    assert not missing
    scenario_loader.get_voice(on_disk)  # raises ScenarioError if invalid
    scenario_loader.get_speed(on_disk)


def test_write_scenario_refuses_to_overwrite_by_default(tmp_path):
    scenario = compose(KNOWN_DOMAIN, "insurance_question", "vague_unclear")
    write_scenario(scenario, scenarios_dir=tmp_path)
    with pytest.raises(MatrixError, match="already exists"):
        write_scenario(scenario, scenarios_dir=tmp_path)


def test_write_scenario_overwrite_true_replaces_the_file(tmp_path):
    scenario = compose(KNOWN_DOMAIN, "insurance_question", "vague_unclear")
    path = write_scenario(scenario, scenarios_dir=tmp_path)
    # Same id, different content - proves overwrite=True actually replaces it.
    mutated = dict(scenario, description="mutated for the test")
    write_scenario(mutated, scenarios_dir=tmp_path, overwrite=True)
    assert json.loads(path.read_text())["description"] == "mutated for the test"


def test_full_matrix_never_collides_with_a_real_hand_written_scenario_id():
    """The 30 generated ids use a double-underscore separator
    (<intent>__<behavior>) specifically so they can never collide with
    one of the 12 curated scenario ids, which don't use that separator.
    This guards write_scenario's collision check actually being
    meaningful against scenarios/ as it exists today."""
    real_ids = set(scenario_loader.available_scenarios())
    matrix_ids = {s["id"] for s in full_matrix(KNOWN_DOMAIN)}
    assert real_ids.isdisjoint(matrix_ids)
