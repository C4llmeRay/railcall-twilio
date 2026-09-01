# -*- coding: utf-8 -*-
"""Check tools/save_credential.py actually stores what the module reads.

WHY THIS FILE EXISTS. save_credential.py shipped as an unmodified copy of the
Slack module's version: it prompted for a `bot_token` starting with `xoxb-`
and wrote `bot_token` / `default_channel` into the vault, while the Twilio
handler reads `account_sid`, `auth_token` and `api_key_sid`. It would have
refused every real Twilio credential.

Nothing caught it because the script is interactive, so no suite touched it,
and "the module is fully tested" quietly meant "every part of it that a test
could reach". The fix is not more care — it is a test that reaches it. The
prompts are driven here with scripted input, so the whole flow runs headless.

The load-bearing assertion is `test_fields_match_credential_spec`: whatever
the script writes must be exactly what module.json declares. That is the one
that fails on a copied-from-another-provider script, whatever else is right.
"""
import getpass as _getpass
import importlib.util
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(_HERE, os.pardir)

results = []


def scenario(label):
    def deco(fn):
        results.append((label, fn))
        return fn
    return deco


def load_script():
    """Import save_credential.py without running it."""
    path = os.path.join(ROOT, "tools", "save_credential.py")
    spec = importlib.util.spec_from_file_location("sc_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def spec_fields():
    path = os.path.join(ROOT, "module", "module.json")
    cs = json.load(open(path, encoding="utf-8"))["credential_spec"]
    return list(cs.get("required") or []), list(cs.get("optional") or [])


def drive(answers, secrets):
    """Run prompt_and_store() headless, and return (rc, stored_fields).

    `answers` feeds input(), `secrets` feeds getpass(). Both are consumed in
    order; running out means the script asked something unexpected, which is
    itself a failure worth seeing.
    """
    mod = load_script()
    seen_prompts = []
    a, s = list(answers), list(secrets)
    captured = {}

    def fake_input(prompt=""):
        seen_prompts.append(prompt)
        if not a:
            raise AssertionError("script asked for more input than expected: "
                                 "%r" % prompt)
        return a.pop(0)

    def fake_getpass(prompt=""):
        seen_prompts.append(prompt)
        if not s:
            raise AssertionError("script asked for more secrets than "
                                 "expected: %r" % prompt)
        return s.pop(0)

    def fake_save(data):
        entry = data["twilio"]
        cid = entry["default"]
        captured.update(entry["credentials"][cid]["fields"])

    mod.__dict__["input"] = fake_input
    mod.__dict__["getpass"] = type("g", (), {"getpass": staticmethod(fake_getpass)})
    mod.save = fake_save
    mod.load = lambda: {}
    mod.os = os

    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        rc = mod.prompt_and_store()
    finally:
        sys.stdout = real_stdout
    return rc, captured, buf.getvalue(), seen_prompts


GOOD_ACCOUNT = "AC" + "0" * 32
GOOD_KEY = "SK" + "1" * 32
GOOD_MG = "MG" + "2" * 32
GOOD_TOKEN = "f" * 32
GOOD_SECRET = "e" * 32


# ── the assertion that would have caught the shipped bug ───────────────────

@scenario("the fields written are exactly those module.json declares")
def _():
    required, optional = spec_fields()
    rc, fields, out, _p = drive(
        answers=[GOOD_ACCOUNT, GOOD_KEY, "+14155550100", GOOD_MG],
        secrets=[GOOD_TOKEN, GOOD_SECRET])
    if rc != 0:
        return "prompt_and_store returned %r; output: %s" % (rc, out[-300:])
    unknown = [k for k in fields if k not in required + optional]
    if unknown:
        return ("wrote fields the module never reads: %s — this is what a "
                "script copied from another provider looks like" % unknown)
    missing = [k for k in required if k not in fields]
    if missing:
        return "did not store required field(s): %s" % missing
    return None


@scenario("a minimal run stores only the two required fields")
def _():
    required, _optional = spec_fields()
    rc, fields, out, _p = drive(
        answers=[GOOD_ACCOUNT, "", "", ""], secrets=[GOOD_TOKEN])
    if rc != 0:
        return "returned %r; output: %s" % (rc, out[-300:])
    if sorted(fields) != sorted(required):
        return "expected exactly %s, got %s" % (sorted(required), sorted(fields))
    return None


@scenario("no other provider's vocabulary survives in the file")
def _():
    """Look for artefacts, not for the word "Slack".

    The docstring compares Twilio's risk to Slack's on purpose, and that is
    prose, not a leftover. What matters is whether another provider's
    *token shapes and field names* are still in here — those are the things
    that make the script wrong rather than merely chatty.
    """
    path = os.path.join(ROOT, "tools", "save_credential.py")
    text = io.open(path, encoding="utf-8").read().lower()
    strays = [w for w in ("xoxb", "xoxp", "xapp", "oauth & permissions",
                          "default_channel", "bot user oauth", "bot_token")
              if w in text]
    if strays:
        return ("still contains %s — the file was copied from another "
                "provider and not fully adapted" % strays)
    return None


# ── refusals ───────────────────────────────────────────────────────────────

@scenario("half an API key pair is refused, not silently stored")
def _():
    rc, fields, out, _p = drive(
        answers=[GOOD_ACCOUNT, GOOD_KEY, "", ""],
        secrets=[GOOD_TOKEN, ""])
    if rc == 0:
        return "accepted a key SID with no secret"
    if "fall back" not in out.lower():
        return ("refused, but did not explain that it would fall back to the "
                "auth token: %s" % out[-200:])
    if fields:
        return "refused but still wrote %s" % sorted(fields)
    return None


@scenario("an API key SID pasted into the account_sid slot is named")
def _():
    mod = load_script()
    problem = mod.check_sid(GOOD_KEY, "AC", "account_sid")
    if not problem:
        return "accepted an SK… SID as the account SID"
    if "api key" not in problem.lower():
        return "refused without saying what it actually is: %s" % problem
    return None


@scenario("the auth token slot refuses a SID pasted into it")
def _():
    rc, _f, out, _p = drive(answers=[GOOD_ACCOUNT, "", "", ""],
                            secrets=[GOOD_MG])
    if rc == 0:
        return "accepted a Messaging Service SID as the auth token"
    if "auth token" not in out.lower():
        return "refused unhelpfully: %s" % out[-200:]
    return None


@scenario("the account SID pasted twice is caught")
def _():
    rc, _f, out, _p = drive(answers=[GOOD_ACCOUNT, "", "", ""],
                            secrets=[GOOD_ACCOUNT])
    if rc == 0:
        return "accepted the account SID as the auth token"
    return None


@scenario("a non-E.164 default_from is rejected before it is stored")
def _():
    # The first two answers for default_from are bad, the third is good;
    # the loop must keep asking rather than store a malformed number.
    rc, fields, out, _p = drive(
        answers=[GOOD_ACCOUNT, "", "14155550100", "+1 415 555 0100",
                 "+14155550100", ""],
        secrets=[GOOD_TOKEN])
    if rc != 0:
        return "returned %r; output: %s" % (rc, out[-300:])
    if fields.get("default_from") != "+14155550100":
        return "stored %r" % fields.get("default_from")
    if "e.164" not in out.lower():
        return "never explained the E.164 rule"
    return None


@scenario("a bad account SID is re-prompted, not stored")
def _():
    rc, fields, out, _p = drive(
        answers=["not-a-sid", GOOD_ACCOUNT, "", "", ""],
        secrets=[GOOD_TOKEN])
    if rc != 0:
        return "returned %r; output: %s" % (rc, out[-300:])
    if fields.get("account_sid") != GOOD_ACCOUNT:
        return "stored %r" % fields.get("account_sid")
    return None


@scenario("no secret is ever printed")
def _():
    rc, _f, out, _p = drive(
        answers=[GOOD_ACCOUNT, GOOD_KEY, "", ""],
        secrets=[GOOD_TOKEN, GOOD_SECRET])
    if rc != 0:
        return "returned %r" % rc
    for name, secret in (("auth_token", GOOD_TOKEN),
                         ("api_key_secret", GOOD_SECRET)):
        if secret in out:
            return "%s appeared in the output in full" % name
    return None


@scenario("the E.164 pattern rejects a leading zero and accepts +44")
def _():
    mod = load_script()
    bad = [n for n in ("+0155501", "14155550100", "+1 415 555 0100")
           if mod.E164_RE.match(n)]
    if bad:
        return "accepted %s" % bad
    if not mod.E164_RE.match("+441632960001"):
        return "rejected a valid UK number"
    return None


def run():
    failed = 0
    for label, fn in results:
        try:
            problem = fn()
        except Exception as e:
            problem = "%s: %s" % (type(e).__name__, e)
        if problem:
            failed += 1
            print("  FAIL  %s" % label)
            print("        %s" % problem)
        else:
            print("  ok    %s" % label)
    print("\n%d scenarios, %d failed" % (len(results), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
