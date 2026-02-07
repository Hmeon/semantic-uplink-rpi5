"""Core metrics aggregation primitives extracted from collector.analyze."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from common.schema import EventMsg


def dedup_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """De-duplicate QoS1 events and enforce deterministic ordering."""
    key = ["run_id", "device_id", "sensor", "seq"]
    if not set(key).issubset(df.columns):
        raise ValueError("dedup requires columns: run_id, device_id, sensor, seq")
    time_col = "t_recv_ns" if "t_recv_ns" in df.columns else "ts"
    out = df.sort_values(["run_id", "device_id", "sensor", "seq", time_col], kind="mergesort")
    out = out.drop_duplicates(subset=key, keep="first", ignore_index=True)
    out = out.sort_values(["run_id", "device_id", "sensor", time_col], kind="mergesort")
    return out.reset_index(drop=True)


def estimate_payload_bytes(df: pd.DataFrame) -> pd.Series:
    """Estimate MQTT publish byte size per event row."""
    from common.metrics import mqtt_publish_size

    if "mqtt_bytes" in df.columns and not df["mqtt_bytes"].isna().any():
        s = df["mqtt_bytes"].astype("int64")
        s.name = "mqtt_bytes"
        return s
    if "mqtt_size_bytes" in df.columns and not df["mqtt_size_bytes"].isna().any():
        s = df["mqtt_size_bytes"].astype("int64")
        s.name = "mqtt_bytes"
        return s

    def _calc(row) -> int:
        msg = EventMsg.from_dict(
            {
                "ts": int(row["ts"]),
                "seq": int(row["seq"]),
                "device_id": str(row["device_id"]),
                "sensor": str(row["sensor"]),
                "val": float(row["val"]),
                "pred": float(row["pred"]),
                "res": float(row["res"]),
                "tau": float(row["tau"]),
                "kbits": int(row["kbits"]),
                "profile": str(row["profile"]),
                "policy": str(row["policy"]),
            }
        )
        payload_len = len(msg.to_json_bytes())
        topic = row.get("topic", None)
        if isinstance(topic, str) and topic:
            return int(mqtt_publish_size(topic, payload_len, qos=1))
        return int(msg.estimated_mqtt_size(qos=1))

    return df.apply(_calc, axis=1).astype("int64")


def _aoi_mean_and_p95_from_segments(
    start_aoi_ms: np.ndarray,
    deltas_ms: np.ndarray,
    *,
    p: float = 0.95,
) -> tuple[float, float]:
    """Compute AoI mean/P-quantile from linear-growth AoI segments."""
    if start_aoi_ms.size == 0 or deltas_ms.size == 0:
        return float("nan"), float("nan")
    if start_aoi_ms.size != deltas_ms.size:
        raise ValueError("start_aoi_ms and deltas_ms must have same length")

    mask = np.isfinite(start_aoi_ms) & np.isfinite(deltas_ms) & (deltas_ms > 0)
    a0 = start_aoi_ms[mask].astype("float64")
    d = deltas_ms[mask].astype("float64")
    if d.size == 0:
        return float("nan"), float("nan")

    total = float(np.sum(d))
    if total <= 0:
        return float("nan"), float("nan")

    mean_ms = float((np.sum(a0 * d) + np.sum(d * d) / 2.0) / total)

    if not (0.0 < p < 1.0):
        raise ValueError("p must be in (0,1)")
    target = p * total

    hi = float(np.max(a0 + d))
    lo = 0.0
    if not math.isfinite(hi) or hi <= 0:
        return mean_ms, float("nan")

    for _ in range(60):
        mid = (lo + hi) / 2.0
        s = float(np.sum(np.clip(mid - a0, 0.0, d)))
        if s < target:
            lo = mid
        else:
            hi = mid
    return mean_ms, float(hi)


def aoi_mean_and_p95_from_rx(gen_ns: np.ndarray, recv_ns: np.ndarray) -> tuple[float, float]:
    """Compute mean/P95 AoI using receiver timestamps."""
    if gen_ns.size < 2 or recv_ns.size < 2:
        return float("nan"), float("nan")
    if gen_ns.size != recv_ns.size:
        raise ValueError("gen_ns and recv_ns must have same length")

    gen = gen_ns.astype(np.int64)
    recv = recv_ns.astype(np.int64)
    order = np.argsort(recv, kind="mergesort")
    gen = gen[order]
    recv = recv[order]

    gen_eff = np.maximum.accumulate(gen)
    start_aoi_ms = np.maximum((recv[:-1] - gen_eff[:-1]).astype("float64") / 1e6, 0.0)
    deltas_ms = np.diff(recv.astype(np.int64)).astype("float64") / 1e6
    return _aoi_mean_and_p95_from_segments(start_aoi_ms, deltas_ms, p=0.95)


def aoi_mean_and_p95(ts_ns: np.ndarray) -> tuple[float, float]:
    """Compute mean/P95 AoI using inter-event gaps (no receive time)."""
    if ts_ns.size < 2:
        return float("nan"), float("nan")
    deltas_ms = np.diff(ts_ns.astype(np.int64)) / 1e6
    start_aoi_ms = np.zeros_like(deltas_ms, dtype="float64")
    return _aoi_mean_and_p95_from_segments(start_aoi_ms, deltas_ms, p=0.95)


def summarize_by_run(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate metrics per run/profile/policy/sensor."""
    need = {"run_id", "ts", "device_id", "sensor", "seq", "profile", "policy", "res", "kbits"}
    if not need.issubset(df.columns):
        missing = sorted(need - set(df.columns))
        raise ValueError(f"missing columns for summarize: {missing}")

    gdf = dedup_and_sort(df).copy()
    gdf["mqtt_bytes"] = estimate_payload_bytes(gdf)

    keys = ["run_id", "profile", "policy", "sensor"]
    rows = []
    for (run_id, prof, pol, sensor), g in gdf.groupby(keys, sort=False):
        use_recv = "t_recv_ns" in g.columns and g["t_recv_ns"].notna().any()
        rx_delay_mean_ms = float("nan")
        rx_delay_p50_ms = float("nan")
        rx_delay_p95_ms = float("nan")
        event_reason_threshold_count = float("nan")
        event_reason_heartbeat_count = float("nan")
        event_reason_threshold_frac = float("nan")
        event_reason_heartbeat_frac = float("nan")

        if use_recv:
            g = g.sort_values("t_recv_ns", kind="mergesort")
            recv = g["t_recv_ns"].astype("int64").to_numpy()
            gen = g["ts"].astype("int64").to_numpy()
            if recv.size < 2:
                dur_s = np.nan
                rate = np.nan
                aoi_mean = np.nan
                aoi_p95 = np.nan
            else:
                dur_s = float((recv.max() - recv.min()) / 1e9)
                total_bytes = float(g["mqtt_bytes"].sum())
                rate = (total_bytes / dur_s) if dur_s > 0 else np.nan
                aoi_mean, aoi_p95 = aoi_mean_and_p95_from_rx(gen, recv)
                rx_delay_ms = np.maximum((recv - gen).astype("float64") / 1e6, 0.0)
                if rx_delay_ms.size > 0:
                    rx_delay_mean_ms = float(np.mean(rx_delay_ms))
                    rx_delay_p50_ms = float(np.quantile(rx_delay_ms, 0.50))
                    rx_delay_p95_ms = float(np.quantile(rx_delay_ms, 0.95))
        else:
            ts = np.sort(g["ts"].astype("int64").to_numpy())
            if ts.size < 2:
                dur_s = np.nan
                rate = np.nan
                aoi_mean = np.nan
                aoi_p95 = np.nan
            else:
                dur_s = float((ts.max() - ts.min()) / 1e9)
                total_bytes = float(g["mqtt_bytes"].sum())
                rate = (total_bytes / dur_s) if dur_s > 0 else np.nan
                aoi_mean, aoi_p95 = aoi_mean_and_p95(ts)

        time_base = "recv" if use_recv else "ts"

        if "event_reason" in g.columns:
            er = g["event_reason"].astype("string")
            thr_mask = er.isin(["THRESHOLD", "THRESHOLD_OVERRIDE", "SAFETY_AOI"])
            event_reason_threshold_count = float(thr_mask.sum())
            event_reason_heartbeat_count = float((er == "HEARTBEAT").sum())
            if len(g) > 0:
                event_reason_threshold_frac = float(event_reason_threshold_count / len(g))
                event_reason_heartbeat_frac = float(event_reason_heartbeat_count / len(g))

        mae_mean = float(g["res"].abs().mean())
        mae_p95 = float(g["res"].abs().quantile(0.95)) if len(g) > 0 else np.nan

        g_seq = g.sort_values("ts", kind="mergesort")
        seq = g_seq["seq"].astype("int64").to_numpy()
        if seq.size == 0:
            n_samples_est = 0
            n_suppressed_est = 0
            send_ratio = float("nan")
        else:
            diffs = np.diff(seq)
            n_suppressed_est = int(np.sum(np.maximum(diffs - 1, 0)))
            n_samples_est = int(seq.size + n_suppressed_est)
            send_ratio = float(seq.size / n_samples_est) if n_samples_est > 0 else float("nan")

        if math.isfinite(dur_s) and dur_s > 0:
            event_rate_hz = float(len(g) / dur_s)
        else:
            event_rate_hz = float("nan")

        action_unique_count = float("nan")
        action_switch_rate = float("nan")
        if len(g) > 0 and {"tau", "kbits"}.issubset(g.columns):
            tau_key = pd.to_numeric(g["tau"], errors="coerce").round(6)
            kbits_key = pd.to_numeric(g["kbits"], errors="coerce")
            mask = tau_key.notna() & kbits_key.notna()
            if mask.any():
                actions = list(
                    zip(
                        tau_key.loc[mask].astype("float64").to_numpy(),
                        kbits_key.loc[mask].astype("int64").to_numpy(),
                    )
                )
                action_unique_count = float(len(set(actions)))
                if len(actions) >= 2:
                    switches = sum(a != b for a, b in zip(actions[1:], actions[:-1]))
                    action_switch_rate = float(switches / (len(actions) - 1))

        rows.append(
            {
                "run_id": str(run_id),
                "profile": str(prof),
                "policy": str(pol),
                "sensor": str(sensor),
                "n_events": int(len(g)),
                "n_samples_est": int(n_samples_est),
                "n_suppressed_est": int(n_suppressed_est),
                "send_ratio": float(send_ratio),
                "duration_s": dur_s,
                "event_rate_hz": float(event_rate_hz),
                "rate_Bps": rate,
                "aoi_mean_ms": aoi_mean,
                "aoi_p95_ms": aoi_p95,
                "mae_event_mean": mae_mean,
                "mae_event_p95": mae_p95,
                "kbits_mean": float(g["kbits"].mean()),
                "action_unique_count": float(action_unique_count),
                "action_switch_rate": float(action_switch_rate),
                "time_base": time_base,
                "rx_delay_mean_ms": float(rx_delay_mean_ms),
                "rx_delay_p50_ms": float(rx_delay_p50_ms),
                "rx_delay_p95_ms": float(rx_delay_p95_ms),
                "event_reason_threshold_count": float(event_reason_threshold_count),
                "event_reason_heartbeat_count": float(event_reason_heartbeat_count),
                "event_reason_threshold_frac": float(event_reason_threshold_frac),
                "event_reason_heartbeat_frac": float(event_reason_heartbeat_frac),
            }
        )

    out = pd.DataFrame(rows)
    pol_order = ["periodic", "fixed_tau", "adaptive"]
    out["policy"] = pd.Categorical(out["policy"], categories=pol_order, ordered=True)
    out = out.sort_values(["run_id", "profile", "policy", "sensor"]).reset_index(drop=True)
    return out


__all__ = [
    "aoi_mean_and_p95",
    "aoi_mean_and_p95_from_rx",
    "dedup_and_sort",
    "estimate_payload_bytes",
    "summarize_by_run",
]
