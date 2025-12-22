# Full repository audit

Snapshot note: this audit is a point-in-time report. Regenerate after major changes.


- Generated: 2025-12-19T18:06:20Z
- Scope: all files in repo excluding .git and .venv* directories

## Findings by category
### Critical
- None

### Major
- RPi5 profiling not executed on actual RPi5 hardware in this audit run (needs on-device validation).

### Minor
- Matplotlib boxplot deprecation warnings addressed by switching to `tick_labels` (no output change).
- Editable install failed due to package discovery; fixed by explicit package list in `pyproject.toml`.
- RPi5 install guidance aligned to `pip install -e .[analysis,hw]` and hw extras now include gpiozero/rpi-lgpio.

## Issues (Symptom -> Root cause -> Fix -> Verification)
- Editable install failed in clean venv -> setuptools auto-discovery found multiple top-level dirs -> added explicit `tool.setuptools.packages.find` -> `pip install -e .[dev,analysis]` succeeded.
- Matplotlib deprecation warning on boxplot labels -> old `labels` kwarg -> updated to `tick_labels` -> reran `collector.analyze` with no warnings.
- RPi5 profiling missing -> no hardware run in this audit -> document runbook + require on-device run -> `scripts/bench_policy_rpi5.py` output recorded from host.
- RPi5 install docs outdated -> requirements vs extras mismatch -> updated README/CODEX/AGENTS and hw extras -> verify by inspecting docs and `pyproject.toml`.

## Per-file status table
| path | category | status | notes |
|---|---|---|---|
| .coverage | root | Not in scope | generated/vendor/IDE output |
| .editorconfig | root | OK |  |
| .github/workflows/ci.yaml | github | OK |  |
| .gitignore | root | OK |  |
| .idea/.gitignore | idea | Not in scope | generated/vendor/IDE output |
| .idea/.name | idea | Not in scope | generated/vendor/IDE output |
| .idea/inspectionProfiles/profiles_settings.xml | idea | Not in scope | generated/vendor/IDE output |
| .idea/misc.xml | idea | Not in scope | generated/vendor/IDE output |
| .idea/modules.xml | idea | Not in scope | generated/vendor/IDE output |
| .idea/semantic-uplink-rpi5.iml | idea | Not in scope | generated/vendor/IDE output |
| .idea/vcs.xml | idea | Not in scope | generated/vendor/IDE output |
| .idea/workspace.xml | idea | Not in scope | generated/vendor/IDE output |
| .pytest_cache/.gitignore | pytest_cache | Not in scope | generated/vendor/IDE output |
| .pytest_cache/CACHEDIR.TAG | pytest_cache | Not in scope | generated/vendor/IDE output |
| .pytest_cache/README.md | pytest_cache | Not in scope | generated/vendor/IDE output |
| .pytest_cache/v/cache/lastfailed | pytest_cache | Not in scope | generated/vendor/IDE output |
| .pytest_cache/v/cache/nodeids | pytest_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/.gitignore | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/11033899190823185755 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/12720307578391960855 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/13595224921119779309 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/18173274452956607883 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/2207411515969817159 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/6131490484399707841 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/6734513616583824091 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/6999182288134776379 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.10/747888083482340643 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.3/10988071422865984401 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.3/11219249909515907901 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.3/15047104421146399640 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.3/7537649873677580791 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/10477258591963850367 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/12201965341562701135 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/12563142708624582228 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/1321775990770992766 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/13774457974143860388 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/14669637766956608660 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/16008113608452351003 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/16724861659226203683 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/16925549037069179870 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/17288477847593501098 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/2216103037376203596 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/2528339162653163846 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/2776171403484221183 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/3394974867740454150 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/3642093950702752514 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/5473579573377965140 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/6570321008349593332 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/696678038516913544 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/7318709297487501737 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/8377909150803271899 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/8656555691382154346 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/9076834586455146326 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/9198408401671849653 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/9237603264484629817 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/9687747223726958963 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/0.14.9/9977173625118883936 | ruff_cache | Not in scope | generated/vendor/IDE output |
| .ruff_cache/CACHEDIR.TAG | ruff_cache | Not in scope | generated/vendor/IDE output |
| AGENTS.md | root | OK |  |
| CODEX.md | root | OK |  |
| PATCH_NOTES.md | root | OK |  |
| README.md | root | OK |  |
| artifacts/analysis_current/figs/paper_action_heatmap__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_action_heatmap__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_cumulative_regret__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_cumulative_regret__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_env_metrics__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_env_metrics__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_env_reward_over_time__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_env_reward_over_time__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_feature_weights__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_feature_weights__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_reward_over_time__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_reward_over_time__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_stability_abs_res__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_stability_abs_res__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_timeline__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/paper_timeline__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_current/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/plot_manifest.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_final/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/plot_manifest.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_labelsfix/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/analysis_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/plot_manifest.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_new/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix2/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotfix3/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/plot_manifest.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_plotmanifest/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/analysis_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_cumulative_regret__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_feature_weights__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_reward_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_timeline__testpaper.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_adaptive_timeline__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/plot_manifest.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_run/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/analysis_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/all_slow_10kbps_adaptive_outbox_pending_ts__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/all_slow_10kbps_adaptive_outbox_pending_ts__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/all_slow_10kbps_compare_dup_bytes_ratio.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/all_slow_10kbps_compare_dup_bytes_ratio.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_event_reasons.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_event_reasons.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_safe_forced_reasons.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_safe_forced_reasons.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_switch_rate.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_adaptive_switch_rate.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_arm_dist__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_arm_dist__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_cumulative_regret__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_cumulative_regret__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_entropy_60s__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_entropy_60s__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_feature_weights__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_feature_weights__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_reward_ts__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_reward_ts__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_timeline__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_timeline__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_ucb_decomposition.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_adaptive_ucb_decomposition.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_reward_components_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_reward_components_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/linucb_arm_distribution.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/linucb_entropy_60s.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/plot_manifest.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_shipit/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/analysis_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/all_slow_10kbps_adaptive_outbox_pending_ts__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/all_slow_10kbps_adaptive_outbox_pending_ts__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/all_slow_10kbps_compare_dup_bytes_ratio.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/all_slow_10kbps_compare_dup_bytes_ratio.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_event_reasons.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_event_reasons.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_reward_by_profile_ts.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_reward_by_profile_ts.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_safe_forced_reasons.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_safe_forced_reasons.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_switch_rate.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_adaptive_switch_rate.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_compare_env_metrics_panel.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_all_compare_env_metrics_panel.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_action_heatmap.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_action_heatmap.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_arm_dist__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_arm_dist__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_cumulative_regret__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_cumulative_regret__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_entropy_60s__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_entropy_60s__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_feature_weights__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_feature_weights__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_reward_ts__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_reward_ts__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_stability_abs_res_ts__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_timeline__sim_compare_run_adaptive.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_timeline__sim_compare_run_adaptive.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_ucb_decomposition.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_adaptive_ucb_decomposition.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_reward_components_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_reward_components_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_rx_delay_box.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/linucb_arm_distribution.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/linucb_entropy_60s.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/plot_manifest.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_sim_compare/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_smoke/analysis_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_smoke/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_smoke/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_smoke/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_smoke/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_smoke/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_smoke/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/all_slow_10kbps_adaptive_outbox_pending_ts__testpaper.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/figs/temp_slow_10kbps_compare_rx_delay_box.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_testpaper/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_action_heatmap__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_action_heatmap__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_cumulative_regret__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_cumulative_regret__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_env_metrics__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_env_metrics__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_env_reward_over_time__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_env_reward_over_time__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_feature_weights__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_feature_weights__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_reward_over_time__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_reward_over_time__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_stability_abs_res__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_stability_abs_res__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_timeline__testpaper__slow_10kbps__temp.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/paper_timeline__testpaper__slow_10kbps__temp.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_aoi_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_aoi_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_aoi_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_aoi_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_kbits_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_kbits_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_mae_mean_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_mae_mean_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_mae_p95_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_mae_p95_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_rate_bar.pdf | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/figs/temp_slow_10kbps_compare_rate_bar.png | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/metrics_by_run.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/metrics_summary.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/metrics_vs_periodic.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/quality_audit.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/quality_audit.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/analysis_with_audit/report.md | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/bench_policy_rpi5.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/sim_compare/run_adaptive/logs/collector_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/sim_compare/run_adaptive/logs/decisions_000001.parquet | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/sim_compare/run_adaptive/logs/events_000001.parquet | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/sim_compare/run_fixed_tau/logs/collector_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/sim_compare/run_fixed_tau/logs/events_000001.parquet | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/sim_compare/run_periodic/logs/collector_meta.json | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/sim_compare/run_periodic/logs/events_000001.parquet | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/testpaper/logs/decisions.csv | artifacts | Not in scope | generated/vendor/IDE output |
| artifacts/testpaper/logs/events.csv | artifacts | Not in scope | generated/vendor/IDE output |
| collector/__init__.py | collector | OK |  |
| collector/__pycache__/__init__.cpython-310.pyc | collector | OK |  |
| collector/__pycache__/__init__.cpython-313.pyc | collector | OK |  |
| collector/__pycache__/analyze.cpython-310.pyc | collector | OK |  |
| collector/__pycache__/analyze.cpython-313.pyc | collector | OK |  |
| collector/__pycache__/collector.cpython-310.pyc | collector | OK |  |
| collector/__pycache__/collector.cpython-312.pyc | collector | OK |  |
| collector/__pycache__/plot_labels.cpython-310.pyc | collector | OK |  |
| collector/__pycache__/quality_audit.cpython-310.pyc | collector | OK |  |
| collector/__pycache__/store_sqlite.cpython-310.pyc | collector | OK |  |
| collector/analyze.py | collector | OK |  |
| collector/collector.py | collector | OK |  |
| collector/plot_labels.py | collector | OK |  |
| collector/quality_audit.py | collector | OK |  |
| collector/store_sqlite.py | collector | OK |  |
| common/__init__.py | common | OK |  |
| common/__pycache__/__init__.cpython-310.pyc | common | OK |  |
| common/__pycache__/__init__.cpython-313.pyc | common | OK |  |
| common/__pycache__/config.cpython-310.pyc | common | OK |  |
| common/__pycache__/discord_webhook.cpython-310.pyc | common | OK |  |
| common/__pycache__/discord_webhook.cpython-313.pyc | common | OK |  |
| common/__pycache__/jsonutil.cpython-310.pyc | common | OK |  |
| common/__pycache__/logging_setup.cpython-310.pyc | common | OK |  |
| common/__pycache__/metrics.cpython-310.pyc | common | OK |  |
| common/__pycache__/mqttutil.cpython-310.pyc | common | OK |  |
| common/__pycache__/mqttutil.cpython-313.pyc | common | OK |  |
| common/__pycache__/quantize.cpython-310.pyc | common | OK |  |
| common/__pycache__/schema.cpython-310.pyc | common | OK |  |
| common/__pycache__/schema.cpython-313.pyc | common | OK |  |
| common/__pycache__/timeutil.cpython-310.pyc | common | OK |  |
| common/config.py | common | OK |  |
| common/discord_webhook.py | common | OK |  |
| common/jsonutil.py | common | OK |  |
| common/logging_setup.py | common | OK |  |
| common/metrics.py | common | OK |  |
| common/mqttutil.py | common | OK |  |
| common/quantize.py | common | OK |  |
| common/schema.py | common | OK |  |
| common/timeutil.py | common | OK |  |
| configs/device.yaml | config | OK |  |
| configs/link_profiles.yaml | config | OK |  |
| configs/policy.yaml | config | OK |  |
| data/processed/.gitkeep | data | OK |  |
| data/raw/.gitkeep | data | OK |  |
| docs/figma/README.md | docs | OK |  |
| docs/figma/architecture_ko.png | docs | Not in scope | design asset |
| docs/figma/linucb_state_ko.png | docs | Not in scope | design asset |
| docs/figma/pipeline_ko.svg | docs | Not in scope | design asset |
| docs/figma/sequence_ko.svg | docs | Not in scope | design asset |
| docs/final/CHANGES.md | docs | OK |  |
| docs/final/COMPLETENESS_SCORE.md | docs | OK |  |
| docs/final/FULL_REPO_AUDIT.md | docs | OK |  |
| docs/final/OPERATIONAL_READINESS.md | docs | OK |  |
| docs/final/PLOTS_QUALITY_AUDIT.md | docs | OK |  |
| docs/final/RPI5_PROFILING_REPORT.md | docs | OK |  |
| docs/final/_entrypoints.md | docs | OK |  |
| docs/final/_inventory.txt | docs | OK |  |
| docs/final/systemd/semantic-uplink-stack.service | docs | OK |  |
| docs/hardware.md | docs | OK |  |
| docs/metrics/FIGURE_NAMING.md | docs | OK |  |
| docs/metrics/LABEL_STYLE.md | docs | OK |  |
| docs/specs/architecture.md | docs | OK |  |
| edge/__init__.py | edge | OK |  |
| edge/__pycache__/__init__.cpython-310.pyc | edge | OK |  |
| edge/__pycache__/edge_daemon.cpython-310.pyc | edge | OK |  |
| edge/__pycache__/edge_daemon.cpython-312.pyc | edge | OK |  |
| edge/edge_daemon.py | edge | OK |  |
| edge/policy/__init__.py | edge | OK |  |
| edge/policy/__pycache__/__init__.cpython-310.pyc | edge | OK |  |
| edge/policy/__pycache__/linucb.cpython-310.pyc | edge | OK |  |
| edge/policy/__pycache__/runtime.cpython-310.pyc | edge | OK |  |
| edge/policy/__pycache__/runtime.cpython-312.pyc | edge | OK |  |
| edge/policy/linucb.py | edge | OK |  |
| edge/policy/runtime.py | edge | OK |  |
| edge/predict/__init__.py | edge | OK |  |
| edge/predict/__pycache__/__init__.cpython-310.pyc | edge | OK |  |
| edge/predict/__pycache__/ar1_rls.cpython-310.pyc | edge | OK |  |
| edge/predict/__pycache__/ewma.cpython-310.pyc | edge | OK |  |
| edge/predict/__pycache__/ewma.cpython-312.pyc | edge | OK |  |
| edge/predict/ar1_rls.py | edge | OK |  |
| edge/predict/ewma.py | edge | OK |  |
| edge/rtc/__init__.py | edge | OK |  |
| edge/rtc/__pycache__/__init__.cpython-310.pyc | edge | OK |  |
| edge/rtc/__pycache__/ds3231.cpython-310.pyc | edge | OK |  |
| edge/rtc/ds3231.py | edge | OK |  |
| edge/sensors/__init__.py | edge | OK |  |
| edge/sensors/__pycache__/__init__.cpython-310.pyc | edge | OK |  |
| edge/sensors/__pycache__/mic_rms.cpython-310.pyc | edge | OK |  |
| edge/sensors/__pycache__/temp.cpython-310.pyc | edge | OK |  |
| edge/sensors/mic_rms.py | edge | OK |  |
| edge/sensors/temp.py | edge | OK |  |
| edge/ui/__init__.py | edge | OK |  |
| edge/ui/__pycache__/__init__.cpython-310.pyc | edge | OK |  |
| edge/ui/__pycache__/buttons.cpython-310.pyc | edge | OK |  |
| edge/ui/__pycache__/buttons.cpython-312.pyc | edge | OK |  |
| edge/ui/__pycache__/lcd.cpython-310.pyc | edge | OK |  |
| edge/ui/__pycache__/lcd.cpython-312.pyc | edge | OK |  |
| edge/ui/__pycache__/status.cpython-310.pyc | edge | OK |  |
| edge/ui/__pycache__/status.cpython-312.pyc | edge | OK |  |
| edge/ui/buttons.py | edge | OK |  |
| edge/ui/lcd.py | edge | OK |  |
| edge/ui/status.py | edge | OK |  |
| edge/uploader/__init__.py | edge | OK |  |
| edge/uploader/__pycache__/__init__.cpython-310.pyc | edge | OK |  |
| edge/uploader/__pycache__/mqtt_publisher.cpython-310.pyc | edge | OK |  |
| edge/uploader/__pycache__/outbox.cpython-310.pyc | edge | OK |  |
| edge/uploader/mqtt_publisher.py | edge | OK |  |
| edge/uploader/outbox.py | edge | OK |  |
| experiments/__pycache__/run_scenarios.cpython-310.pyc | experiments | OK |  |
| experiments/run_scenarios.py | experiments | Improvement Suggested | uses print; consider logging for long runs |
| infra/mosquitto/mosquitto.conf | infra | OK |  |
| infra/systemd/semantic-uplink-stack.env.example | infra | OK |  |
| link/__init__.py | link | OK |  |
| link/__pycache__/__init__.cpython-310.pyc | link | OK |  |
| link/shaper/__init__.py | link | OK |  |
| link/shaper/__pycache__/__init__.cpython-310.pyc | link | OK |  |
| link/shaper/__pycache__/tc_profiles.cpython-310.pyc | link | OK |  |
| link/shaper/tc_profiles.py | link | OK |  |
| logs/broker/.gitkeep | logs | Not in scope | generated/vendor/IDE output |
| logs/collector/.gitkeep | logs | Not in scope | generated/vendor/IDE output |
| logs/edge/.gitkeep | logs | Not in scope | generated/vendor/IDE output |
| models/README.md | root | OK |  |
| node_modules/.package-lock.json | node_modules | Not in scope | generated/vendor/IDE output |
| package-lock.json | root | OK |  |
| package.json | root | OK |  |
| pyproject.toml | root | OK |  |
| requirements.txt | root | OK |  |
| results/testpaper_out/figures/bar_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/bar_aoi_p95_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/bar_kbits_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/bar_mae_event_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/bar_mae_event_p95__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/bar_rate_Bps__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_action_heatmap__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_cumulative_regret__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_env_metrics__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_env_reward_over_time__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_feature_weights__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_reward_over_time__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_stability_abs_res__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/paper_timeline__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/figures/pareto_rate_Bps_vs_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/metrics_by_run.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/metrics_summary.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/metrics_vs_periodic.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out/report.md | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/bar_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/bar_aoi_p95_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/bar_kbits_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/bar_mae_event_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/bar_mae_event_p95__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/bar_rate_Bps__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_action_heatmap__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_cumulative_regret__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_env_metrics__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_env_reward_over_time__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_feature_weights__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_reward_over_time__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_stability_abs_res__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/paper_timeline__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/figures/pareto_rate_Bps_vs_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/metrics_by_run.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/metrics_summary.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/metrics_vs_periodic.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out2/report.md | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/bar_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/bar_aoi_p95_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/bar_kbits_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/bar_mae_event_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/bar_mae_event_p95__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/bar_rate_Bps__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_action_heatmap__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_cumulative_regret__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_env_metrics__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_env_reward_over_time__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_feature_weights__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_reward_over_time__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_stability_abs_res__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/paper_timeline__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/figures/pareto_rate_Bps_vs_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/metrics_by_run.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/metrics_summary.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/metrics_vs_periodic.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out3/report.md | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/bar_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/bar_aoi_p95_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/bar_kbits_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/bar_mae_event_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/bar_mae_event_p95__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/bar_rate_Bps__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_action_heatmap__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_cumulative_regret__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_env_metrics__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_env_reward_over_time__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_feature_weights__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_reward_over_time__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_stability_abs_res__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/paper_timeline__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/figures/pareto_rate_Bps_vs_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/metrics_by_run.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/metrics_summary.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/metrics_vs_periodic.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out4/report.md | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/bar_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/bar_aoi_p95_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/bar_kbits_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/bar_mae_event_mean__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/bar_mae_event_p95__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/bar_rate_Bps__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_action_heatmap__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_cumulative_regret__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_env_metrics__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_env_reward_over_time__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_feature_weights__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_reward_over_time__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_stability_abs_res__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/paper_timeline__testpaper__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/figures/pareto_rate_Bps_vs_aoi_mean_ms__slow_10kbps__temp.png | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/metrics_by_run.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/metrics_summary.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/metrics_vs_periodic.csv | results | Not in scope | generated/vendor/IDE output |
| results/testpaper_out5/report.md | results | Not in scope | generated/vendor/IDE output |
| scripts/apply_profile.sh | scripts | OK |  |
| scripts/bench_policy_rpi5.py | scripts | OK |  |
| scripts/health_check.sh | scripts | OK |  |
| scripts/install_systemd_stack.sh | scripts | OK |  |
| scripts/run_stack.sh | scripts | OK |  |
| scripts/start_collector.sh | scripts | OK |  |
| scripts/start_edge.sh | scripts | OK |  |
| scripts/uninstall_systemd_stack.sh | scripts | OK |  |
| semantic_uplink_rpi5.egg-info/PKG-INFO | root | OK |  |
| semantic_uplink_rpi5.egg-info/SOURCES.txt | root | OK |  |
| semantic_uplink_rpi5.egg-info/dependency_links.txt | root | OK |  |
| semantic_uplink_rpi5.egg-info/requires.txt | root | OK |  |
| semantic_uplink_rpi5.egg-info/top_level.txt | root | OK |  |
| stack/__init__.py | stack | OK |  |
| stack/__pycache__/__init__.cpython-310.pyc | stack | OK |  |
| stack/__pycache__/pi_stack.cpython-310.pyc | stack | OK |  |
| stack/pi_stack.py | stack | OK |  |
| tests/__pycache__/conftest.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/__pycache__/conftest.cpython-310.pyc | tests | OK |  |
| tests/__pycache__/conftest.cpython-313-pytest-8.4.2.pyc | tests | OK |  |
| tests/__pycache__/test_ds3231.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/__pycache__/test_ds3231.cpython-310.pyc | tests | OK |  |
| tests/conftest.py | tests | OK |  |
| tests/integration/__pycache__/test_end_to_end_e2e.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/integration/__pycache__/test_end_to_end_e2e.cpython-310.pyc | tests | OK |  |
| tests/integration/__pycache__/test_end_to_end_no_mqtt.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/integration/__pycache__/test_end_to_end_no_mqtt.cpython-310.pyc | tests | OK |  |
| tests/integration/__pycache__/test_end_to_end_placeholder.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/integration/test_end_to_end_e2e.py | tests | OK |  |
| tests/integration/test_end_to_end_no_mqtt.py | tests | OK |  |
| tests/test_ds3231.py | tests | OK |  |
| tests/unit/__pycache__/test_analyze_metrics.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_analyze_metrics.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_buttons.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_buttons.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_config_validation.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_config_validation.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_discord_webhook.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_discord_webhook.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_discord_webhook.cpython-313-pytest-8.4.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_edge_daemon_cli.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_edge_daemon_cli.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_ewma.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_ewma_predictor.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_ewma_predictor.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_experiments_runner_config.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_experiments_runner_config.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_jsonutil.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_jsonutil.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_link_profiles_yaml.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_link_profiles_yaml.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_link_tc_profiles.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_link_tc_profiles.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_linucb.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_linucb.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_linucb_policy.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_linucb_policy.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_mqtt_publisher.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_mqtt_publisher.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_outbox.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_outbox.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_pi_stack.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_pi_stack.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_policy_runtime.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_policy_runtime.cpython-310.pyc | tests | OK |  |
| tests/unit/__pycache__/test_quantize.cpython-310-pytest-9.0.2.pyc | tests | OK |  |
| tests/unit/__pycache__/test_quantize.cpython-310.pyc | tests | OK |  |
| tests/unit/test_analyze_metrics.py | tests | OK |  |
| tests/unit/test_buttons.py | tests | OK |  |
| tests/unit/test_config_validation.py | tests | OK |  |
| tests/unit/test_discord_webhook.py | tests | OK |  |
| tests/unit/test_edge_daemon_cli.py | tests | OK |  |
| tests/unit/test_ewma.py | tests | OK |  |
| tests/unit/test_ewma_predictor.py | tests | OK |  |
| tests/unit/test_experiments_runner_config.py | tests | OK |  |
| tests/unit/test_jsonutil.py | tests | OK |  |
| tests/unit/test_link_profiles_yaml.py | tests | OK |  |
| tests/unit/test_link_tc_profiles.py | tests | OK |  |
| tests/unit/test_linucb.py | tests | OK |  |
| tests/unit/test_linucb_policy.py | tests | OK |  |
| tests/unit/test_mqtt_publisher.py | tests | OK |  |
| tests/unit/test_outbox.py | tests | OK |  |
| tests/unit/test_pi_stack.py | tests | OK |  |
| tests/unit/test_policy_runtime.py | tests | OK |  |
| tests/unit/test_quantize.py | tests | OK |  |
