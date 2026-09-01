# -*- coding: utf-8 -*-
"""Drive everything that works with NO Twilio account.

    python tools/offline_probe.py

No account, no credential, no network, no cost. The transport is replaced
with a function that raises if anything reaches it, so this cannot make a
request even by accident — and any command that tries is reported as needing
an account rather than quietly passing.

WHAT THIS CAN AND CANNOT TELL YOU. It covers the guards that decide before
the network: argument shapes, E.164, the confirmation flags, and the whole
segment calculation. Those are real code paths with real answers, and a
refusal here is the same refusal a live run would produce.

It cannot tell you anything about live pricing, geo permissions, trial-
account behaviour, carrier filtering, or whether a number is reachable.
Those need a real account and are listed at the end so the gap stays
visible instead of being implied away.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(_HERE, os.pardir)
MOD = os.path.join(ROOT, "module")

E164 = "+14155550123"


class NetworkTouched(Exception):
    """Raised if a command tries to reach Twilio. Nothing here may."""


def load_handler():
    """Load the handler with a fake credential and a transport that refuses.

    The credential is syntactically valid and completely fake. It exists so
    _creds() passes its shape checks; nothing authenticates with it because
    nothing gets as far as a request.
    """
    src = open(os.path.join(MOD, "handlers", "handler.py"),
               encoding="utf-8").read()
    ns = {
        "__name__": "railcall_module_twilio",
        "__rc_helpers__": {
            "vault_get": lambda p: {"account_sid": "AC" + "0" * 32,
                                    "auth_token": "f" * 32},
            "airlock_payload_hash": lambda c, i: "0" * 64,
        },
    }
    exec(compile(src, "handler.py", "exec"), ns)

    ns["_req"] = _refuse_everything
    return ns


def _refuse_everything(*a, **k):
    raise NetworkTouched("a request would have gone to Twilio")


results = []
EXECUTED = set()


def with_canned_reads(ns, payload):
    """Allow GETs, answered from a fixture; still refuse every write.

    Some confirmation guards deliberately read the resource first so the
    refusal can name what is about to be destroyed — "+14155550100 goes back
    to Twilio's pool" rather than an opaque PN… SID. That read is free and
    changes nothing, and the better error is worth it. It does mean the guard
    cannot be reached without answering a GET, so one is answered here from a
    fixture. Nothing leaves the machine either way.
    """
    def transport(method, path, params=None, body=None, host=None, **k):
        if str(method).upper() != "GET":
            raise NetworkTouched("a %s would have been sent to Twilio" % method)
        return dict(payload)
    ns["_req"] = transport


def expect_refusal(label, ns, cmd, inputs, must_mention=()):
    EXECUTED.add(cmd)
    try:
        ns["twilio_" + cmd](dict(inputs), None)
    except NetworkTouched:
        results.append(("FAIL", label,
                        "reached the network — this guard cannot be verified "
                        "without an account, so it does not belong here"))
        return
    except RuntimeError as e:
        text = str(e)
        missing = [m for m in must_mention if m.lower() not in text.lower()]
        if missing:
            results.append(("FAIL", label, "refused but did not mention %s: %s"
                            % (missing, text[:150])))
        else:
            results.append(("ok  ", label, ""))
        return
    except Exception as e:
        results.append(("FAIL", label, "raised %s: %s"
                        % (type(e).__name__, str(e)[:140])))
        return
    results.append(("FAIL", label, "did NOT refuse"))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ns = load_handler()

    print("=" * 70)
    print("NUMBER SHAPES — refused before any request")
    print("=" * 70)
    for cmd in ("send_sms", "send_whatsapp", "place_call", "price_message",
                "price_call", "trigger_studio_flow"):
        extra = {}
        if cmd == "place_call":
            extra = {"twiml": "<Response/>"}
        if cmd == "trigger_studio_flow":
            extra = {"flow_sid": "FW" + "0" * 32,
                     "confirm_may_spend_externally": True}
        expect_refusal("%s refuses a number with no leading +" % cmd, ns, cmd,
                       dict({"to": "14155550123", "body": "x"}, **extra),
                       ["E.164"])
    expect_refusal("lookup_number refuses a malformed number", ns,
                   "lookup_number", {"phone_number": "555-0123"}, ["E.164"])

    print()
    print("=" * 70)
    print("MONEY CONFIRMATIONS — refused before any request")
    print("=" * 70)
    expect_refusal("buy_phone_number refuses without confirm_recurring_charge",
                   ns, "buy_phone_number", {"country": "US"},
                   ["RECURRING", "confirm_recurring_charge"])
    # These two read the resource first, on purpose, so the refusal can name
    # it. Answer that read from a fixture rather than skip the guard.
    with_canned_reads(ns, {"phone_number": E164, "friendly_name": "probe",
                           "messages": []})
    expect_refusal("release_phone_number refuses without confirm_unrecoverable",
                   ns, "release_phone_number",
                   {"phone_number_sid": "PN" + "0" * 32},
                   ["confirm_unrecoverable"])
    expect_refusal("release_phone_number names the NUMBER, not just the SID",
                   ns, "release_phone_number",
                   {"phone_number_sid": "PN" + "0" * 32}, [E164])
    expect_refusal("delete_conversation refuses without confirm_deletes_messages",
                   ns, "delete_conversation",
                   {"conversation_sid": "CH" + "0" * 32},
                   ["confirm_deletes_messages"])
    ns["_req"] = _refuse_everything
    expect_refusal(
        "trigger_studio_flow refuses a ceiling it cannot honour",
        ns, "trigger_studio_flow",
        {"flow_sid": "FW" + "0" * 32, "to": E164, "from_number": E164,
         "confirm_may_spend_externally": True, "expected_max_cost_usd": 1.0},
        ["cannot be honoured"])

    print()
    print("=" * 70)
    print("ARGUMENT SHAPES — refused before any request")
    print("=" * 70)
    for cmd, field in (("check_verification", "service_sid"),
                       ("start_verification", "service_sid"),
                       ("get_recording", "recording_sid"),
                       ("delete_recording", "recording_sid"),
                       ("get_conversation", "conversation_sid"),
                       ("get_studio_execution", "flow_sid"),
                       ("stop_studio_execution", "flow_sid"),
                       ("update_phone_number", "phone_number_sid")):
        expect_refusal("%s refuses a missing %s" % (cmd, field), ns, cmd, {},
                       [field])

    print()
    print("=" * 70)
    print("THE SEGMENT ARITHMETIC — pure computation, no account needed")
    print("=" * 70)
    seg = ns["_segments"]
    checks = [
        ("x" * 160, 1, "GSM-7"),
        ("x" * 161, 2, "GSM-7"),
        ("x" * 100 + "\U0001F600", 2, "UCS-2"),
        ("please don’t reply", 1, "UCS-2"),
        ("café", 1, "GSM-7"),
    ]
    for body, want_seg, want_enc in checks:
        n, enc, units = seg(body)
        label = ("%d chars -> %d segment(s) %s"
                 % (len(body), want_seg, want_enc))
        if (n, enc) == (want_seg, want_enc):
            results.append(("ok  ", label, ""))
        else:
            results.append(("FAIL", label, "got %d %s" % (n, enc)))
    print("     (full table audit: python tests/test_gsm.py)")

    print()
    print("=" * 70)
    for status, label, note in results:
        print("  %s  %s%s" % (status, label, ("  — " + note) if note else ""))
    bad = sum(1 for r in results if r[0] == "FAIL")
    print()
    print("%d passed, %d failed" % (len(results) - bad, bad))

    declared = [c["id"].split(".", 1)[1] for c in
                json.load(open(os.path.join(MOD, "module.json"),
                               encoding="utf-8"))["commands"]]
    missed = [c for c in declared if c not in EXECUTED]
    print()
    print("COVERAGE: %d/%d commands exercised with no account"
          % (len(declared) - len(missed), len(declared)))
    print()
    print("STILL NEEDS A REAL ACCOUNT — nothing above says anything about these:")
    print("  live pricing (every expected_max_cost_usd decision)")
    print("  geo permissions (error 21408 — a region disabled in the Console)")
    print("  trial-account behaviour (21219)")
    print("  carrier filtering (30007), and whether a number is reachable")
    print("  the %d commands that must call Twilio to decide anything:" % len(missed))
    print("    %s" % ", ".join(missed))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
