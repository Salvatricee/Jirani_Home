"""Connector to the n8n supporting workflow.

After a human reviews a draft, the app POSTs the outcome to an n8n webhook. n8n validates it, creates a
hand-off task record for the right department and returns the result, which the app stores.
This creates a RECORD only. It never changes an order, refund or delivery (P05: out of scope).

Statuses: ok | rejected (n8n refused the payload) | failed (unreachable/timeout/error) | simulated
"""
import json
import time
import urllib.error
import urllib.request

import config


def build_payload(ticket_id, draft, review_action, reviewer, final_text) -> dict:
    return {
        "ticket_id": ticket_id,
        "draft_id": draft["id"],
        "draft_version": draft["version"],
        "route": draft["route"],
        "destination": draft["destination"],
        "action": review_action,  # approved | edited_and_approved | rejected
        "reviewer": reviewer,
        "policy_ids": json.loads(draft["policy_ids"]),
        "final_text_chars": len(final_text or ""),
        "source": "jirani-prototype",
    }


def trigger(payload: dict, simulate: bool = False) -> dict:
    if simulate:
        return {"status": "simulated", "http_status": None, "latency_s": 0.0,
                "response": {"note": "SIMULATED: n8n was not called. No task record was created."}}
    start = time.time()
    req = urllib.request.Request(
        config.N8N_WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.N8N_TIMEOUT_S) as r:
            body = json.loads(r.read().decode("utf-8") or "{}")
            return {"status": "ok", "http_status": r.status, "response": body,
                    "latency_s": round(time.time() - start, 2)}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
        status = "rejected" if e.code == 400 else "failed"
        return {"status": status, "http_status": e.code, "response": body, "error": f"HTTP {e.code}",
                "latency_s": round(time.time() - start, 2)}
    except Exception as e:  # noqa: BLE001 - unreachable, timeout, bad JSON: record it, never crash
        return {"status": "failed", "http_status": None, "response": None,
                "error": f"{type(e).__name__}: {e}", "latency_s": round(time.time() - start, 2)}
