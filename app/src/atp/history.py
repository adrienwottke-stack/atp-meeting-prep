"""Coarse, read-only history timing; a budget is never a promise."""

from datetime import UTC, datetime

PHASE_SECONDS = {"validating": 300, "researching": 300, "evaluating": 120,
                 "generating": 60, "exporting": 30}
TERMINAL = {"completed", "partial", "failed", "cancelled"}


def history_timing(run, deadline_seconds, now=None):
    now = now or datetime.now(UTC)
    status = run["status"]
    remaining, state = None, "unavailable"
    if status in TERMINAL:
        state = "finished"
    elif status == "queued":
        state = "queued"
    elif status == "needs_clarification":
        state = "paused"
    elif run.get("job_status") == "running":
        lease, saved = run.get("lease_until"), run.get("usage_saved_at")
        if lease and lease <= now:
            state = "interrupted"
        elif lease and saved:
            phase = run.get("phase") or status
            usage = run.get("job_usage") or {}
            active = float(usage.get("active_seconds", 0)) + max(0, (now - saved).total_seconds())
            if phase in PHASE_SECONDS:
                remaining = max(0, min(PHASE_SECONDS[phase], deadline_seconds - active))
                state = "running"
    return {"estimated_remaining_seconds": remaining, "timing_state": state}
