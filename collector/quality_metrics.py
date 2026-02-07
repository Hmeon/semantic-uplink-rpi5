"""Seq-aligned quality/coverage metrics extracted from collector.analyze."""

from __future__ import annotations

import numpy as np
import pandas as pd

from collector.metrics_core import dedup_and_sort
from common.quantize import quantize_array, quantizer_for_sensor
from common.schema import SensorType


def _pick_baseline_run(g: pd.DataFrame, pol: str) -> str | None:
    sub = g[g["policy"].astype("string") == pol]
    if sub.empty:
        return None
    counts = sub.groupby("run_id", sort=False)["seq"].count()
    if counts.empty:
        return None
    return str(counts.idxmax())


def _recon_err(
    base_seq: np.ndarray,
    base_val: np.ndarray,
    cand_seq: np.ndarray,
    cand_val: np.ndarray,
) -> np.ndarray:
    if base_seq.size == 0 or cand_seq.size == 0:
        return np.array([], dtype="float64")
    idx = np.searchsorted(cand_seq, base_seq, side="right") - 1
    ok = idx >= 0
    if not np.any(ok):
        return np.array([], dtype="float64")
    return np.abs(base_val[ok] - cand_val[idx[ok]]).astype("float64")


def _segments_from_mask(seq: np.ndarray, mask: np.ndarray) -> list[tuple[int, int]]:
    if seq.size == 0 or mask.size == 0 or seq.size != mask.size:
        return []
    idx = np.where(mask)[0]
    if idx.size == 0:
        return []
    segs: list[tuple[int, int]] = []
    s = int(idx[0])
    p = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i == p + 1 and int(seq[i]) == int(seq[p]) + 1:
            p = i
            continue
        segs.append((int(seq[s]), int(seq[p])))
        s = i
        p = i
    segs.append((int(seq[s]), int(seq[p])))
    return segs


def _segment_recall(
    segs: list[tuple[int, int]],
    cand_seq: np.ndarray,
) -> tuple[float, float, float]:
    if not segs:
        return float("nan"), float("nan"), float("nan")
    if cand_seq.size == 0:
        return float(len(segs)), 0.0, 0.0
    hit = 0
    for a, b in segs:
        j = int(np.searchsorted(cand_seq, a, side="left"))
        if j < cand_seq.size and int(cand_seq[j]) <= int(b):
            hit += 1
    recall = float(hit / len(segs))
    return float(len(segs)), float(hit), recall


def compute_seq_aligned_quality_metrics(
    df: pd.DataFrame,
    *,
    baseline_policy: str = "periodic",
    tau_ref_policy: str = "fixed_tau",
) -> pd.DataFrame:
    """Compute quality metrics by aligning sparse policies to a periodic baseline via `seq`."""
    need = {"run_id", "profile", "policy", "sensor", "seq", "val", "res", "tau"}
    if not need.issubset(df.columns):
        return pd.DataFrame()

    df = dedup_and_sort(df).copy()
    min_anomaly_segment_len_samples = 2

    group_cols = ["profile", "sensor"]
    if "meta_scenario" in df.columns:
        group_cols.append("meta_scenario")
    if "meta_seed" in df.columns:
        group_cols.append("meta_seed")

    rows: list[dict[str, object]] = []
    for keys, g in df.groupby(group_cols, sort=False, dropna=False):
        prof = keys[0]
        sensor = keys[1]
        base_run = _pick_baseline_run(g, str(baseline_policy))
        if base_run is None:
            continue
        base = g[
            (g["policy"].astype("string") == str(baseline_policy))
            & (g["run_id"].astype("string") == base_run)
        ]
        base = base.sort_values("seq", kind="mergesort")
        base_seq_s = pd.to_numeric(base["seq"], errors="coerce")
        base_val_s = pd.to_numeric(base["val"], errors="coerce")
        base_res_s = pd.to_numeric(base["res"], errors="coerce")
        base_ok = base_seq_s.notna() & base_val_s.notna() & base_res_s.notna()
        base_seq = base_seq_s[base_ok].astype("int64").to_numpy()
        base_val = base_val_s[base_ok].to_numpy(dtype="float64")
        base_res = base_res_s[base_ok].to_numpy(dtype="float64")

        base_kbits: int | None = None
        if "kbits" in base.columns:
            kb_s = pd.to_numeric(base["kbits"], errors="coerce")
            kb = kb_s[base_ok].to_numpy(dtype="float64")
            kb = kb[np.isfinite(kb)]
            if kb.size > 0:
                base_kbits = int(np.median(kb))

        tau_ref = float("nan")
        tau_run = _pick_baseline_run(g, str(tau_ref_policy))
        if tau_run is not None:
            tau_src = g[
                (g["policy"].astype("string") == str(tau_ref_policy))
                & (g["run_id"].astype("string") == tau_run)
            ]
            tau_vals = pd.to_numeric(tau_src["tau"], errors="coerce").to_numpy(dtype="float64")
            tau_vals = tau_vals[np.isfinite(tau_vals)]
            if tau_vals.size > 0:
                tau_ref = float(np.median(tau_vals))

        anomaly_segs: list[tuple[int, int]] = []
        tau_ref_ok = bool(np.isfinite(tau_ref))
        if tau_ref_ok:
            mask = np.isfinite(base_res) & (np.abs(base_res) > float(tau_ref))
            anomaly_segs = _segments_from_mask(base_seq, mask)
            anomaly_segs = [
                (a, b)
                for (a, b) in anomaly_segs
                if (int(b) - int(a) + 1) >= int(min_anomaly_segment_len_samples)
            ]

        for run_id, gr in g.groupby("run_id", sort=False):
            pol = str(gr["policy"].iloc[0])
            gr = gr.sort_values("seq", kind="mergesort")
            cand_seq_s = pd.to_numeric(gr["seq"], errors="coerce")
            cand_val_s = pd.to_numeric(gr["val"], errors="coerce")
            cand_ok = cand_seq_s.notna() & cand_val_s.notna()
            cand_seq = cand_seq_s[cand_ok].astype("int64").to_numpy()
            cand_val = cand_val_s[cand_ok].to_numpy(dtype="float64")

            if base_kbits is not None:
                try:
                    q = quantizer_for_sensor(SensorType(str(sensor)), int(base_kbits))
                    cand_val, _ = quantize_array(q, cand_val)
                except Exception:
                    pass

            err = _recon_err(base_seq, base_val, cand_seq, cand_val)
            if err.size > 0:
                recon_mean = float(np.mean(err))
                recon_p95 = float(np.quantile(err, 0.95))
                recon_p99 = float(np.quantile(err, 0.99))
                recon_max = float(np.max(err))
            else:
                recon_mean = float("nan")
                recon_p95 = float("nan")
                recon_p99 = float("nan")
                recon_max = float("nan")

            seg_n, seg_hit, seg_recall = float("nan"), float("nan"), float("nan")
            if not tau_ref_ok:
                seg_n, seg_hit, seg_recall = float("nan"), float("nan"), float("nan")
            elif not anomaly_segs:
                seg_n, seg_hit, seg_recall = 0.0, 0.0, 1.0
            elif cand_seq.size > 0:
                seg_n, seg_hit, seg_recall = _segment_recall(anomaly_segs, cand_seq)
            else:
                seg_n, seg_hit, seg_recall = float(len(anomaly_segs)), 0.0, 0.0

            rows.append(
                {
                    "run_id": str(run_id),
                    "profile": str(prof),
                    "policy": str(pol),
                    "sensor": str(sensor),
                    "recon_mae_mean": recon_mean,
                    "recon_mae_p95": recon_p95,
                    "recon_mae_p99": recon_p99,
                    "recon_mae_max": recon_max,
                    "anomaly_tau_ref": float(tau_ref),
                    "anomaly_segments": seg_n,
                    "anomaly_segments_hit": seg_hit,
                    "anomaly_segment_recall": seg_recall,
                }
            )

    return pd.DataFrame(rows)


__all__ = ["compute_seq_aligned_quality_metrics"]
