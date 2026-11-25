"""HEAL_LIBRARY_CLASSES: component-library class names as locators, opt in. Off (the default) nothing changes."""
import pytest

from voyageur.config import ConfigError, ServiceSettings
from voyageur.inventory import Inventory
from voyageur.locator_policy import (LIBRARY_PREFIXES, STABILITY_ORDER, ElementFacts, candidates, facts_of, library_prefixes, usable_class,
                                         write)

pytestmark = pytest.mark.unit


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("HEAL_LIBRARY_CLASSES", "Mui,ant-,mat-,chakra-,Mantine")


def facts(cls, **kw):
    return facts_of({"tag": "div", "class": cls, **kw})


def css(f):
    return [c for c in candidates(f, "python", order=STABILITY_ORDER) if ".locator(" in c]


def test_off_by_default_a_library_class_is_not_a_locator():
    assert library_prefixes() == () and css(facts("MuiSelect-select MuiSelect-outlined")) == []


@pytest.mark.parametrize("cls,want", [
    ("MuiSelect-select", 'page.locator("div.MuiSelect-select")'), ("ant-select-item", 'page.locator("div.ant-select-item")'),
    ("mat-menu-item", 'page.locator("div.mat-menu-item")'), ("chakra-select", 'page.locator("div.chakra-select")'),
    ("MantineSelect-input", 'page.locator("div.MantineSelect-input")'), ("mantine-Select-input", 'page.locator("div.mantine-Select-input")')])
def test_a_listed_library_class_becomes_a_css_candidate(on, cls, want):
    assert css(facts(cls)) == [want]


@pytest.mark.parametrize("cls", ["css-1x2y3z", "css-1x2y3z4", "MuiBox-root-123", "jss123", "jss12-root", "MuiSelect-select-a1b2c3d4",
                                 "MuiSelect-select-123456", "sc-AxjAm", "Mui-focused", "Mui-selected", "MuiButton-root-Mui-disabled",
                                 "ant-select-open", "ant-select-dropdown-hidden", "ant-tabs-tab-active", "mat-focused", "ant-1x2y3z4w",
                                 "MuiSelect-select2024", "mat-x-12"])
def test_hashed_suffixed_and_state_variants_are_refused_even_when_on(on, cls):
    assert css(facts(cls)) == [] and not usable_class(cls, library_prefixes())


def test_only_the_listed_prefixes_open_the_generated_class_door(monkeypatch):
    monkeypatch.setenv("HEAL_LIBRARY_CLASSES", "ant")
    assert css(facts("MuiSelect-select")) == [] and css(facts("ant-select-item"))      # Mui is generated and not listed
    monkeypatch.setenv("HEAL_LIBRARY_CLASSES", " MUI-, Mat ")
    assert library_prefixes() == ("mui", "mat") and css(facts("MuiSelect-select")) and css(facts("mat-menu-item"))


def test_listing_a_library_also_turns_its_state_classes_away_that_off_would_have_written(monkeypatch):
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False)
    assert css(facts("ant-select-open"))                      # today's behaviour, unchanged
    monkeypatch.setenv("HEAL_LIBRARY_CLASSES", "ant")
    assert css(facts("ant-select-open")) == []


def test_css_in_js_classes_are_never_written_on_or_off(monkeypatch):
    for setting in (None, "Mui,ant"):
        (monkeypatch.setenv("HEAL_LIBRARY_CLASSES", setting) if setting else monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False))
        for cls in ("css-1x2y3z", "jss123", "sc-AxjAm"):
            assert css(facts(cls)) == [], (setting, cls)


def test_a_prefix_must_start_the_class_and_end_at_a_word_boundary(on):
    for cls in ("Muiltiple", "Mui", "Muiltiple-x"):                 # generated-looking, but not `Mui` + a component name
        assert css(facts(cls)) == [], cls
    assert usable_class("mantis-x", library_prefixes()) and usable_class("antelope", library_prefixes())    # plain classes: today's rule


def test_an_all_caps_or_acronym_class_is_not_read_as_a_library_prefix(on):
    from voyageur.locator_policy import is_library_class
    lib = library_prefixes()
    for cls in ("MATRIX-row", "ANTENNA", "MUIX"):                    # capitals after the prefix, but no component name
        assert not is_library_class(cls, lib), cls
    for cls in ("MuiSelect-select", "MuiIconButton-root", "ant-select-item"):
        assert is_library_class(cls, lib), cls


def test_unknown_prefixes_are_a_startup_error_and_a_known_set_is_documented():
    with pytest.raises(ConfigError, match="HEAL_LIBRARY_CLASSES"):
        ServiceSettings.from_env({"HEAL_LIBRARY_CLASSES": "Mui,Bootstrap"})
    assert ServiceSettings.from_env({"HEAL_LIBRARY_CLASSES": "Mui, ant-"}).library_classes == ("mui", "ant")
    assert ServiceSettings.from_env({}).library_classes == () and ServiceSettings.from_env({"HEAL_LIBRARY_CLASSES": " "}).library_classes == ()
    assert LIBRARY_PREFIXES == ("mui", "ant", "chakra", "mantine", "mat")


def test_a_library_class_ranks_below_data_hooks_and_ids_and_above_copy(on):
    f = ElementFacts(tag="div", id="pick", test_id="t", data_attrs=(("data-menu", "m"),), classes=("MuiSelect-select",),
                     role="button", accessible_name="Pick", text="Pick")
    out = candidates(f, "python", order=STABILITY_ORDER)
    order = [i for i, c in enumerate(out)]
    named = {c: i for i, c in enumerate(out)}
    assert named['page.get_by_test_id("t")'] < named['page.locator("#pick")'] < named['page.locator("div[data-menu=\'m\']")'] \
        < named['page.locator("div.MuiSelect-select")'] < named['page.get_by_role("button", name="Pick", exact=True)'] \
        < named['page.get_by_text("Pick", exact=True)'] and order


def inventory(*els):
    return Inventory([{"ref": f"e{i}", **e} for i, e in enumerate(els)], True)


def btn(**kw):
    return {"tag": "button", "class": "MuiButton-root", "box": {"x": 0, "y": 0, "width": 10, "height": 10}, **kw}


def test_uniqueness_is_still_proved_two_elements_with_the_class_give_no_candidate(on):
    inv = inventory(btn(), btn(box={"x": 0, "y": 20, "width": 10, "height": 10}))
    assert write(inv.elements[0], inv, "python", "self.page", "css", broken_call="self.page.locator('.old')").candidates == []
    alone = inventory(btn())
    got = write(alone.elements[0], alone, "python", "self.page", "css", broken_call="self.page.locator('.old')").candidates
    assert [w.code["python"] for w in got] == ['self.page.locator("button.MuiButton-root")']


def test_a_class_on_a_tag_the_probe_does_not_list_stays_unproven_so_it_is_not_written(on):
    """The proof is unchanged: the listing does not cover every div, so `div.MuiSelect-select` cannot be shown unique."""
    inv = inventory({"tag": "div", "class": "MuiSelect-select", "role": "combobox", "box": {"x": 0, "y": 0, "width": 10, "height": 10}})
    assert not [w for w in write(inv.elements[0], inv, "python", "self.page", "css", broken_call="self.page.locator('.old')").candidates
                if "MuiSelect" in w.code["python"]]


def test_off_the_same_unique_element_gets_no_library_candidate(monkeypatch):
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False)
    alone = inventory(btn())
    assert write(alone.elements[0], alone, "python", "self.page", "css", broken_call="self.page.locator('.old')").candidates == []


def test_the_policy_reads_the_setting_in_a_worker_process_too(monkeypatch):
    """Workers are spawned with the service's environment: the policy reads the variable itself, not a global set in the parent."""
    import multiprocessing
    monkeypatch.setenv("HEAL_LIBRARY_CLASSES", "Mui")
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        assert pool.apply(library_prefixes) == ("mui",)
