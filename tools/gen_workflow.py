# -*- coding: utf-8 -*-
"""Generate workflow/twilio-spend-airlock.json.

The companion workflow is its own marketplace listing (ray9/twilio-spend-
airlock) and is NOT part of the signed module bundle — .moduleignore keeps
workflow/ out of the tree.

WHAT THE WORKFLOW IS FOR. The module gives you thirty-eight governed
commands. The workflow gives you the one sequence that makes SPENDING safe:
validate the destination, price it against Twilio's own API, compute the
real segment count, put the actual dollar figure in front of a human, and
only then send — carrying the priced ceiling so the module refuses if
anything moved in between.

The point is that the cost is *derived*, not estimated. An approver asked to
sign off on "send an SMS" has no idea whether that is a twentieth of a cent
or thirty cents, and the difference between those two is the whole reason
this module exists.
"""
import collections
import json
import os

NODES = []
EDGES = []


def node(nid, ntype, code, input_from=None):
    n = collections.OrderedDict()
    n["id"] = nid
    n["type"] = ntype
    if input_from:
        n["input_from"] = input_from
    n["code"] = code
    NODES.append(n)
    return nid


def edge(a, b):
    EDGES.append(collections.OrderedDict([("from", a), ("to", b)]))


node("price", "command", """
# Price the message BEFORE anything is approved. This resolves the country
# via Lookup, fetches Twilio's current price for it, and computes the real
# segment count from the actual body — so nothing downstream is guessing.
run("twilio.price_message", {"to": ctx["to"], "body": ctx["message"]})
""")

node("balance", "command", """
# What is actually left to spend. A send that would overdraw the account
# fails at Twilio with an unhelpful error; seeing the balance in the
# approval turns that into a decision.
run("twilio.get_balance", {})
""")

node("assess", "transform", """
# Turn the price into the two things an approver actually needs: what this
# costs, and whether anything about it is unusual.
p = state["price"]
b = state["balance"]

cost = p["estimated_cost"]
ceiling = ctx.get("max_cost_usd", 0.05)

emit({
    "to": p["to"],
    "country": p["country"],
    "text": ctx["message"],
    "segments": p["segments"],
    "encoding": p["encoding"],
    "characters": p["characters"],
    "estimated_cost": cost,
    "currency": p["currency"],
    "balance": b["balance"],
    "over_ceiling": cost > ceiling,
    "is_high_cost": p["is_high_cost"],
    # Carried into the send so the module re-checks against the same numbers.
    "expected_max_cost_usd": ceiling,
    "expected_segments": p["segments"],
    "allowed_countries": ctx.get("allowed_countries", []),
    "why_multi_segment": (
        "this message is %s, so a segment holds %d characters" % (
            p["encoding"], 70 if p["encoding"] == "UCS-2" else 160)
        if p["segments"] > 1 else ""),
})
""", input_from=["price", "balance"])

node("approve", "approval", """
# The human sees a dollar figure, not a command name. Segment count and
# encoding are shown because they are the reason a short message can cost
# three times what anyone expected.
a = state["assess"]
require_approval({
    "title": "Send SMS to %s (%s)" % (a["to"], a["country"]),
    "text": a["text"],
    "cost": "%.5f %s" % (a["estimated_cost"], a["currency"]),
    "segments": "%d (%s, %d chars)" % (
        a["segments"], a["encoding"], a["characters"]),
    "note": a["why_multi_segment"],
    "high_cost_destination": a["is_high_cost"],
    "account_balance": a["balance"],
})
""", input_from="assess")

node("send", "command", """
# The priced ceiling and segment count ride along from step one. If the
# destination price moved, or the body would now split differently, the
# module refuses rather than spending more than was approved.
a = state["assess"]
run("twilio.send_sms", {
    "to": a["to"],
    "body": a["text"],
    "expected_max_cost_usd": a["expected_max_cost_usd"],
    "expected_segments": a["expected_segments"],
    "allowed_countries": a["allowed_countries"],
})
""", input_from="approve")

node("receipt", "transform", """
# What it actually cost, in terms an auditor can reconcile against the
# Twilio invoice months later.
m = state["send"]
emit({
    "sent": True,
    "message_sid": m["message_sid"],
    "to": m["to"],
    "country": m["country"],
    "body_sha256": m["body_sha256"],
    "segments": m["num_segments"],
    "encoding": m["encoding"],
    "estimated_cost": m["estimated_cost"],
    "currency": m["currency"],
    "status": m["status"],
})
""", input_from="send")

edge("price", "assess")
edge("balance", "assess")
edge("assess", "approve")
edge("approve", "send")
edge("send", "receipt")

DESC = (
    "Takes a single SMS request and drives it end to end through Twilio with "
    "the cost known before anyone approves it: resolve the destination "
    "country, price it against Twilio's own Pricing API, compute the real "
    "billable segment count from the actual message body, read the account "
    "balance, and put a dollar figure in front of a human - then send, "
    "carrying that priced ceiling and segment count so the module refuses if "
    "either moved in between. An approver asked to sign off on 'send an SMS' "
    "cannot tell whether that costs a twentieth of a cent or thirty cents, "
    "and cannot tell that one pasted curly quote just tripled it by dropping "
    "the segment size from 160 characters to 70. This workflow makes both "
    "visible before the money is spent rather than after."
)

workflow = collections.OrderedDict()
workflow["id"] = "ray9/twilio-spend-airlock"
workflow["kind"] = "canvas"
workflow["version"] = "1.0.0"
workflow["status"] = "active"
# Valid marketplace categories: Data, Eng, Finance, HR/IT, Marketing, Ops,
# Revenue, Risk, Success, Support, Governance. Cost control is Finance.
workflow["category"] = "Finance"
workflow["title"] = "Twilio Spend Airlock"
workflow["desc"] = (
    "Prices the message against Twilio's own API, counts the real segments, "
    "shows a human the dollar figure, then sends under that ceiling."
)
workflow["description"] = DESC
workflow["context"] = collections.OrderedDict([
    ("to", ""),
    ("message", ""),
    ("max_cost_usd", 0.05),
    ("allowed_countries", []),
])
workflow["capabilities"] = collections.OrderedDict([
    ("providers", ["twilio"]),
    ("allow_irreversible", True),
    ("max_spend_cents", 100),
])
workflow["nodes"] = NODES
workflow["edges"] = EDGES
workflow["engine_spec"] = collections.OrderedDict([
    ("id", workflow["id"]),
    ("title", workflow["title"]),
    ("nodes", NODES),
    ("context", workflow["context"]),
    ("capabilities", workflow["capabilities"]),
])
workflow["module_dependency"] = collections.OrderedDict([
    ("id", "ray9/twilio"),
    ("minimum_version", "1.0.0"),
])

_HERE = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(_HERE, os.pardir, "workflow", "twilio-spend-airlock.json")
with open(out, "w", encoding="utf-8", newline="\n") as f:
    json.dump(workflow, f, indent=2, ensure_ascii=False)
    f.write("\n")

print("wrote %s" % out)
print("nodes: %d  edges: %d  description: %d chars"
      % (len(NODES), len(EDGES), len(DESC)))
