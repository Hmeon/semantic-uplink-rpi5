from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

from collector import analyze as analyze_mod


def _write_minimal_events_csv(path: Path) -> None:
    base_ts = 1_700_200_000_000_000_000
    rows: list[dict[str, object]] = []
    seq = 1
    for policy, tau in [("periodic", 1.0), ("fixed_tau", 2.0), ("adaptive", 1.5)]:
        for i in range(2):
            ts = base_ts + (seq - 1) * 1_000_000_000
            rows.append(
                {
                    "device_id": "dev-1",
                    "sensor": "temp",
                    "profile": "slow_10kbps",
                    "policy": policy,
                    "seq": seq,
                    "ts": ts,
                    "t_recv_ns": ts + 120_000_000,
                    "val": 20.0 + i,
                    "pred": 19.8 + i,
                    "res": 0.2,
                    "tau": tau,
                    "kbits": 8,
                    "mqtt_bytes": 120,
                }
            )
            seq += 1
    pd.DataFrame(rows).to_csv(path, index=False)


def _build_rich_events_df() -> pd.DataFrame:
    base_ts = 1_700_300_000_000_000_000
    rows: list[dict[str, object]] = []
    seq = 1

    def _push(policy: str, tau: float, kbits: int, res: float, reason: str, i: int) -> None:
        nonlocal seq
        ts = base_ts + (seq - 1) * 1_000_000_000
        rows.append(
            {
                "device_id": "dev-1",
                "sensor": "temp",
                "profile": "slow_10kbps",
                "policy": policy,
                "seq": seq,
                "ts": ts,
                "t_recv_ns": ts + 120_000_000 + i * 5_000_000,
                "val": 20.0 + i,
                "pred": 19.7 + i,
                "res": res,
                "tau": tau,
                "kbits": kbits,
                "mqtt_bytes": 128,
                "event_reason": reason,
            }
        )
        seq += 1

    for i in range(2):
        _push("periodic", 1.0, 8, 0.40 + 0.05 * i, "HEARTBEAT", i)
    for i in range(2):
        _push("fixed_tau", 2.0, 8, 0.30 + 0.04 * i, "THRESHOLD", i)
    adaptive = [
        (1.5, 8, 0.10, "THRESHOLD"),
        (2.0, 10, 0.18, "HEARTBEAT"),
        (1.5, 8, 0.26, "THRESHOLD"),
        (2.0, 10, 0.34, "HEARTBEAT"),
        (1.5, 8, 0.42, "THRESHOLD"),
        (2.0, 10, 0.50, "HEARTBEAT"),
    ]
    for i, (tau, kbits, res, reason) in enumerate(adaptive, start=4):
        _push("adaptive", tau, kbits, res, reason, i)

    return pd.DataFrame(rows)


def _write_decisions_matching_adaptive_events(path: Path, *, include_ucb: bool) -> None:
    ev = _build_rich_events_df()
    ev = ev[ev["policy"] == "adaptive"].reset_index(drop=True)
    rows: list[dict[str, object]] = []
    forced_reasons = ["NONE", "AOI_LIMIT", "MAE_LIMIT", "BOTH", "NONE", "AOI_LIMIT"]
    for i, r in ev.iterrows():
        row: dict[str, object] = {
            "ts": int(r["ts"]),
            "t_recv_ns": int(r["t_recv_ns"]) + 3_000_000,
            "device_id": str(r["device_id"]),
            "state_aoi": 900.0 + 180.0 * i,
            "state_res": float(r["res"]),
            "state_res_var": 0.01 * (i + 1),
            "state_loss": 0.02 * (i % 3),
            "state_q_len": int(i % 4),
            "tau": float(r["tau"]),
            "kbits": int(r["kbits"]),
            "reward": 1.0 - 0.06 * i,
            "arm_id": int(i % 2),
            "safe_arm_forced": bool(i % 3 == 0),
            "forced_reason": forced_reasons[i],
            "reward_aoi": -0.15 * (i + 1),
            "reward_mae": -0.10 * (i + 1),
            "reward_rate": -0.05 * (i + 1),
            "rate_limit_skips": int(i % 2),
        }
        if include_ucb:
            row.update(
                {
                    "ucb_exploitation": 0.6 + 0.08 * i,
                    "ucb_exploration": 0.2 + 0.03 * i,
                    "ucb_score": 0.8 + 0.11 * i,
                    "ucb_alpha": 0.5,
                }
            )
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_analyze_main_writes_core_artifact_contract(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runA" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    out_dir = tmp_path / "analysis_out"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )

    analyze_mod.main()

    required = [
        "analysis_meta.json",
        "metrics_summary.csv",
        "metrics_by_run.csv",
        "metrics_vs_periodic.csv",
        "metrics_vs_fixed_tau.csv",
        "kpi_final.csv",
        "kpi_verdict.json",
        "report.md",
    ]
    for name in required:
        assert (out_dir / name).exists(), f"missing artifact: {name}"

    assert not (out_dir / "plot_manifest.json").exists()

    summary = pd.read_csv(out_dir / "metrics_summary.csv")
    assert {"profile", "policy", "sensor"}.issubset(summary.columns)
    assert {"periodic", "fixed_tau", "adaptive"}.issubset(
        set(summary["policy"].astype(str).tolist())
    )

    meta = json.loads((out_dir / "analysis_meta.json").read_text(encoding="utf-8"))
    flags = meta.get("flags", {})
    assert flags.get("plots") is False
    assert flags.get("paper_plots") is False
    assert flags.get("diagnostic_plots") is False

    verdict = json.loads((out_dir / "kpi_verdict.json").read_text(encoding="utf-8"))
    assert verdict.get("project_verdict") in {"PASS", "FAIL", "SKIP"}


def test_analyze_main_with_plots_writes_manifest_and_core_figure_contract(
    tmp_path,
    monkeypatch,
) -> None:
    run_logs = tmp_path / "artifacts" / "runB" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    out_dir = tmp_path / "analysis_plots"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--plot-formats",
            "png",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    figs_dir = out_dir / "figs"
    assert figs_dir.exists()
    pngs = sorted(figs_dir.glob("*.png"))
    assert pngs, "expected at least one generated png figure"

    expected_core = "temp_slow_10kbps_compare_rate_bar.png"
    assert (figs_dir / expected_core).exists()

    manifest_path = out_dir / "plot_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["plot_cfg"]["dir_name"] == "figs"
    assert manifest["plot_cfg"]["formats"] == ["png"]
    figs = manifest.get("figures", [])
    assert isinstance(figs, list)
    assert figs

    core_base = expected_core.removesuffix(".png")
    core = [f for f in figs if str(f.get("base_name")) == core_base]
    assert core, "expected core figure entry in plot_manifest"
    entry = core[0]
    assert "files" in entry and expected_core in entry["files"]
    assert "axes" in entry and isinstance(entry["axes"], list)
    assert entry["axes"]
    assert all("xlabel" in ax and "ylabel" in ax for ax in entry["axes"])


def test_analyze_main_with_audit_writes_audit_artifacts_and_summary_consistency(
    tmp_path,
    monkeypatch,
) -> None:
    run_logs = tmp_path / "artifacts" / "runC" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    out_dir = tmp_path / "analysis_audit"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    qj = out_dir / "quality_audit.json"
    qm = out_dir / "quality_audit.md"
    assert qj.exists()
    assert qm.exists()

    report = json.loads(qj.read_text(encoding="utf-8"))
    md = qm.read_text(encoding="utf-8")
    assert "# Quality audit report" in md
    assert "## Summary" in md
    assert "## Table metric coverage (NaN/inf)" in md

    m_vis = re.search(
        r"- Visualization \(expected\): PASS (\d+) / FAIL (\d+) / SKIP (\d+)",
        md,
    )
    assert m_vis, "missing visualization summary line in markdown"
    md_pass, md_fail, md_skip = [int(x) for x in m_vis.groups()]
    js_counts = report.get("visualization", {}).get("expected_status_counts", {})
    assert md_pass == int(js_counts.get("PASS", 0))
    assert md_fail == int(js_counts.get("FAIL", 0))
    assert md_skip == int(js_counts.get("SKIP", 0))

    m_log = re.search(r"- Logging: PASS (\d+) / FAIL (\d+)", md)
    assert m_log, "missing logging summary line in markdown"
    md_log_pass, md_log_fail = [int(x) for x in m_log.groups()]
    js_log = report.get("logging", {}).get("status_counts", {})
    assert md_log_pass == int(js_log.get("PASS", 0))
    assert md_log_fail == int(js_log.get("FAIL", 0))


def test_analyze_main_with_diagnostic_plots_contract(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runD" / "logs"
    run_logs.mkdir(parents=True)
    _build_rich_events_df().to_csv(run_logs / "events.csv", index=False)
    _write_decisions_matching_adaptive_events(run_logs / "decisions.csv", include_ucb=True)

    out_dir = tmp_path / "analysis_diag"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--plots",
            "--no-paper-plots",
            "--diagnostic-plots",
            "--ucb-timeseries",
            "--plot-formats",
            "png",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    figs_dir = out_dir / "figs"
    assert figs_dir.exists()
    assert list(figs_dir.glob("*.png"))
    assert list(figs_dir.glob("*_adaptive_arm_dist__*.png"))
    assert list(figs_dir.glob("*_adaptive_entropy_60s__*.png"))
    assert list(figs_dir.glob("*_adaptive_switch_rate.png"))
    assert list(figs_dir.glob("*_adaptive_ucb_terms_ts__*.png"))

    manifest = json.loads((out_dir / "plot_manifest.json").read_text(encoding="utf-8"))
    figures = manifest.get("figures", [])
    assert figures
    base_names = {str(f.get("base_name", "")) for f in figures}
    assert any("_adaptive_arm_dist__" in b for b in base_names)
    assert any("_adaptive_entropy_60s__" in b for b in base_names)
    assert any(b.endswith("_adaptive_switch_rate") for b in base_names)
    assert any("_adaptive_ucb_terms_ts__" in b for b in base_names)


def test_analyze_main_with_paper_plots_contract_and_report_links(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runE" / "logs"
    run_logs.mkdir(parents=True)
    _build_rich_events_df().to_csv(run_logs / "events.csv", index=False)
    _write_decisions_matching_adaptive_events(run_logs / "decisions.csv", include_ucb=False)

    out_dir = tmp_path / "analysis_paper"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--plots",
            "--paper-plots",
            "--no-diagnostic-plots",
            "--plot-formats",
            "png",
            "--reward-window",
            "4",
            "--action-bins",
            "4",
            "--top-actions",
            "6",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    figs_dir = out_dir / "figs"
    assert figs_dir.exists()

    env_metrics = "temp_all_compare_env_metrics_panel.png"
    env_reward = "temp_all_adaptive_reward_by_profile_ts.png"
    assert (figs_dir / env_metrics).exists()
    assert (figs_dir / env_reward).exists()
    assert list(figs_dir.glob("*_adaptive_action_heatmap.png"))
    assert list(figs_dir.glob("*_adaptive_feature_weights__*.png"))
    assert list(figs_dir.glob("*_adaptive_reward_ts__*.png"))
    assert list(figs_dir.glob("*_adaptive_cumulative_regret__*.png"))

    report_md = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "## Paper Figures (논문용 추가 플롯)" in report_md
    assert f"![](figs/{env_metrics})" in report_md
    assert f"![](figs/{env_reward})" in report_md
    run_specific = sorted(figs_dir.glob("*_adaptive_feature_weights__*.png"))
    assert run_specific
    assert f"![](figs/{run_specific[0].name})" in report_md


def test_analyze_main_with_all_optional_plot_branches_contract(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runF" / "logs"
    run_logs.mkdir(parents=True)
    _build_rich_events_df().to_csv(run_logs / "events.csv", index=False)
    _write_decisions_matching_adaptive_events(run_logs / "decisions.csv", include_ucb=True)

    out_dir = tmp_path / "analysis_all_plots"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--plots",
            "--paper-plots",
            "--diagnostic-plots",
            "--ucb-timeseries",
            "--plot-formats",
            "png",
            "--reward-window",
            "4",
            "--action-bins",
            "4",
            "--top-actions",
            "6",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    figs_dir = out_dir / "figs"
    assert figs_dir.exists()
    assert list(figs_dir.glob("*.png"))

    # Core/standard + paper + diagnostic branches are all expected in one run.
    assert (figs_dir / "temp_slow_10kbps_compare_rate_bar.png").exists()
    assert (figs_dir / "temp_all_compare_env_metrics_panel.png").exists()
    assert (figs_dir / "temp_all_adaptive_reward_by_profile_ts.png").exists()
    assert list(figs_dir.glob("*_adaptive_feature_weights__*.png"))
    assert list(figs_dir.glob("*_adaptive_arm_dist__*.png"))
    assert list(figs_dir.glob("*_adaptive_entropy_60s__*.png"))
    assert list(figs_dir.glob("*_adaptive_switch_rate.png"))
    assert list(figs_dir.glob("*_adaptive_ucb_terms_ts__*.png"))

    report_md = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "## Paper Figures" in report_md
    assert "![](figs/temp_all_compare_env_metrics_panel.png)" in report_md
    assert "![](figs/temp_all_adaptive_reward_by_profile_ts.png)" in report_md

    manifest = json.loads((out_dir / "plot_manifest.json").read_text(encoding="utf-8"))
    figures = manifest.get("figures", [])
    assert figures
    base_names = {str(f.get("base_name", "")) for f in figures}
    assert "temp_slow_10kbps_compare_rate_bar" in base_names
    assert "temp_all_compare_env_metrics_panel" in base_names
    assert any("_adaptive_feature_weights__" in b for b in base_names)
    assert any("_adaptive_arm_dist__" in b for b in base_names)
    assert any("_adaptive_ucb_terms_ts__" in b for b in base_names)


def test_analyze_main_with_nondefault_baseline_writes_all_comparison_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    run_logs = tmp_path / "artifacts" / "runG" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    out_dir = tmp_path / "analysis_baseline_adaptive"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--baseline-policy",
            "adaptive",
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    cmp_adaptive = out_dir / "metrics_vs_adaptive.csv"
    cmp_periodic = out_dir / "metrics_vs_periodic.csv"
    cmp_fixed = out_dir / "metrics_vs_fixed_tau.csv"
    assert cmp_adaptive.exists()
    assert cmp_periodic.exists()
    assert cmp_fixed.exists()

    for path in [cmp_adaptive, cmp_periodic, cmp_fixed]:
        df_cmp = pd.read_csv(path)
        assert not df_cmp.empty
        assert {"profile", "policy", "sensor"}.issubset(df_cmp.columns)

    meta = json.loads((out_dir / "analysis_meta.json").read_text(encoding="utf-8"))
    assert meta.get("baseline_policy") == "adaptive"


def test_analyze_main_discord_notification_with_mentions(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runH" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    sent: dict[str, object] = {}

    def _fake_send_discord_message(
        webhook_url: str,
        message: str,
        *,
        username: str | None = None,
        allowed_mentions: dict[str, object] | None = None,
    ) -> None:
        sent["webhook_url"] = webhook_url
        sent["message"] = message
        sent["username"] = username
        sent["allowed_mentions"] = allowed_mentions

    monkeypatch.setattr(analyze_mod, "send_discord_message", _fake_send_discord_message)

    out_dir = tmp_path / "analysis_discord"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--discord-webhook",
            "https://example.invalid/webhook",
            "--discord-username",
            "semantic-uplink-bot",
            "--discord-mention",
            "12345",
            "--discord-mention",
            "67890",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert sent.get("webhook_url") == "https://example.invalid/webhook"
    assert sent.get("username") == "semantic-uplink-bot"
    assert sent.get("allowed_mentions") == {"parse": [], "users": ["12345", "67890"]}
    msg = str(sent.get("message", ""))
    assert msg.startswith("<@12345> <@67890>\n")
    assert "**Semantic Uplink 분석 요약**" in msg


def test_analyze_main_handles_decisions_load_failure(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runI" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    def _raise_load_decisions(_paths):
        raise RuntimeError("simulated decisions load failure")

    monkeypatch.setattr(analyze_mod, "load_decisions", _raise_load_decisions)

    out_dir = tmp_path / "analysis_decisions_fail"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "metrics_by_run.csv").exists()
    assert (out_dir / "kpi_verdict.json").exists()
    assert not (out_dir / "linucb_arm_distribution.csv").exists()
    assert not (out_dir / "linucb_entropy_60s.csv").exists()


def test_analyze_main_handles_fallback_compare_failures(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runJ" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    original_compare = analyze_mod.compare_policies

    def _fake_compare(summary: pd.DataFrame, *, baseline_policy: str = "periodic") -> pd.DataFrame:
        if baseline_policy in {"periodic", "fixed_tau"}:
            raise RuntimeError(f"simulated fallback failure: {baseline_policy}")
        return original_compare(summary, baseline_policy=baseline_policy)

    monkeypatch.setattr(analyze_mod, "compare_policies", _fake_compare)

    out_dir = tmp_path / "analysis_compare_fallback_fail"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--baseline-policy",
            "adaptive",
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    # Primary baseline comparison is still produced.
    assert (out_dir / "metrics_vs_adaptive.csv").exists()
    # Fallback compare files may be skipped when fallback generation fails.
    assert not (out_dir / "metrics_vs_periodic.csv").exists()
    assert not (out_dir / "metrics_vs_fixed_tau.csv").exists()
    # Main analysis artifacts should still be emitted.
    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "kpi_verdict.json").exists()


def test_analyze_main_handles_discord_webhook_error(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runK" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    def _raise_discord_error(*_args, **_kwargs):
        raise analyze_mod.DiscordWebhookError("simulated discord failure")

    monkeypatch.setattr(analyze_mod, "send_discord_message", _raise_discord_error)

    out_dir = tmp_path / "analysis_discord_error"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--discord-webhook",
            "https://example.invalid/webhook",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "metrics_by_run.csv").exists()
    assert (out_dir / "kpi_verdict.json").exists()


def test_analyze_main_handles_quality_metrics_failure(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runL" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    def _raise_quality_metrics(*_args, **_kwargs):
        raise RuntimeError("simulated quality metrics failure")

    monkeypatch.setattr(
        analyze_mod,
        "compute_seq_aligned_quality_metrics",
        _raise_quality_metrics,
    )

    out_dir = tmp_path / "analysis_quality_metrics_error"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    by_run = pd.read_csv(out_dir / "metrics_by_run.csv")
    assert "recon_mae_mean" not in by_run.columns
    assert "anomaly_segment_recall" not in by_run.columns
    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "kpi_verdict.json").exists()


def test_analyze_main_handles_plot_manifest_write_failure(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runM" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    def _raise_manifest_write(*_args, **_kwargs):
        raise RuntimeError("simulated manifest write failure")

    monkeypatch.setattr(analyze_mod, "_write_plot_manifest_impl", _raise_manifest_write)

    out_dir = tmp_path / "analysis_manifest_error"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--plot-formats",
            "png",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "report.md").exists()
    assert (out_dir / "figs").exists()
    assert list((out_dir / "figs").glob("*.png"))
    assert not (out_dir / "plot_manifest.json").exists()


def test_analyze_main_handles_quality_audit_write_failure(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runN" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    import collector.quality_audit as quality_audit_mod

    def _raise_audit_write(*_args, **_kwargs):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(quality_audit_mod, "write_quality_audit_files", _raise_audit_write)

    out_dir = tmp_path / "analysis_audit_write_error"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "kpi_verdict.json").exists()
    assert not (out_dir / "quality_audit.json").exists()
    assert not (out_dir / "quality_audit.md").exists()


def test_analyze_main_handles_analysis_meta_write_failure(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runO" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    original_write_text = Path.write_text

    def _patched_write_text(self: Path, data: str, *args, **kwargs):
        if self.name == "analysis_meta.json":
            raise RuntimeError("simulated analysis_meta write failure")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _patched_write_text)

    out_dir = tmp_path / "analysis_meta_write_error"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert not (out_dir / "analysis_meta.json").exists()
    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "metrics_by_run.csv").exists()
    assert (out_dir / "kpi_verdict.json").exists()
    assert (out_dir / "report.md").exists()


def test_analyze_main_handles_kpi_verdict_write_failure(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runP" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    original_write_text = Path.write_text

    def _patched_write_text(self: Path, data: str, *args, **kwargs):
        if self.name == "kpi_verdict.json":
            raise RuntimeError("simulated kpi verdict write failure")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _patched_write_text)

    out_dir = tmp_path / "analysis_kpi_write_error"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert (out_dir / "metrics_summary.csv").exists()
    assert (out_dir / "kpi_final.csv").exists()
    assert not (out_dir / "kpi_verdict.json").exists()
    assert (out_dir / "report.md").exists()


def test_analyze_main_handles_save_parquet_failure(tmp_path, monkeypatch) -> None:
    run_logs = tmp_path / "artifacts" / "runQ" / "logs"
    run_logs.mkdir(parents=True)
    _write_minimal_events_csv(run_logs / "events.csv")

    original_to_parquet = pd.DataFrame.to_parquet

    def _patched_to_parquet(self: pd.DataFrame, path, *args, **kwargs):
        if str(path).endswith("metrics_summary.parquet"):
            raise RuntimeError("simulated parquet write failure")
        return original_to_parquet(self, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", _patched_to_parquet)

    out_dir = tmp_path / "analysis_parquet_error"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(tmp_path / "artifacts"),
            "--out",
            str(out_dir),
            "--save-parquet",
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze_mod.main()

    assert (out_dir / "metrics_summary.csv").exists()
    assert not (out_dir / "metrics_summary.parquet").exists()
    assert (out_dir / "kpi_verdict.json").exists()
