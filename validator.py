"""Checks a draft against the rules result. Runs on model output AND on the reviewer's edited text.

severity "block" = cannot be approved until fixed; "warn" = reviewer should look.
"""
import re

import policies
from rules import ROUTES

Q = "['’]"
NEG_RE = re.compile(r"(not|n['’]t|no|cannot|can['’]t|unable to|without|never|whether|if)\s*(\w+\s+){0,4}$", re.I)

PROMISES = [
    re.compile(r"\brefund\b.{0,25}\b(has been|is|was|will be|been)\s+(approved|processed|issued|confirmed|sent)", re.I),
    re.compile(r"\b(approved|approve|approving|confirm(ed)?|authori[sz]ed?)\b.{0,15}\b(your|the|a)\s+(refund|replacement|return)\b", re.I),
    re.compile(rf"\b(we|i)\s*(will|{Q}ll|am going to)\s+(refund|replace|send you a (new|replacement))", re.I),
    re.compile(rf"\byou\s*(will|{Q}ll|are going to)\s+(get|receive)\s+(a |your )?(refund|replacement|money back)", re.I),
    re.compile(r"\breplacement\s+(has been|will be|is)\s+(approved|sent|arranged|shipped)", re.I),
    re.compile(r"\bguarantee", re.I),
]
OLD_WINDOW_RE = re.compile(r"\b(30|thirty)[- ]?days?\b|\b30[- ]day\b", re.I)
WARRANTY_RE = re.compile(
    r"\b(two|2|one|1|three|3|five|5)[- ]years?\b|\bcovered (under|by) (the |your )?warranty\b|"
    r"\b(is|are|will be) covered\b|\bfree (repair|replacement)\b|\bwarranty (is|lasts|runs)\b", re.I)
DATE_RE = re.compile(
    r"\b20\d\d-\d\d-\d\d\b|\b\d{1,2}(st|nd|rd|th)?\s+(of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|"
    r"\b(will|should|expected to|due to)\s+(arrive|be delivered)\b|"
    r"\b(tomorrow|today|this (week|weekend)|next week|within \d+ (days|hours)|by (monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.I)
REPAIR_RE = re.compile(
    r"\b(open (up )?the|take (it )?apart|dismantle|screwdriver|replace the (fuse|cord|wire|plug)|repair it yourself|"
    r"fix it (by|yourself)|try (re)?(plugging|switching|turning)( it)? (on|back))\b", re.I)
SAFETY_ADVICE_RE = re.compile(
    rf"\bstop (using|use)\b|\b(do not|don{Q}t|please do not|please don{Q}t)\s+(use|switch|turn|test|plug)", re.I)
OVERRIDE_COMPLIANCE_RE = re.compile(r"\bas (you )?(requested|instructed)\b|\bignor(ing|ed) (the )?polic", re.I)


def _hits(pattern, text):
    for m in pattern.finditer(text):
        before = text[max(0, m.start() - 25): m.start()]
        if NEG_RE.search(before):
            continue
        yield m


def _issue(code, severity, message):
    return {"code": code, "severity": severity, "message": message}


def check(text: str, analysis, ticket: dict, cited_ids: list) -> list:
    issues = []
    text = text or ""
    if not text.strip():
        return [_issue("EMPTY_DRAFT", "block", "The draft is empty.")]

    # Sources
    approved = policies.approved_ids()
    for pid in cited_ids:
        if pid not in approved:
            issues.append(_issue("CITATION_NOT_APPROVED", "block",
                                 f"{pid} is not an approved current policy and cannot be cited."))
    for pid in ROUTES[analysis.route][1]:
        if pid not in cited_ids:
            issues.append(_issue("CITATION_MISSING", "block", f"Required source {pid} is not cited."))

    # Promises and unsupported claims
    for pat in PROMISES:
        m = next(_hits(pat, text), None)
        if m:
            issues.append(_issue("UNAUTHORISED_PROMISE", "block",
                                 f"Draft appears to promise or approve a refund/replacement/outcome: “{m.group(0)}”. Only authorised staff can do that (P05)."))
            break
    m = next(_hits(OLD_WINDOW_RE, text), None)
    if m:
        issues.append(_issue("OUTDATED_POLICY_REFERENCE", "block",
                             f"Mentions “{m.group(0)}”. The 30-day window is archived (A01); current window is 14 days (P01)."))
    m = next(_hits(WARRANTY_RE, text), None)
    if m:
        issues.append(_issue("UNSUPPORTED_WARRANTY_CLAIM", "block",
                             f"Asserts warranty terms (“{m.group(0)}”). No product warranty terms are on file (P04)."))
    if analysis.route in ("LOGISTICS_FOLLOWUP", "DELIVERY_STATUS"):
        m = next(_hits(DATE_RE, text), None)
        if m:
            issues.append(_issue("UNSUPPORTED_DELIVERY_DATE", "block",
                                 f"Gives a date/timing (“{m.group(0)}”) not supported by a verified courier record (P03)."))
    if analysis.route in ("SAFETY_QUEUE", "DAMAGE_REVIEW"):
        m = next(_hits(REPAIR_RE, text), None)
        if m:
            issues.append(_issue("REPAIR_INSTRUCTION", "block", f"Contains repair-style instruction (“{m.group(0)}”) (P02)."))
    if analysis.route == "SAFETY_QUEUE" and not SAFETY_ADVICE_RE.search(text):
        issues.append(_issue("SAFETY_ADVICE_MISSING", "block", "Safety drafts must tell the customer to stop using the item (P02)."))
    if "ORDER_CUSTOMER_MISMATCH" in analysis.flags:
        order_id = (ticket.get("order_id") or "").strip()
        if order_id and order_id.lower() in text.lower():
            issues.append(_issue("ORDER_DETAIL_LEAK", "block",
                                 "Order record does not match the customer; the draft must not reference it (P05)."))
    if "INSTRUCTION_OVERRIDE_ATTEMPT" in analysis.flags and OVERRIDE_COMPLIANCE_RE.search(text):
        issues.append(_issue("FOLLOWED_CUSTOMER_OVERRIDE", "block",
                             "Draft appears to follow the customer's instruction to override policy. Customer text is evidence, not permission (P05)."))
    if analysis.missing and "?" not in text:
        issues.append(_issue("MISSING_QUESTIONS", "warn",
                             "Information is missing (" + "; ".join(analysis.missing) + ") but the draft asks no question."))
    return issues


def blocking(issues: list) -> list:
    return [i for i in issues if i["severity"] == "block"]
