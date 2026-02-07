from __future__ import annotations

import subprocess

import pytest

from link.shaper import tc_profiles as tc


def test_apply_and_clear_profile_issue_expected_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    run_cmds: list[str] = []
    ignore_cmds: list[str] = []

    monkeypatch.setattr(tc, "_require_root", lambda: None)

    def _fake_run(cmd: str, check: bool = True):
        _ = check
        run_cmds.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tc, "_run", _fake_run)
    monkeypatch.setattr(tc, "_ignore", lambda cmd: ignore_cmds.append(cmd))

    tc.apply_profile("eth0", "slow_10kbps", both=False)
    assert any("tc qdisc replace dev eth0 root handle 1: htb default 1" in c for c in run_cmds)
    assert any(
        "tc class replace dev eth0 parent 1: classid 1:1 htb rate 10kbit" in c
        for c in run_cmds
    )

    tc.clear("eth0", both=False)
    assert "tc qdisc del dev eth0 root" in ignore_cmds


def test_cellular_var_replaces_existing_toggle_and_uses_ifb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tc, "_require_root", lambda: None)
    monkeypatch.setattr(
        tc,
        "_run",
        lambda cmd, check=True: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(tc, "_ignore", lambda cmd: None)

    class _OldToggle:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self, timeout: float = 2.0) -> None:
            _ = timeout
            self.stopped = True

    old = _OldToggle()
    tc._toggle_registry["eth0"] = old

    class _NewToggle:
        def __init__(self, iface, ifb, low_kbit, high_kbit, period_s):
            self.iface = iface
            self.ifb = ifb
            self.low_kbit = low_kbit
            self.high_kbit = high_kbit
            self.period_s = period_s
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self, timeout: float = 2.0):
            _ = timeout
            self.stopped = True

    monkeypatch.setattr(tc, "_RateToggle", _NewToggle)

    tc.apply_profile("eth0", "cellular_var", both=True, var_period_s=7)
    assert old.stopped is True
    assert "eth0" in tc._toggle_registry
    new = tc._toggle_registry["eth0"]
    assert isinstance(new, _NewToggle)
    assert new.started is True
    assert new.ifb == "ifb0"
    assert new.period_s == 7

    tc.clear("eth0", both=True)
    assert new.stopped is True
    assert "eth0" not in tc._toggle_registry


def test_status_includes_both_interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "_require_root", lambda: None)

    def _fake_run(cmd: str, check: bool = True):
        _ = check
        if "qdisc show dev ifb0" in cmd:
            out = "qdisc netem 10:"
        elif "class show dev ifb0" in cmd:
            out = "class htb 1:1"
        elif "qdisc show dev eth0" in cmd:
            out = "qdisc htb 1:"
        else:
            out = "class htb 1:1"
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(tc, "_run", _fake_run)
    out = tc.status("eth0", both=True)
    assert "# eth0 qdisc:" in out
    assert "# ifb0 qdisc:" in out
