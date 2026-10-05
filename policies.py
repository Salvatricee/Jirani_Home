"""Loads the policy pack and separates APPROVED CURRENT sources from archived/unapproved ones.

Source selection is done here, in code: archived (A01) and unapproved (U01) documents are never
sent to the model, so the model cannot quote them.
"""
import re
from functools import lru_cache

from config import DATA

HEADER_RE = re.compile(r"^([PAU]\d\d) \| ")


@lru_cache(maxsize=1)
def load_policies() -> dict:
    text = (DATA / "jirani-policy-pack.txt").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[PAU]\d\d \| )", text)
    policies = {}
    for block in blocks:
        lines = block.strip().split("\n")
        if not HEADER_RE.match(lines[0]):
            continue  # preamble
        parts = [p.strip() for p in lines[0].split(" | ")]
        pid, title = parts[0], parts[1]
        header = lines[0]
        approved = "APPROVED CURRENT" in header
        if approved:
            status = "APPROVED CURRENT"
        elif "ARCHIVED" in header:
            status = "ARCHIVED"
        else:
            status = "UNAPPROVED"
        version = next((p for p in parts if re.fullmatch(r"v\d+", p)), "")
        owner = ""
        body_lines = []
        for ln in lines[1:]:
            if ln.startswith("Owner:"):
                owner = ln.split(":", 1)[1].strip()
            else:
                body_lines.append(ln)
        policies[pid] = {
            "id": pid,
            "title": title,
            "version": version,
            "status": status,
            "approved": approved,
            "owner": owner,
            "text": " ".join(body_lines).strip(),
        }
    return policies


def approved_ids() -> set:
    return {pid for pid, p in load_policies().items() if p["approved"]}


def excluded() -> list:
    return [p for p in load_policies().values() if not p["approved"]]


def label(pid: str) -> str:
    p = load_policies().get(pid)
    if not p:
        return f"{pid} (unknown source)"
    return f"{p['id']} · {p['title']} · {p['version']} · {p['status']}"
