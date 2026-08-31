# -*- coding: utf-8 -*-
"""Validate and dry-run ray9/twilio-spend-airlock.

Static checks first (references resolve, canvas matches engine spec, DAG is
acyclic), then an actual execution of the whole graph: every transform's
`code` really runs, with command outputs supplied from the same kind of mock
the handler suite uses. A workflow that has never been executed is a diagram.
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(_HERE, os.pardir)

WF = json.load(open(os.path.join(ROOT, "workflow",
                                 "twilio-spend-airlock.json"),
                    encoding="utf-8"))
MANIFEST = json.load(open(os.path.join(ROOT, "module", "module.json"),
                          encoding="utf-8"))

fails = []
engine = WF["engine_spec"]["nodes"]
display = WF["nodes"]
by_id = {n["id"]: n for n in engine}

# ── 1. canvas and engine spec must not drift ──────────────────────────────
if len(display) != len(engine):
    fails.append("canvas has %d nodes, engine_spec has %d"
                 % (len(display), len(engine)))
else:
    for d, e in zip(display, engine):
        if d != e:
            fails.append("canvas node %r drifted from the engine spec"
                         % d.get("id"))

# ── 2. every run() must name a real command ───────────────────────────────
commands = {c["id"] for c in MANIFEST["commands"]}
for n in engine:
    for ref in re.findall(r'run\(\s*"([a-z_.]+)"', n.get("code", "")):
        if ref not in commands:
            fails.append("%s calls unknown command %s" % (n["id"], ref))

# ── 3. every state[...] reference must name an upstream node ──────────────
edges = [(e["from"], e["to"]) for e in WF["edges"]]
upstream = {}
for a, b in edges:
    upstream.setdefault(b, set()).add(a)


def ancestors(nid, seen=None):
    seen = seen if seen is not None else set()
    for p in upstream.get(nid, ()):
        if p not in seen:
            seen.add(p)
            ancestors(p, seen)
    return seen


for n in engine:
    for ref in re.findall(r'state\[\s*"([a-z_]+)"\s*\]', n.get("code", "")):
        if ref not in by_id:
            fails.append("%s reads state[%r], which is not a node"
                         % (n["id"], ref))
        elif ref not in ancestors(n["id"]):
            fails.append("%s reads state[%r], which is not upstream of it — "
                         "it may not have run yet" % (n["id"], ref))

# ── 4. every ctx[...] must be declared in context ─────────────────────────
ctx_keys = set(WF["context"])
for n in engine:
    code = n.get("code", "")
    for ref in re.findall(r'ctx\[\s*"([a-z_]+)"\s*\]', code):
        if ref not in ctx_keys:
            fails.append("%s reads ctx[%r], not declared in context"
                         % (n["id"], ref))
    for ref in re.findall(r'ctx\.get\(\s*"([a-z_]+)"', code):
        if ref not in ctx_keys:
            fails.append("%s reads ctx.get(%r), not declared in context"
                         % (n["id"], ref))

# ── 5. the DAG must be acyclic and fully connected ────────────────────────
for a, b in edges:
    if a not in by_id:
        fails.append("edge from unknown node %r" % a)
    if b not in by_id:
        fails.append("edge to unknown node %r" % b)

WHITE, GREY, BLACK = 0, 1, 2
colour = {n["id"]: WHITE for n in engine}
adj = {}
for a, b in edges:
    adj.setdefault(a, []).append(b)


def visit(nid, path):
    if colour.get(nid) == GREY:
        fails.append("cycle: %s" % " -> ".join(path + [nid]))
        return
    if colour.get(nid) == BLACK:
        return
    colour[nid] = GREY
    for nxt in adj.get(nid, ()):
        visit(nxt, path + [nid])
    colour[nid] = BLACK


for n in engine:
    if colour[n["id"]] == WHITE:
        visit(n["id"], [])

reachable = set()
roots = [n["id"] for n in engine if n["id"] not in upstream]
for r in roots:
    reachable.add(r)
    reachable |= {x for x in adj.get(r, ())}
    stack = list(adj.get(r, ()))
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                stack.append(nxt)
for n in engine:
    if n["id"] not in reachable:
        fails.append("%s is unreachable from any root" % n["id"])

# ── 6. an approval must gate every irreversible command ───────────────────
irreversible = {c["id"] for c in MANIFEST["commands"] if c.get("irreversible")}
approval_nodes = {n["id"] for n in engine if n["type"] == "approval"}
for n in engine:
    called = set(re.findall(r'run\(\s*"([a-z_.]+)"', n.get("code", "")))
    if called & irreversible:
        if not (ancestors(n["id"]) & approval_nodes):
            fails.append("%s calls %s (irreversible) with no approval node "
                         "upstream of it"
                         % (n["id"], ", ".join(sorted(called & irreversible))))

# ── 7. the module dependency must point at this module ────────────────────
dep = WF.get("module_dependency") or {}
if dep.get("id") != MANIFEST["id"]:
    fails.append("module_dependency is %r, module is %r"
                 % (dep.get("id"), MANIFEST["id"]))
if dep.get("minimum_version") != MANIFEST["version"]:
    fails.append("module_dependency pins %r, module is at %r"
                 % (dep.get("minimum_version"), MANIFEST["version"]))


# ── 8. actually execute the graph ─────────────────────────────────────────
PRICE_OUT = {
    "ok": True, "to": "+14155550123", "country": "US", "segments": 1,
    "encoding": "GSM-7", "characters": 19, "price_per_segment": 0.0079,
    "cheapest_carrier_price": 0.0079, "estimated_cost": 0.0079,
    "currency": "USD", "is_high_cost": False,
}
BALANCE_OUT = {"ok": True, "account_sid": "AC" + "0" * 32,
               "balance": 42.19, "currency": "USD", "as_of": ""}
SEND_OUT = {
    "ok": True, "message_sid": "SM" + "1" * 32, "to": "+14155550123",
    "from": "+14155550100", "body": "release 4.2 is live",
    "body_sha256": "deadbeef", "status": "queued", "num_segments": 1,
    "encoding": "GSM-7", "estimated_cost": 0.0079,
    "price_per_segment": 0.0079, "currency": "USD", "country": "US",
}

COMMAND_OUT = {
    "twilio.price_message": PRICE_OUT,
    "twilio.get_balance": BALANCE_OUT,
    "twilio.send_sms": SEND_OUT,
}


def execute(ctx):
    state, approvals, order, seen = {}, [], [], set()

    def topo(nid):
        if nid in seen:
            return
        seen.add(nid)
        for p in sorted(upstream.get(nid, ())):
            topo(p)
        order.append(nid)

    for n in engine:
        topo(n["id"])

    for nid in order:
        n = by_id[nid]
        captured = {}

        def run(action_id, payload):
            captured["run"] = (action_id, payload)
            if action_id not in COMMAND_OUT:
                raise AssertionError("no mock output for %s" % action_id)
            return COMMAND_OUT[action_id]

        def emit(value):
            captured["emit"] = value

        def require_approval(preview):
            approvals.append((nid, preview))
            captured["emit"] = preview

        env = {"ctx": ctx, "state": state, "run": run, "emit": emit,
               "require_approval": require_approval}
        try:
            exec(compile(n["code"], "<%s>" % nid, "exec"), env)
        except Exception as e:
            fails.append("node %s raised %s: %s" % (nid, type(e).__name__, e))
            return state, approvals

        if "emit" in captured:
            state[nid] = captured["emit"]
        elif "run" in captured:
            state[nid] = COMMAND_OUT[captured["run"][0]]
        else:
            state[nid] = {}

    return state, approvals


CTX = {"to": "+14155550123", "message": "release 4.2 is live",
       "max_cost_usd": 0.05, "allowed_countries": ["US"]}
state, approvals = execute(dict(CTX))

if not approvals:
    fails.append("the graph ran without ever asking for approval")
else:
    preview = approvals[0][1]
    for key in ("title", "text", "cost", "segments", "account_balance"):
        if key not in preview:
            fails.append("the approval preview omits %r — an approver cannot "
                         "see what they are approving" % key)
    # The whole point: a DOLLAR FIGURE, not a command name.
    if "USD" not in str(preview.get("cost", "")):
        fails.append("the approval preview does not show a currency amount — "
                     "an approver cannot tell a twentieth of a cent from "
                     "thirty cents")

# The guard values must be DERIVED from the live price, not typed.
assess = state.get("assess") or {}
if assess.get("expected_segments") != PRICE_OUT["segments"]:
    fails.append("expected_segments was not derived from the live pricing call")
if assess.get("estimated_cost") != PRICE_OUT["estimated_cost"]:
    fails.append("the cost shown was not the priced cost")

if "receipt" in state:
    r = state["receipt"]
    for key in ("message_sid", "estimated_cost", "currency", "segments"):
        if key not in r:
            fails.append("the receipt omits %r" % key)

# A multi-segment message must explain ITSELF in the preview, since that is
# the surprise this workflow exists to surface.
COMMAND_OUT["twilio.price_message"] = dict(
    PRICE_OUT, segments=2, encoding="UCS-2", estimated_cost=0.0158)
state2, approvals2 = execute(dict(CTX))
COMMAND_OUT["twilio.price_message"] = PRICE_OUT
if approvals2:
    note = str(approvals2[0][1].get("note") or "")
    if "70" not in note:
        fails.append("a 2-segment UCS-2 message did not explain in the "
                     "approval preview why it split — that surprise is the "
                     "reason this workflow exists")
else:
    fails.append("the multi-segment run never reached an approval")


def run_all():
    print("workflow: %s  (%d nodes, %d edges)"
          % (WF["id"], len(engine), len(WF["edges"])))
    print("executed the graph: %d nodes produced state, %d approval(s)"
          % (len(state), len(approvals)))
    if fails:
        print("\nFAILURES (%d):" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("workflow validates and executes")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
