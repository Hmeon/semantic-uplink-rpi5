# Changes (ship-it audit)

## 2025-12-20
- Fixed editable install failure by defining explicit package discovery in `pyproject.toml` (no runtime behavior change).
- Updated Matplotlib boxplot calls to use `tick_labels` to avoid deprecation warnings (plot output unchanged).
- Default behavior unchanged; diagnostics and optional safety settings remain default-off.
