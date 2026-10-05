"""Local MOCK of the n8n webhook (same contract as n8n/jirani-review-outcome-workflow.json).

This is NOT n8n. Use it only to test the app when n8n isn't running, and say so in your evaluation.
    python tools/mock_n8n.py            # listens on http://localhost:5678/webhook/jirani-review-outcome
    python tools/mock_n8n.py --fail     # always returns HTTP 500, to test workflow-failure handling
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PATH = "/webhook/jirani-review-outcome"
REQUIRED = ["ticket_id", "draft_id", "draft_version", "route", "destination", "action", "reviewer"]
VALID_ACTIONS = ["approved", "edited_and_approved", "rejected"]
PRIORITIES = {
    "safety_queue": ("urgent", 1), "service_lead": ("high", 8), "logistics_followup": ("normal", 24),
    "manager_decision": ("normal", 24), "returns_review": ("normal", 24), "service_review": ("normal", 48),
    "agent_followup": ("normal", 24), "agent_review": ("normal", 24),
}
FAIL = "--fail" in sys.argv


def process(body: dict):
    errors = []
    missing = [k for k in REQUIRED if body.get(k) in (None, "")]
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if body.get("action") and body["action"] not in VALID_ACTIONS:
        errors.append("invalid action: " + str(body["action"]))
    if errors:
        return 400, {"valid": False, "status": "rejected", "errors": errors}
    if body["action"] == "rejected":
        return 200, {"valid": True, "status": "no_task", "reason": "Draft rejected by reviewer; no hand-off created",
                     "ticket_id": body["ticket_id"], "draft_id": body["draft_id"]}
    priority, due = PRIORITIES.get(body["destination"], ("normal", 24))
    return 200, {
        "valid": True, "status": "task_created", "run_id": "MOCK-" + format(int(time.time() * 1000), "X"),
        "ticket_id": body["ticket_id"], "draft_id": body["draft_id"],
        "task": {"queue": body["destination"], "priority": priority, "due_hours": due,
                 "title": f"{body['route']} for ticket {body['ticket_id']}", "policy_ids": body.get("policy_ids", []),
                 "approved_by": body["reviewer"], "draft_version": body["draft_version"]},
        "note": "MOCK server. Record only. No order, refund or delivery was changed.",
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        if self.path != PATH:
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        code, out = (500, {"error": "mock forced failure"}) if FAIL else process(body)
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    print(f"MOCK n8n listening on http://localhost:5678{PATH}  (fail mode: {FAIL})")
    HTTPServer(("localhost", 5678), Handler).serve_forever()
