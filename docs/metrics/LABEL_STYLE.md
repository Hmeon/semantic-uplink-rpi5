# Plot label & unit style

This repo treats plots as report/paper artifacts, so axis labels must be:

- consistent across plots
- explicit about units
- stable enough for automated audits

## Conventions

- Percent: `[%]`
- Probability / rate: `[0..1]`
- Entropy: `[bits]`
- Latency: `[ms]`
- Throughput: `[B/s]`
- Counts: `[count]`

## Required labels (examples)

- Duplicate bytes ratio: `Duplicate bytes ratio [%]`
- Latency: `E2E latency (rx - gen) [ms]`

## Implementation guidance

- Prefer reusing shared string constants (to avoid typos and drift).
- Do not leave `xlabel`/`ylabel` empty; audits may mark missing labels as **FAIL**.

