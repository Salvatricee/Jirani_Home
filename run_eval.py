"""Batch evaluation. Runs every ticket through rules -> drafting -> validator and writes an
evaluation-record.csv in the format supplied with the case.

    python run_eval.py                                  # dev tickets, drafting mode from env (auto)
    python run_eval.py --mode template                  # offline templates only
    python run_eval.py --tickets final.csv --expected final_expected.csv --out eval-final.csv

The 'reviewer' column is set to 'auto-eval'. A HUMAN must still read the drafts and confirm/override
the result. 'cost' records token counts when a model is used; add prices yourself.
"""
import argparse
import csv

import config
import drafting
import policies
import rules
import validator

UNSUPPORTED = {"UNAUTHORISED_PROMISE", "OUTDATED_POLICY_REFERENCE", "UNSUPPORTED_WARRANTY_CLAIM",
               "UNSUPPORTED_DELIVERY_DATE", "ORDER_DETAIL_LEAK", "FOLLOWED_CUSTOMER_OVERRIDE", "REPAIR_INSTRUCTION"}
COLUMNS = ["test_id", "ticket_id", "version", "model", "expected_outcome", "actual_outcome", "correct_source",
           "unsupported_claim", "safe_next_step", "reviewer", "elapsed_minutes", "cost", "evidence", "result"]


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickets", default=str(config.DATA / "jirani-development-tickets.csv"))
    ap.add_argument("--expected", default=str(config.DATA / "dev_expected.csv"))
    ap.add_argument("--mode", default=config.DRAFT_MODE, choices=["auto", "llm", "template"])
    ap.add_argument("--out", default="evaluation-record-dev.csv")
    ap.add_argument("--drafts", default="", help="optional path to write full draft texts for human review")
    args = ap.parse_args()

    expects_model = args.mode == "llm" or (args.mode == "auto" and drafting.llm_available())
    expected = {r["ticket_id"]: r for r in read(args.expected)}
    approved = policies.approved_ids()
    rows, drafts_out, passed = [], [], 0

    for n, t in enumerate(read(args.tickets), 1):
        exp = expected.get(t["ticket_id"])
        a = rules.analyse(t)
        d = drafting.generate(t, a, mode=args.mode)
        issues = validator.check(d["draft"], a, t, d["policy_ids"])
        blocks = validator.blocking(issues)
        exp_route = exp["expected_route"] if exp else ""
        exp_pol = exp["expected_policy_ids"].split(";") if exp else []
        correct_source = all(p in d["policy_ids"] for p in exp_pol) and all(p in approved for p in d["policy_ids"])
        unsupported = any(i["code"] in UNSUPPORTED for i in issues)
        safe = (a.route == exp_route) and not blocks if exp else False
        ok = correct_source and not unsupported and safe
        not_model = expects_model and d["mode"] != "llm"   # model was expected but a template was used
        if not_model:
            ok = False
        passed += ok
        rows.append({
            "test_id": f"T{n:02d}", "ticket_id": t["ticket_id"], "version": config.APP_VERSION,
            "model": d["model"] + ("" if d["mode"] in ("llm", "template") else f" [{d['mode']}]"),
            "expected_outcome": (exp["expected_outcome"] if exp else "NO EXPECTED OUTCOME SUPPLIED"),
            "actual_outcome": f"route={a.route}; destination={a.destination}; cited={','.join(d['policy_ids'])}",
            "correct_source": "yes" if correct_source else "no",
            "unsupported_claim": "yes" if unsupported else "no",
            "safe_next_step": "yes" if safe else "no",
            "reviewer": "auto-eval (human must confirm)",
            "elapsed_minutes": round(d["latency_s"] / 60, 3),
            "cost": d.get("usage", "") or "n/a (no model call)",
            "evidence": (("MODEL CALL FAILED, TEMPLATE USED: " + d["error"][:150] + " | ") if not_model else "")
                        + (("NOTE: " + d["error"][:150] + " | ") if d["mode"] == "llm" and d["error"] else "")
                        + ("; ".join(f"{i['code']}({i['severity']})" for i in issues) or "no validator issues"),
            "result": "NOT-MODEL-OUTPUT" if not_model else ("pass" if ok else "FAIL"),
        })
        drafts_out.append(f"=== {t['ticket_id']} [{d['mode']}] ===\n{d['draft']}\n")
        print(f"{t['ticket_id']}  {a.route:<26} {rows[-1]['result']:<17} {rows[-1]['evidence'][:110]}")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    if args.drafts:
        open(args.drafts, "w", encoding="utf-8").write("\n".join(drafts_out))
    print(f"\n{passed}/{len(rows)} passed automatic checks -> {args.out}")
    if expects_model and any(r["result"] == "NOT-MODEL-OUTPUT" for r in rows):
        print("WARNING: some drafts were NOT written by the model (call failed). Fix the cause (key, model name, "
              "quota) and re-run before using these results as evidence.")


if __name__ == "__main__":
    main()
