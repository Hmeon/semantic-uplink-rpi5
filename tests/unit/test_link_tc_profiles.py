from __future__ import annotations

from common.schema import LinkProfile
from link.shaper.tc_profiles import PROFILES, _build_netem_args


def test_link_profile_enum_matches_tc_profiles() -> None:
    enum_values = {p.value for p in LinkProfile}
    assert enum_values == set(PROFILES.keys())


def test_tc_profiles_netem_loss_correlation_format() -> None:
    for name, prof in PROFILES.items():
        args = _build_netem_args(prof)
        tokens = args.split()
        if prof.loss_pct <= 0:
            assert "loss" not in tokens, f"{name}: unexpected loss args: {args!r}"
            continue

        assert "loss" in tokens, f"{name}: loss missing in args: {args!r}"
        i = tokens.index("loss")
        assert tokens[i + 1] == "random"
        assert tokens[i + 2] == f"{prof.loss_pct}%"

        if prof.loss_corr_pct > 0:
            assert tokens[i + 3] == f"{prof.loss_corr_pct}%"
        else:
            assert f"{prof.loss_corr_pct}%" not in tokens

