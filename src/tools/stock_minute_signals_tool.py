"""One deterministic entry for ad-hoc replay and existing scheduled tasks."""
from datetime import datetime

from src.services.stock_minute_signals import read_minute_signals, record_monitor_result


def run(args, *, runtime_ctx=None):
    params = dict(args)
    params.pop("_runtime", None)
    runtime = dict(runtime_ctx or {})
    if runtime.get("scheduled_task_id") and (params.get("as_of") or params.get("since")):
        raise ValueError("scheduled monitors replay the current session; as_of/since are for ad-hoc replay")
    for key in ("as_of", "since"):
        if params.get(key):
            params[key] = datetime.fromisoformat(params[key])
    result = read_minute_signals(**params)
    if runtime.get("scheduled_task_id"):
        if any(s["trade_date"] != s["as_of"][:10] for s in result["snapshots"]):
            result.pop("evaluated", None)
            result.update(events=[], current_conditions=[],
                          observation_note="当前不是所选市场的交易日；保留数据时点，本轮不记录历史交易日的新信号。")
        else:
            result = record_monitor_result(result, owner_id=runtime.get("owner_user_id"),
                schedule_id=runtime.get("scheduled_task_id"), step_id=runtime.get("scheduled_task_step_id"),
                run_id=runtime.get("scheduled_task_run_id"), codes=params["codes"], period=params.get("period", 5))
    else:
        result.pop("evaluated", None)
    return {"tool": "stock_minute_signals", "ok": True, "data": result, "error": ""}
