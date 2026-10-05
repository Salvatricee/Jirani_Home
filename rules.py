"""Deterministic case analysis.

Everything that must be *right* (which policy applies, day counts, whether the order belongs to the
customer, whether there is a safety hazard) is computed here, not by the model. The model only turns
the result into customer-friendly wording, and the validator checks what it wrote.
"""
import re
from dataclasses import dataclass, field, asdict
from datetime import date

WINDOW_DAYS = 14  # P01 v2: day 14 is included

SAFETY_RE = re.compile(
    r"\b(smok(e|es|ed|ing)|spark(s|ed|ing)?|exposed\s+(live\s+)?wires?|caught\s+fire|on\s+fire|"
    r"burn(s|ed|t|ing)|melt(s|ed|ing)?|electric(al)?\s+shock)\b",
    re.I,
)
INJECTION_RE = re.compile(
    r"(ignore|disregard|override|forget)\b.{0,30}\b(polic(y|ies)|rules?|instructions?)"
    r"|mark\b.{0,30}\bapproved|system prompt|you are now",
    re.I,
)
MODEL_RE = re.compile(r"\b[A-Z]{2,4}-\d{1,4}\b")

# route -> (destination queue, primary policy IDs that must be cited)
ROUTES = {
    "RETURN_REVIEW_OFFER": ("returns_review", ["P01"]),
    "RETURN_MANAGER_DECISION": ("manager_decision", ["P01"]),
    "NEED_INFO": ("agent_followup", ["P01"]),
    "SAFETY_QUEUE": ("safety_queue", ["P02"]),
    "DAMAGE_REVIEW": ("service_lead", ["P02"]),
    "LOGISTICS_FOLLOWUP": ("logistics_followup", ["P03"]),
    "DELIVERY_STATUS": ("agent_followup", ["P03"]),
    "WARRANTY_SERVICE_REVIEW": ("service_review", ["P04"]),
    "VERIFY_REFERENCE": ("agent_followup", ["P05"]),
    "OUT_OF_SCOPE": ("agent_review", []),
}

GUIDE = {
    "RETURN_REVIEW_OFFER": "All P01 conditions are met. Offer to START A RETURN REVIEW. Make clear this is a review request, not a refund decision or approval.",
    "RETURN_MANAGER_DECISION": "A P01 condition is not met. Explain the current policy plainly and say the request has been passed to a manager for a decision. Do not promise an exception or any outcome.",
    "NEED_INFO": "Facts needed to apply P01 are missing. Ask the customer for each missing item. Do not decide eligibility.",
    "SAFETY_QUEUE": "Safety hazard. Tell the customer to stop using the item and not to test it again. Say the report is being referred to the safety team. Give NO repair instructions. Do not promise a replacement or refund. Ask for a photo only if it is safe to take one.",
    "DAMAGE_REVIEW": "Damage report handled under P02, not refused under P01. Ask for any missing details (order reference, delivery date, description, photo if safe). Do not promise a replacement or refund before review.",
    "LOGISTICS_FOLLOWUP": "No verified courier record exists. Say that no verified update is available and that a logistics follow-up has been requested. NEVER give or estimate a delivery date or status.",
    "DELIVERY_STATUS": "Relay ONLY the verified courier status given in the facts. Do not add dates. A delivery enquiry does not authorise a refund.",
    "WARRANTY_SERVICE_REVIEW": "Warranty terms vary by product and supplier and none are on file. Confirm/collect product model and order reference and request a service review. Do NOT state eligibility, duration, or free repair/replacement.",
    "VERIFY_REFERENCE": "The order reference does not match this customer. STOP order-specific advice. Do not mention or show any order details or the reference itself. Ask for a verified order reference.",
    "OUT_OF_SCOPE": "Outside this prototype's scope. Write a short holding reply; a support agent will handle it.",
}


@dataclass
class Analysis:
    ticket_id: str
    topic: str
    route: str
    destination: str
    policy_ids: list
    facts: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    guide: str = ""

    def to_dict(self):
        return asdict(self)


def _date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _yn(value):
    v = (value or "").strip().lower()
    return v if v in ("yes", "no") else "unknown"


def _make(t, topic, route, facts=None, missing=None, reasons=None, flags=None, extra_policies=None):
    destination, primary = ROUTES[route]
    policy_ids = list(primary)
    for p in extra_policies or []:
        if p not in policy_ids:
            policy_ids.append(p)
    return Analysis(
        ticket_id=t.get("ticket_id", ""),
        topic=topic,
        route=route,
        destination=destination,
        policy_ids=policy_ids,
        facts=facts or {},
        missing=missing or [],
        reasons=reasons or [],
        flags=flags or [],
        guide=GUIDE[route],
    )


def analyse(t: dict) -> Analysis:
    topic = (t.get("topic") or "").strip().lower()
    msg = t.get("message", "") or ""
    cust = (t.get("customer_id") or "").strip()
    order_cust = (t.get("order_customer_id") or "").strip()
    order_id = (t.get("order_id") or "").strip()

    flags = []
    mismatch = bool(order_id and order_cust and cust and order_cust != cust)
    injection = bool(INJECTION_RE.search(msg))
    safety = bool(SAFETY_RE.search(msg))
    if mismatch:
        flags.append("ORDER_CUSTOMER_MISMATCH")
    if injection:
        flags.append("INSTRUCTION_OVERRIDE_ATTEMPT")
    if safety:
        flags.append("SAFETY_HAZARD")
    if not order_id:
        flags.append("NO_ORDER_REFERENCE")
    extra = ["P05"] if (injection or mismatch) else []

    # 1. Safety always wins. Order details are withheld if the record does not match.
    if safety:
        facts = {} if mismatch else {"order_reference": order_id}
        missing = [] if _date(t.get("delivered_date")) or mismatch else ["delivery date"]
        return _make(t, topic, "SAFETY_QUEUE", facts, missing, flags=flags, extra_policies=extra)

    # 2. Record mismatch: stop order-specific advice.
    if mismatch:
        return _make(t, topic, "VERIFY_REFERENCE", {}, ["a verified order reference"],
                     ["Order record belongs to a different customer"], flags, extra)

    opened = _date(t.get("opened_date"))
    delivered = _date(t.get("delivered_date"))

    if topic == "return":
        facts, missing, reasons = {"window_days": WINDOW_DAYS}, [], []
        if not opened:
            missing.append("ticket date")
        if delivered and opened:
            days = (opened - delivered).days
            facts["days_since_delivery"] = days
            if days < 0:
                flags.append("DATE_INCONSISTENT")
                missing.append("a confirmed delivery date")
            elif days > WINDOW_DAYS:
                reasons.append(f"the request was made {days} days after delivery, outside the {WINDOW_DAYS}-day window")
        elif not delivered:
            missing.append("the date the item was delivered")
        for key, label_missing, label_no in [
            ("item_unused", "whether the item is unused", "the item has been used"),
            ("original_packaging", "whether it is in its original packaging", "the item is not in its original packaging"),
            ("proof_of_purchase", "proof of purchase", "there is no proof of purchase"),
        ]:
            v = _yn(t.get(key))
            facts[key] = v
            if v == "no":
                reasons.append(label_no)
            elif v == "unknown":
                missing.append(label_missing)
        if reasons:
            return _make(t, topic, "RETURN_MANAGER_DECISION", facts, missing, reasons, flags, extra)
        if missing:
            return _make(t, topic, "NEED_INFO", facts, missing, [], flags, extra)
        return _make(t, topic, "RETURN_REVIEW_OFFER", facts, [], [], flags, extra)

    if topic == "damage":
        missing = []
        if not order_id:
            missing.append("order reference")
        if not delivered:
            missing.append("delivery date")
        return _make(t, topic, "DAMAGE_REVIEW", {"order_reference": order_id}, missing, flags=flags, extra_policies=extra)

    if topic == "delivery":
        status = (t.get("verified_courier_status") or "").strip()
        if not status:
            return _make(t, topic, "LOGISTICS_FOLLOWUP", {"verified_courier_record": "none"}, [],
                         ["No verified courier record"], flags, extra)
        return _make(t, topic, "DELIVERY_STATUS", {"verified_courier_status": status}, [], [], flags, extra)

    if topic == "warranty":
        m = MODEL_RE.search(msg)
        model = m.group(0) if m else ""
        missing = []
        if not model:
            missing.append("product model")
        if not order_id:
            missing.append("order reference")
        return _make(t, topic, "WARRANTY_SERVICE_REVIEW",
                     {"product_model": model, "order_reference": order_id}, missing, flags=flags, extra_policies=extra)

    return _make(t, topic, "OUT_OF_SCOPE", {}, [], ["Topic not covered by this prototype"], flags, extra)
