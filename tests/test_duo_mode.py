import pytest

import duo_mode


def test_next_role_toggles():
    assert duo_mode.next_role(duo_mode.ROLE_VISIONAER) == duo_mode.ROLE_PRAGMATIKER
    assert duo_mode.next_role(duo_mode.ROLE_PRAGMATIKER) == duo_mode.ROLE_VISIONAER


def test_next_role_unknown_input_defaults_to_visionaer():
    # Alles, was nicht explizit "Visionär" ist, wird als "aktuell Pragmatiker"
    # behandelt und wechselt zu Visionär -- verhindert ein Stecken-bleiben.
    assert duo_mode.next_role("irgendwas") == duo_mode.ROLE_VISIONAER


def test_build_system_instruction_off_returns_base_unchanged():
    base = "Du bist ein hilfreicher Assistent."
    assert duo_mode.build_system_instruction(base, "off") == base


def test_build_system_instruction_auto_mentions_both_roles():
    base = "Basis-Instruktion."
    result = duo_mode.build_system_instruction(base, "auto")
    assert base in result
    assert duo_mode.ROLE_VISIONAER in result
    assert duo_mode.ROLE_PRAGMATIKER in result
    assert "JEDER" in result


def test_build_system_instruction_manual_mentions_start_role():
    base = "Basis-Instruktion."
    result = duo_mode.build_system_instruction(base, "manual", start_role=duo_mode.ROLE_PRAGMATIKER)
    assert f"Du beginnst als {duo_mode.ROLE_PRAGMATIKER}" in result
    assert "Systemnachricht" in result


def test_build_system_instruction_rejects_unknown_mode():
    with pytest.raises(ValueError):
        duo_mode.build_system_instruction("Basis", "chaos")


def test_build_switch_message_names_the_role():
    msg = duo_mode.build_switch_message(duo_mode.ROLE_PRAGMATIKER)
    assert duo_mode.ROLE_PRAGMATIKER in msg
    assert msg.startswith("[Systemhinweis")
