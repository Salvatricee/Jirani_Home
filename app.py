"""Jirani Home - Support Draft Assistant (classroom prototype). All data is fictional.

Run:  streamlit run app.py
"""
import json

import pandas as pd
import streamlit as st

import config
import db
import drafting
import policies
import rules
import validator
import workflow

st.set_page_config(page_title="Jirani Home · Support Draft Assistant", layout="wide")
db.init()
POL = policies.load_policies()

st.title("Jirani Home · Support Draft Assistant")
st.caption("Classroom prototype · fictional data only · AI output is a DRAFT for staff review (P05) · "
           "no real customers, payments or order changes")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Session")
    reviewer = st.text_input("Reviewer name (required to approve/reject)", key="reviewer")
    mode_choice = st.selectbox("Drafting mode", ["auto", "llm", "template"],
                               help="auto = Gemini if GEMINI_API_KEY is set, else offline templates")
    st.info(drafting.mode_label(mode_choice))
    simulate = st.checkbox("Simulate n8n (do NOT call the webhook)", value=False,
                           help="Outcome is recorded as SIMULATED, never as a real workflow run.")
    st.caption(f"n8n webhook: `{config.N8N_WEBHOOK_URL}`")
    fail_model = st.checkbox("Test: simulate model outage", value=False)
    st.divider()
    st.subheader("Tickets")
    uploaded = st.file_uploader("Load a different ticket CSV (e.g. final evaluation set)", type="csv")
    st.caption(f"App version {config.APP_VERSION}")


@st.cache_data
def load_tickets(source):
    df = pd.read_csv(source, dtype=str).fillna("")
    return {r["ticket_id"]: r for r in df.to_dict("records")}


tickets = load_tickets(uploaded if uploaded else str(config.DATA / "jirani-development-tickets.csv"))

tab_review, tab_records, tab_sources = st.tabs(["Review", "Records", "Policy sources"])


def render_issues(issues):
    if not issues:
        st.success("All automatic checks passed.")
    for i in issues:
        (st.error if i["severity"] == "block" else st.warning)(f"**{i['code']}**: {i['message']}")


# ------------------------------------------------------------------ review tab
with tab_review:
    tid = st.selectbox("Ticket", list(tickets), format_func=lambda k: f"{k} · {tickets[k]['topic']} · {tickets[k]['message'][:60]}")
    t = tickets[tid]
    analysis = rules.analyse(t)

    left, right = st.columns(2)
    with left:
        st.subheader("Ticket")
        st.markdown(f"**Customer message**\n\n> {t['message']}")
        st.json({k: v for k, v in t.items() if k != "message"}, expanded=False)
    with right:
        st.subheader("What the rules checked")
        st.markdown(f"**Route:** `{analysis.route}` → **{analysis.destination}**  \n"
                    f"**Required policy:** {', '.join(analysis.policy_ids) or 'none'}")
        if analysis.facts:
            st.markdown("**Facts:** " + ", ".join(f"{k} = `{v}`" for k, v in analysis.facts.items()))
        if analysis.reasons:
            st.markdown("**Why it can't be a simple yes:** " + "; ".join(analysis.reasons))
        if analysis.missing:
            st.markdown("**Missing information:** " + "; ".join(analysis.missing))
        for f in analysis.flags:
            st.warning(f"Flag: {f}")

    st.divider()
    draft = db.latest_draft(tid)

    if st.button("Generate draft" if not draft else "Regenerate draft (new version)", type="primary"):
        with st.spinner("Drafting…"):
            d = drafting.generate(t, analysis, mode=mode_choice, fail_model=fail_model)
            issues = validator.check(d["draft"], analysis, t, d["policy_ids"])
            db.save_draft(tid, analysis, d, issues, config.APP_VERSION)
        st.rerun()

    if not draft:
        st.info("No draft yet for this ticket. Click **Generate draft**.")
    else:
        cited = json.loads(draft["policy_ids"])
        review = db.latest_review(draft["id"])
        st.subheader(f"Draft v{draft['version']}  ·  {draft['mode']}  ·  {draft['model']}")
        if draft["mode"].startswith("template"):
            st.warning("This draft was NOT written by a model" +
                       (f" (model call failed: {draft['error']})" if draft["error"] else " (template mode)") + ".")
        if draft["mode"] == "llm" and draft["error"]:
            st.info(draft["error"])
        if draft["reviewer_note"]:
            st.caption(f"Reviewer note: {draft['reviewer_note']}")

        # ---- already reviewed: read-only outcome (persists after reopening the app)
        if review:
            st.markdown(f"**Decision:** `{review['action']}` by **{review['reviewer']}** at {review['created_at']}")
            if review["reason"]:
                st.markdown(f"**Reason:** {review['reason']}")
            st.text_area("Final text", review["final_text"], height=220, disabled=True, key=f"final_{draft['id']}")
            ev = db.latest_event(draft["id"])
            if ev:
                label = {"ok": "success", "simulated": "info", "rejected": "error", "failed": "error"}[ev["status"]]
                getattr(st, label)(f"Workflow: **{ev['status'].upper()}**" + (f" · {ev['error']}" if ev["error"] else ""))
                st.json(json.loads(ev["response_json"]) if ev["response_json"] else {}, expanded=False)
                if ev["status"] in ("failed", "rejected") and st.button("Retry workflow"):
                    result = workflow.trigger(json.loads(ev["payload_json"]), simulate)
                    db.save_event(draft["id"], tid, json.loads(ev["payload_json"]), result)
                    st.rerun()
            st.caption("Generate a new draft above if you need another version.")
        else:
            # ---- editable review
            edited = st.text_area("Draft (you can edit before approving)", draft["draft_text"],
                                  height=260, key=f"txt_{draft['id']}")
            issues = validator.check(edited, analysis, t, cited)
            st.markdown("**Sources cited:** " + ("; ".join(policies.label(p) for p in cited) or "none"))
            excl = ", ".join(f"{p['id']} ({p['status'].lower()})" for p in policies.excluded())
            st.caption(f"Never sent to the model or accepted as sources: {excl}")
            render_issues(issues)

            blocking = validator.blocking(issues)
            can_act = bool(reviewer.strip())
            if not can_act:
                st.info("Enter your reviewer name in the sidebar to approve or reject.")
            c1, c2 = st.columns(2)
            reject_reason = c2.text_input("Reason for rejecting", key=f"rej_{draft['id']}")
            if c1.button("✅ Approve → test inbox (simulated send)", disabled=(not can_act or bool(blocking))):
                action = "approved" if edited.strip() == draft["draft_text"].strip() else "edited_and_approved"
                db.save_review(draft["id"], reviewer.strip(), action, edited, "", issues)
                db.add_inbox(draft["id"], tid, edited)
                payload = workflow.build_payload(tid, draft, action, reviewer.strip(), edited)
                db.save_event(draft["id"], tid, payload, workflow.trigger(payload, simulate))
                st.rerun()
            if blocking:
                c1.caption("Approval is blocked until the red issues are fixed in the text.")
            if c2.button("❌ Reject draft", disabled=(not can_act or not reject_reason.strip())):
                db.save_review(draft["id"], reviewer.strip(), "rejected", edited, reject_reason.strip(), issues)
                payload = workflow.build_payload(tid, draft, "rejected", reviewer.strip(), edited)
                db.save_event(draft["id"], tid, payload, workflow.trigger(payload, simulate))
                st.rerun()

# ------------------------------------------------------------------ records tab
with tab_records:
    st.caption("Everything below is stored in SQLite and remains after you close and reopen the app.")
    for name, title in [("drafts", "Drafts (version, model, sources, validator result)"),
                        ("reviews", "Reviews (reviewer, action, final text)"),
                        ("workflow_events", "Workflow events (n8n outcome)"),
                        ("test_inbox", "Test inbox (simulated sends)")]:
        st.subheader(title)
        rows = db.table(name)
        if rows:
            df = pd.DataFrame(rows)
            for big in ("analysis_json", "payload_json"):
                if big in df:
                    df = df.drop(columns=[big])
            st.dataframe(df, width="stretch", hide_index=True)
        else:
            st.write("_Nothing yet._")

# ------------------------------------------------------------------ sources tab
with tab_sources:
    for p in POL.values():
        with st.expander(f"{p['id']} · {p['title']} · {p['status']}"):
            st.write(p["text"])
            if p["owner"]:
                st.caption(f"Owner: {p['owner']}")
            if not p["approved"]:
                st.error("Excluded from drafting. Kept only to test source selection.")
