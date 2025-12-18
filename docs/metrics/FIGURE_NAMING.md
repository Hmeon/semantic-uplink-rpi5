# Figure naming standard

All figures produced by `python -m collector.analyze` must use a paper-ready, deterministic filename
so that:

- reports can embed them reliably
- automated audits can detect missing/extra plots
- multi-run artifacts can be compared and diffed safely

## Rule (default)

`{sensor}_{profile}_{policy}_{metric}[__{run_id}].{ext}`

- `sensor`: sensor name (e.g., `temp`, `mic`); cross-sensor aggregations may use `all`
- `profile`: link profile name; multi-profile aggregations MUST use `all`
- `policy`: policy name; multi-policy comparisons MUST use `compare`
- `metric`: plot identifier (snake_case)
- `run_id` (optional): required only when plots are per-run and would collide otherwise
- `ext`: one of `png`, `pdf`, `svg` (depending on `--plot-formats`)

### Examples

- `temp_slow_10kbps_compare_rate_bar.png`
- `temp_slow_10kbps_compare_rx_delay_box.pdf`
- `all_slow_10kbps_adaptive_outbox_pending_ts__run1.png`
- `temp_all_compare_reward_components_bar.svg`

## Slugging (safety)

To avoid filesystem and Markdown issues, any `/`, `\`, spaces, `:`, `|` must be replaced with `_`.

## Enforcement

`python -m collector.quality_audit --analysis-dir <DIR>` enforces this rule and marks violations
as **FAIL**.
