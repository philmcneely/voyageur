"""Unit: the configuration readers (one parse per setting; errors name the variable and never the value)."""
import pytest

from voyageur.config import (ConfigError, _boolean, _choice, _integer, _parse, _text, _v)

pytestmark = pytest.mark.unit

VARS = (
    _v("port", "HEAL_PORT", _integer(1)),
    _v("retention", "HEAL_RETENTION_DAYS", _integer(0)),
    _v("mode", "HEAL_MODE", _choice("a", "b", aliases={"x": "a"})),
    _v("flag", "HEAL_FLAG", _boolean),
    _v("caller", "HEAL_CALLER", keep_empty=True),
    _v("base", "HEAL_NEW_BASE HEAL_OLD_BASE", _text),
)


def test_no_variables_means_no_fields_so_the_dataclass_defaults_apply():
    assert _parse({}, VARS) == {}


@pytest.mark.parametrize("name,bad", [("HEAL_PORT", "0"), ("HEAL_PORT", "ten"), ("HEAL_PORT", "1.5"),
                                      ("HEAL_RETENTION_DAYS", "-1"), ("HEAL_RETENTION_DAYS", "sk-secret-value")])
def test_a_bad_number_names_the_variable_and_not_the_value(name, bad):
    with pytest.raises(ConfigError) as err:
        _parse({name: bad}, VARS)
    assert name in str(err.value) and bad not in str(err.value)


def test_a_zero_is_a_valid_value_where_the_minimum_is_zero():
    assert _parse({"HEAL_RETENTION_DAYS": "0"}, VARS) == {"retention": 0}


def test_a_choice_outside_its_values_lists_them_and_aliases_map_to_one():
    with pytest.raises(ConfigError, match="a | b"):
        _parse({"HEAL_MODE": "c"}, VARS)
    assert _parse({"HEAL_MODE": " X "}, VARS) == {"mode": "a"}


@pytest.mark.parametrize("word,value", [("1", True), ("TRUE", True), ("yes", True), ("On", True),
                                        ("0", False), ("false", False), ("NO", False), ("off", False)])
def test_every_flag_goes_through_the_one_boolean_parser(word, value):
    assert _parse({"HEAL_FLAG": word}, VARS) == {"flag": value}


def test_a_flag_that_is_neither_yes_nor_no_is_an_error_not_silently_off():
    with pytest.raises(ConfigError, match="HEAL_FLAG"):
        _parse({"HEAL_FLAG": "maybe"}, VARS)


def test_blank_means_unset_except_where_empty_is_a_value():
    assert _parse({"HEAL_PORT": "  ", "HEAL_FLAG": ""}, VARS) == {}
    assert _parse({"HEAL_CALLER": ""}, VARS) == {"caller": ""}


def test_the_first_name_that_is_set_wins_and_the_old_name_is_a_fallback():
    assert _parse({"HEAL_OLD_BASE": "old"}, VARS) == {"base": "old"}
    assert _parse({"HEAL_OLD_BASE": "old", "HEAL_NEW_BASE": "new"}, VARS) == {"base": "new"}
    assert _parse({"HEAL_NEW_BASE": " ", "HEAL_OLD_BASE": "old"}, VARS) == {"base": "old"}


def test_values_are_stripped():
    assert _parse({"HEAL_CALLER": " ci \n"}, VARS) == {"caller": "ci"}


# ---- ServiceSettings ----------------------------------------------------------------------------------------------
def test_service_settings_defaults_and_overrides():
    from voyageur.config import ServiceSettings
    d = ServiceSettings.from_env({})
    assert (d.token, d.retention_days, d.run_token_budget, d.candidate_cap, d.max_calls) == (None, 14, None, 30, 2)
    s = ServiceSettings.from_env({"HEAL_SERVICE_TOKEN": " t ", "HEAL_RETENTION_DAYS": "0", "HEAL_RUN_TOKEN_BUDGET": "500",
                                  "HEAL_CANDIDATE_CAP": "10", "HEAL_MAX_CALLS": "3", "HEAL_DB": "x.db",
                                  "HEAL_MODEL_PRICES": '{"m": {"input": 1, "output": 2.5}}'})
    assert (s.token, s.retention_days, s.run_token_budget, s.candidate_cap, s.max_calls, s.db_path) == \
        ("t", 0, 500, 10, 3, "x.db")
    assert s.prices == {"m": (1.0, 2.5)}


@pytest.mark.parametrize("var, bad", [("HEAL_RETENTION_DAYS", "-1"), ("HEAL_RUN_TOKEN_BUDGET", "0"),
                                      ("HEAL_MAX_CALLS", "x"), ("HEAL_MODEL_PRICES", "[1]"),
                                      ("HEAL_MODEL_PRICES", '{"m": {"input": "a", "output": 1}}')])
def test_service_settings_name_the_variable_not_the_value(var, bad):
    from voyageur.config import ConfigError, ServiceSettings
    with pytest.raises(ConfigError) as e:
        ServiceSettings.from_env({var: bad})
    assert var in str(e.value) and bad not in str(e.value)
