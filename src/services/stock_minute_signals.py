"""Completed-bar technical events. No trading decisions, vendor calls or messages."""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from src.services.stock_indicator_minute import MINUTE_REVISION, SHANGHAI, read_minute_window
from src.services.stock_indicator_store import ROOT
from src.services.stock_minute_cache import atomic_json, file_lock

RULES = {
    "ma20_cross_up": "收盘由不高于MA20变为高于MA20",
    "ma20_cross_down": "收盘由不低于MA20变为低于MA20",
    "ma20_hold_above": "连续指定根完整K线收盘高于MA20，首次达到要求",
    "session_macd_cross_up": "当日起算MACD柱由不大于0变为大于0",
    "session_macd_cross_down": "当日起算MACD柱由不小于0变为小于0",
    "volume_expansion": "当前K量能比超过阈值的区段首次观测",
}
SIGNAL_REVISION = "minute_rules_v1"


def rule_config(rules, confirm_bars=3, volume_ratio_threshold=1.5):
    selected = sorted(set(rules or ["ma20_cross_up", "ma20_cross_down"]))
    if isinstance(rules, str) or set(selected) - RULES.keys():
        raise ValueError("rules must contain supported minute rule names")
    if type(confirm_bars) is not int or not 1 <= confirm_bars <= 60:
        raise ValueError("confirm_bars must be an integer in 1..60")
    if isinstance(volume_ratio_threshold, bool) or not math.isfinite(float(volume_ratio_threshold)) or volume_ratio_threshold <= 0:
        raise ValueError("volume_ratio_threshold must be positive and finite")
    return {"rules": selected, "confirm_bars": confirm_bars,
            "volume_ratio_threshold": float(volume_ratio_threshold), "revision": SIGNAL_REVISION}


def evaluate_minute_signals(rows, *, config, since=None):
    """Evaluate the whole history first; apply the requested event window last."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["code"], row["trade_date"], row["period_minutes"])].append(row)
    events, states, evaluated = [], [], set()
    for (code, day, period), history in grouped.items():
        history.sort(key=lambda r: r["bar_end_time"])
        previous, run_above = None, 0
        for index, row in enumerate(history):
            def margin(bar, name):
                if not bar or not bar["is_finalized"]:
                    return None
                if name.startswith("ma20"):
                    a, b = bar.get("close"), bar.get("ma20")
                    return a - b if a is not None and b is not None else None
                if name.startswith("session_macd"):
                    return bar.get("session_macd_hist")
                value = bar.get("volume_ratio5")
                return value - config["volume_ratio_threshold"] if value is not None else None
            ma = margin(row, "ma20")
            run_above = run_above + 1 if ma is not None and ma > 0 else 0
            for name in config["rules"]:
                current, prior = margin(row, name), margin(previous, name)
                known = current is not None
                if "cross" in name:
                    known = known and prior is not None
                    triggered = known and (prior <= 0 < current if name.endswith("up") else prior >= 0 > current)
                elif name == "ma20_hold_above":
                    triggered = known and run_above == config["confirm_bars"]
                else:
                    triggered = known and current > 0 and (prior is None or prior <= 0)
                key = (code, name, row["bar_end_time"])
                # A missing preceding input cannot invalidate a previously seen event.
                if known and (since is None or datetime.fromisoformat(row["bar_end_time"]) > since) and (
                    name != "ma20_hold_above" or all(margin(r, name) is not None
                        for r in history[max(0, index-config["confirm_bars"]+1):index+1])):
                    evaluated.add(key)
                if triggered and (since is None or datetime.fromisoformat(row["bar_end_time"]) > since):
                    identity = json.dumps([code, day, period, name, row["bar_end_time"], config, MINUTE_REVISION], sort_keys=True)
                    events.append({"event_id": hashlib.sha256(identity.encode()).hexdigest(),
                        "code": code, "rule": name, "bar_end_time": row["bar_end_time"],
                        "trade_date": day, "period_minutes": period, "rule_revision": SIGNAL_REVISION,
                        "formula_revision": MINUTE_REVISION,
                        "evidence": {"previous_margin": prior, "current_margin": current,
                                     "confirmed_bars": run_above if name == "ma20_hold_above" else None}})
            previous = row
        last = history[-1]
        for name in config["rules"]:
            value = margin(last, name)
            holds = None if value is None else (value < 0 if name.endswith("down") else value > 0)
            if name == "ma20_hold_above" and holds:
                holds = run_above >= config["confirm_bars"]
            states.append({"code": code, "rule": name, "data_as_of": last["bar_end_time"],
                           "condition_holds": holds})
    return {"events": sorted(events, key=lambda r: (r["bar_end_time"], r["code"], r["rule"])),
            "current_conditions": states, "evaluated": evaluated}


def read_minute_signals(*, codes, rules=None, period=5, as_of=None, since=None,
                        confirm_bars=3, volume_ratio_threshold=1.5, max_lag_seconds=None,
                        reader=read_minute_window):
    config = rule_config(rules, confirm_bars, volume_ratio_threshold)
    as_of = as_of or datetime.now(SHANGHAI)
    if as_of.tzinfo is None:
        raise ValueError("as_of requires a timezone")
    if isinstance(codes, str) or not 1 <= len(codes) <= 100:
        raise ValueError("provide 1..100 full stock codes")
    if since is not None and since.tzinfo is None:
        raise ValueError("since requires a timezone")
    if since is not None and since > as_of:
        raise ValueError("since must not be later than as_of")
    selected = sorted(set(codes))
    rows, snapshots, hits = [], [], 0
    for offset in range(0, len(selected), 20):
        data = reader(codes=selected[offset:offset + 20], period=period, as_of=as_of,
                      max_lag_seconds=max_lag_seconds)
        rows.extend(data["rows"])
        snapshots.extend(data["snapshots"])
        hits += data.get("source_cache_hits", 0)
    if since is not None and any(s["trade_date"] != since.astimezone(SHANGHAI).date().isoformat() for s in snapshots):
        raise ValueError("since must be in the selected trading session")
    result = evaluate_minute_signals(rows, config=config, since=since)
    result.update(snapshots=snapshots, config=config, external_requests=0, source_cache_hits=hits)
    return result


def record_monitor_result(result, *, owner_id, schedule_id, step_id, run_id, codes, period,
                          detected_at=None, root=None):
    """Persist observed events in existing artifact storage; never send notifications.

    Owner/schedule/step identities come exclusively from trusted task runtime.
    Replays remain queryable after a failed task run. Detection is not delivery.
    """
    if not all((owner_id, schedule_id, step_id, run_id)):
        raise ValueError("monitor recording requires trusted owner, schedule, step and run identities")
    detected = (detected_at or datetime.now(SHANGHAI)).isoformat()
    scope = json.dumps([owner_id, schedule_id, step_id, sorted(set(codes)), period,
                        result["config"], MINUTE_REVISION], sort_keys=True)
    directory = Path(root or ROOT / "data/stock_indicator_artifacts/minute_signals") / hashlib.sha256(scope.encode()).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    sessions = {s["trade_date"] for s in result["snapshots"]}
    if len(sessions) != 1:
        raise ValueError("a monitor run must cover one trading session")
    path = directory / f"{sessions.pop()}.json"
    with file_lock(directory / "events.lock"):
        state = json.loads(path.read_text()) if path.exists() else {"events": {}, "revisions": []}
        if state.get("last_run_id") == run_id:
            return state["last_result"]
        found = {event["event_id"]: event for event in result["events"]}
        fingerprints = {s["code"]: s["input_fingerprint"] for s in result["snapshots"]}
        added, corrections = [], []
        for event_id, event in found.items():
            old = state["events"].get(event_id)
            if old is None:
                recorded = {**event, "detected_at": detected, "input_fingerprint": fingerprints[event["code"]]}
                state["events"][event_id] = recorded
                added.append(recorded)
            elif old.get("invalidated_at") or old["evidence"] != event["evidence"]:
                correction = {"event_id": event_id, "recorded_at": detected,
                              "input_fingerprint": fingerprints[event["code"]], "evidence": event["evidence"]}
                # Original observation remains immutable in revision history.
                state["revisions"].append({"previous_observation": dict(old), **correction})
                old.pop("invalidated_at", None)
                old.update(evidence=event["evidence"], revised_at=detected,
                           input_fingerprint=fingerprints[event["code"]])
                corrections.append(correction)
        for event_id, old in state["events"].items():
            key = (old["code"], old["rule"], old["bar_end_time"])
            if event_id not in found and not old.get("invalidated_at") and key in result["evaluated"]:
                old["invalidated_at"] = detected
                correction = {"event_id": event_id, "invalidated_at": detected}
                state["revisions"].append(correction)
                corrections.append(correction)
        output = {k: v for k, v in result.items() if k != "evaluated"}
        output.update(events=list(state["events"].values()), new_events=added, corrections=corrections,
                      detected_at=detected, event_artifact=str(path),
                      processed_data_as_of={s["code"]: s["data_as_of"] for s in result["snapshots"]})
        state.update(last_run_id=run_id, last_result=output)
        atomic_json(path, state)
        return output
