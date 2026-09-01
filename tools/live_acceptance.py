# -*- coding: utf-8 -*-
"""Drive ray9/twilio against a REAL Twilio account.

    python tools/live_acceptance.py                    # reads + pricing. COSTS NOTHING.
    python tools/live_acceptance.py --to +14155550123  # prices a real destination
    python tools/live_acceptance.py --to +1... --spend # ACTUALLY SENDS ONE SMS

THE DEFAULT RUN IS FREE. Twilio's Lookup (basic) and Pricing APIs carry no
charge, and neither do any of the list reads — so the entire default pass
costs nothing, which means there is no excuse for not running it before
publishing.

`--spend` sends exactly ONE SMS, to the number you name, with a cost ceiling
set. It reports what it cost. It never buys a number, never places a call,
never starts a verification, never triggers a Studio flow and never releases
anything: those either cost meaningfully more, recur, or cannot be undone,
and a script is the wrong place to exercise them. TESTING.md §4 walks through
them by hand.

The guard checks in the middle are the point of this script. They are all
refusals, and a refusal costs nothing — so they run even without --spend.
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(_HERE, os.pardir)
MOD = os.path.join(ROOT, "module")

VAULT_PATH = os.path.expanduser(
    "~/.railcall/station/.railcall_workspace/credentials.local.json")


def load_handler():
    src = open(os.path.join(MOD, "handlers", "handler.py"),
               encoding="utf-8").read()
    if not os.path.isfile(VAULT_PATH):
        print("No vault at %s — run tools/save_credential.py first."
              % VAULT_PATH)
        sys.exit(1)
    vault = json.load(open(VAULT_PATH, encoding="utf-8"))
    entry = vault.get("twilio")
    if not entry:
        print("No `twilio` credential in the vault — run "
              "tools/save_credential.py first.")
        sys.exit(1)
    creds = entry.get("credentials") or {}
    cid = entry.get("default") or sorted(creds)[0]
    fields = (creds.get(cid) or {}).get("fields") or {}

    ns = {
        "__name__": "railcall_module_twilio",
        "__rc_helpers__": {
            "vault_get": lambda p: fields,
            "airlock_payload_hash": lambda c, i: __import__("hashlib").sha256(
                (c + json.dumps(i, sort_keys=True)).encode()).hexdigest(),
        },
    }
    exec(compile(src, "handler.py", "exec"), ns)
    return ns


PASS, FAIL, SKIP = "ok  ", "FAIL", "skip"
results = []


# Every command name this run actually invoked. A refusal counts: the command
# ran and declined. What does NOT count is a command nobody called at all —
# a pass count measures what ran, not what exists, and reading the first as
# the second overstates the module's state.
EXECUTED = set()


def coverage():
    """Which declared commands never ran? Report it rather than be asked."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, os.pardir, "module", "module.json")
    declared = [c["id"].split(".", 1)[1]
                for c in json.load(open(path, encoding="utf-8"))["commands"]]
    missed = [c for c in declared if c not in EXECUTED]
    print()
    print("COVERAGE: %d/%d commands executed against the live API"
          % (len(declared) - len(missed), len(declared)))
    if missed:
        print("  never executed: %s" % ", ".join(missed))
        print("  a passing run does NOT mean these work — nothing called them.")
    return missed


def step(label, fn, optional=False):
    try:
        out = fn()
    except Exception as e:
        if optional:
            results.append((SKIP, label, str(e)[:160]))
            return None
        results.append((FAIL, label,
                        "%s: %s" % (type(e).__name__, str(e)[:200])))
        return None
    results.append((PASS, label, ""))
    return out


def expect_refusal(label, fn, must_mention=()):
    try:
        fn()
    except RuntimeError as e:
        text = str(e)
        missing = [m for m in must_mention if m.lower() not in text.lower()]
        if missing:
            results.append((FAIL, label, "refused, but did not mention %s: %s"
                            % (missing, text[:160])))
        else:
            results.append((PASS, label, ""))
        return
    except Exception as e:
        results.append((FAIL, label, "raised %s, expected RuntimeError: %s"
                        % (type(e).__name__, str(e)[:160])))
        return
    results.append((FAIL, label, "did NOT refuse — the guard is not working "
                                 "against live data"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--to", help="a destination number in E.164 to price "
                                 "against (no charge unless --spend)")
    ap.add_argument("--spend", action="store_true",
                    help="ACTUALLY send one SMS to --to")
    ap.add_argument("--max-cost", type=float, default=0.05,
                    help="ceiling for the --spend send (default 0.05)")
    args = ap.parse_args()

    if args.spend and not args.to:
        print("--spend requires --to")
        return 1

    ns = load_handler()

    def call(name, inputs):
        EXECUTED.add(name)
        out, _err = ns["twilio_" + name](inputs, None)
        return out

    print("=" * 70)
    print("READS AND PRICING — no charge")
    print("=" * 70)

    cred = step("verify_credential", lambda: call("verify_credential", {}))
    if cred:
        print("     account:  %s (%s)"
              % (cred.get("friendly_name"), cred.get("account_sid")))
        print("     type:     %s   auth: %s"
              % (cred.get("type"), cred.get("auth_method")))
        if cred.get("is_trial"):
            print("     TRIAL ACCOUNT — sends only reach numbers verified in "
                  "the Console (error 21219 otherwise)")
        if cred.get("auth_method") == "auth_token":
            print("     NOTE: using the account auth token. An API key (SK…) "
                  "is scoped and individually revocable.")

    bal = step("get_balance", lambda: call("get_balance", {}))
    if bal:
        print("     balance:  %s %s" % (bal.get("balance"), bal.get("currency")))

    step("get_usage", lambda: call("get_usage", {"limit": 5}), optional=True)
    step("list_messages", lambda: call("list_messages", {"limit": 5}))
    step("list_calls", lambda: call("list_calls", {"limit": 5}))
    step("list_phone_numbers", lambda: call("list_phone_numbers", {}))
    step("list_recordings", lambda: call("list_recordings", {"limit": 5}),
         optional=True)
    step("list_messaging_services",
         lambda: call("list_messaging_services", {}), optional=True)
    step("list_conversations", lambda: call("list_conversations", {}),
         optional=True)
    step("list_studio_flows", lambda: call("list_studio_flows", {}),
         optional=True)
    step("search_available_numbers", lambda: call(
        "search_available_numbers", {"country": "US", "limit": 3}),
        optional=True)

    if not args.to:
        print()
        print("     (pass --to +1... to exercise pricing and the guards)")
        return report()

    print()
    print("=" * 70)
    print("THE SEGMENT ARITHMETIC — the most valuable live check")
    print("=" * 70)

    plain = step("price_message (plain 100 chars)", lambda: call(
        "price_message", {"to": args.to, "body": "x" * 100}))
    emoji = step("price_message (same 100 chars + one emoji)", lambda: call(
        "price_message", {"to": args.to, "body": "x" * 100 + "\U0001F600"}))
    if plain and emoji:
        print("     plain: %d segment(s) %s at %s/segment -> %s %s"
              % (plain["segments"], plain["encoding"],
                 plain["price_per_segment"], plain["estimated_cost"],
                 plain["currency"]))
        print("     emoji: %d segment(s) %s -> %s %s"
              % (emoji["segments"], emoji["encoding"],
                 emoji["estimated_cost"], emoji["currency"]))
        if plain["encoding"] != "GSM-7" or plain["segments"] != 1:
            results.append((FAIL, "plain text prices as 1 GSM-7 segment",
                            "got %d %s" % (plain["segments"],
                                           plain["encoding"])))
        elif emoji["encoding"] != "UCS-2" or emoji["segments"] != 2:
            results.append((FAIL, "one emoji doubles the segment count",
                            "got %d %s — the whole cost model rests on this"
                            % (emoji["segments"], emoji["encoding"])))
        else:
            results.append((PASS, "one emoji doubles the segment count", ""))
        if plain["is_high_cost"]:
            print("     NOTE: %s is a HIGH-COST destination (%s/segment)"
                  % (plain["country"], plain["price_per_segment"]))

    step("price_call", lambda: call("price_call",
                                    {"to": args.to, "minutes": 1}))
    step("lookup_number", lambda: call("lookup_number",
                                       {"phone_number": args.to}))

    print()
    print("=" * 70)
    print("GUARDS — all refusals, all free")
    print("=" * 70)

    if plain:
        expect_refusal(
            "send refuses a ceiling below the live price",
            lambda: call("send_sms", {
                "to": args.to, "body": "acceptance probe",
                "expected_max_cost_usd": 0.0000001}),
            ["over the approved ceiling"])

    expect_refusal(
        "send refuses when the body splits into more segments than approved",
        lambda: call("send_sms", {
            "to": args.to, "body": "x" * 200, "expected_segments": 1}),
        ["bills as 2 segment", "1 was approved"])

    expect_refusal(
        "send refuses a destination outside allowed_countries",
        lambda: call("send_sms", {
            "to": args.to, "body": "probe",
            "allowed_countries": ["ZZ"]}),
        ["not in the approved country list"])

    expect_refusal(
        "buy_phone_number refuses without confirm_recurring_charge",
        lambda: call("buy_phone_number", {"country": "US"}),
        ["RECURRING", "confirm_recurring_charge"])

    expect_refusal(
        "place_call refuses recording without consent confirmation",
        lambda: call("place_call", {
            "to": args.to, "twiml": "<Response/>", "record": True}),
        ["consent"])

    expect_refusal(
        "trigger_studio_flow refuses a cost ceiling it cannot honour",
        lambda: call("trigger_studio_flow", {
            "flow_sid": "FW" + "0" * 32, "to": args.to,
            "confirm_may_spend_externally": True,
            "expected_max_cost_usd": 1.00}),
        ["cannot be honoured"])

    expect_refusal(
        "a number without a leading + is refused",
        lambda: call("send_sms", {"to": args.to.lstrip("+"), "body": "x"}),
        ["E.164"])

    if not args.spend:
        print()
        print("     (pass --spend to actually send one SMS to %s)" % args.to)
        return report()

    print()
    print("=" * 70)
    print("SPEND — one real SMS")
    print("=" * 70)
    import time
    body = "railcall acceptance probe %d" % int(time.time())
    sent = step("send_sms (ceiling %.4f)" % args.max_cost,
                lambda: call("send_sms", {
                    "to": args.to, "body": body,
                    "expected_max_cost_usd": args.max_cost,
                    "expected_segments": 1}))
    if sent:
        print("     sid:  %s" % sent.get("message_sid"))
        print("     cost: %s %s (%d segment, %s)"
              % (sent.get("estimated_cost"), sent.get("currency"),
                 sent.get("num_segments"), sent.get("encoding")))
        step("get_message (delivery status)",
             lambda: call("get_message",
                          {"message_sid": sent["message_sid"]}))

    return report()


def report():
    print()
    print("=" * 70)
    ok = sum(1 for r in results if r[0] == PASS)
    bad = sum(1 for r in results if r[0] == FAIL)
    skipped = sum(1 for r in results if r[0] == SKIP)
    for status, label, note in results:
        print("  %s  %s%s" % (status, label, ("  — " + note) if note else ""))
    print()
    print("%d passed, %d failed, %d skipped" % (ok, bad, skipped))
    coverage()
    if bad:
        print("\nDo not publish with live failures. If pricing failed, check "
              "the account is not restricted; if a send failed, check geo "
              "permissions (error 21408) before assuming a code bug.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
