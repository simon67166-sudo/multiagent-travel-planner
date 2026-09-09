"""First-stage exception assessment. No automatic deletion or event verification."""
def run(shared_state: dict, event_type: str, event_detail: str | None = None) -> dict:
    affected = []
    for day in shared_state["trip_plan"]["days"].values():
        for node in day["nodes"].values():
            if node.get("place") and node["place"] in (event_detail or ""):
                affected.append(node["place"])
    return {
        "needs_replan": bool(affected) and event_type != "unknown",
        "requires_confirmation": True,
        "event_verified": False,
        "affected_locations": affected,
        "suggested_adjustment": "行程尚未變更。請先確認事件資訊，再決定是否調整受影響地點。",
        "event_detail": event_detail,
    }
