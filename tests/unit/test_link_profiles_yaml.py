from __future__ import annotations

from link.shaper.tc_profiles import PROFILES, load_profiles_config


def test_link_profiles_yaml_matches_builtin_tc_profiles() -> None:
    loaded = load_profiles_config("configs/link_profiles.yaml")
    assert loaded == PROFILES

