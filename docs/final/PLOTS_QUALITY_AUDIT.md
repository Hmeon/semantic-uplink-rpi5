# Plots quality audit

- Analysis dir: `artifacts/analysis_shipit`
- Figs dir: `artifacts/analysis_shipit/figs`
- Formats inferred: `png,pdf`
- Plot flags: {'plots_enabled': True, 'diagnostic_plots_enabled': True, 'ucb_timeseries_enabled': False, 'pareto_p95_enabled': False}

## Summary
- Visualization expected PASS/FAIL/SKIP: {'PASS': 17, 'SKIP': 3}
- Visualization files PASS/FAIL/SKIP: {'PASS': 50}
- Visualization labels PASS/FAIL/SKIP: {'PASS': 28}
- Format audit: PASS (ok)

## Expected figures matrix
| base_name | status | reason | formats |
|---|---|---|---|
| temp_slow_10kbps_compare_rate_bar | PASS | expected (data present) | png,pdf |
| temp_slow_10kbps_compare_aoi_mean_bar | PASS | expected (data present) | png,pdf |
| temp_slow_10kbps_compare_aoi_p95_bar | PASS | expected (data present) | png,pdf |
| temp_slow_10kbps_compare_mae_mean_bar | PASS | expected (data present) | png,pdf |
| temp_slow_10kbps_compare_mae_p95_bar | PASS | expected (data present) | png,pdf |
| temp_slow_10kbps_compare_kbits_mean_bar | PASS | expected (data present) | png,pdf |
| temp_slow_10kbps_compare_reward_components_bar | PASS | expected (reward components present) | png,pdf |
| temp_slow_10kbps_compare_pareto_rate_vs_aoi_mean | PASS | expected (data present) | png,pdf |
| temp_slow_10kbps_compare_rx_delay_box | PASS | expected (rx_delay present) | png,pdf |
| all_slow_10kbps_adaptive_outbox_pending_ts__sim_compare_run_adaptive | PASS | expected (outbox metrics present) | png,pdf |
| all_slow_10kbps_fixed_tau_outbox_pending_ts__sim_compare_run_fixed_tau | SKIP | outbox metrics present but all non-finite | png,pdf |
| all_slow_10kbps_periodic_outbox_pending_ts__sim_compare_run_periodic | SKIP | outbox metrics present but all non-finite | png,pdf |
| all_slow_10kbps_compare_dup_bytes_ratio | PASS | expected (dup_bytes_ratio present) | png,pdf |
| temp_slow_10kbps_adaptive_arm_dist__sim_compare_run_adaptive | PASS | expected (arm distribution present) | png,pdf |
| temp_slow_10kbps_adaptive_entropy_60s__sim_compare_run_adaptive | PASS | expected (entropy present) | png,pdf |
| temp_all_adaptive_safe_forced_reasons | PASS | expected (safe forced present) | png,pdf |
| temp_all_adaptive_switch_rate | PASS | expected (switch rate present) | png,pdf |
| temp_all_adaptive_rate_limit_skips_per_decision | SKIP | rate-limit skips all zero | png,pdf |
| temp_slow_10kbps_adaptive_ucb_decomposition | PASS | expected (UCB terms present) | png,pdf |
| temp_all_adaptive_event_reasons | PASS | expected (event reasons present) | png,pdf |

## DPI/size checks
- PNG min size and DPI checks are enforced by `collector/quality_audit.py` (min bytes, min width/height, DPI >= 300).
- Vector format requirement: at least one of PDF/SVG (current run uses PDF).
