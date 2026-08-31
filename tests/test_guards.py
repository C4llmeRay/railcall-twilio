# -*- coding: utf-8 -*-
"""Adversarial tests for the refusals the module's listing actually claims.

test_schema.py proves every command works when nothing is wrong. This file
proves the guards refuse when something IS wrong — and for this module that
mostly means "before it spends money it should not have spent".

The segment-arithmetic group is the one to read first. It is not a guard in
the usual sense, it is the calculation the cost ceiling is built on, and if
it is wrong then every ceiling in the module is wrong by the same factor.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(_HERE, os.pardir, "module")

src = open(os.path.join(MOD, "handlers", "handler.py"), encoding="utf-8").read()

ACCOUNT = "AC" + "0" * 32
AUTH_TOKEN = "supersecrettoken" + "b" * 16
VAULT = {"account_sid": ACCOUNT, "auth_token": AUTH_TOKEN,
         "default_from": "+14155550100"}

ns = {
    "__name__": "railcall_module_twilio",
    "__rc_helpers__": {
        "vault_get": lambda p: VAULT,
        "airlock_payload_hash": lambda c, i: "h",
    },
}
exec(compile(src, "handler.py", "exec"), ns)

TO = "+14155550123"
results = []


def scenario(label):
    def deco(fn):
        results.append((label, fn))
        return fn
    return deco


def install(country="US", sms_price="0.0079", voice_price="0.013",
            valid=True, number_price="1.15", participants=1,
            message_body="release 4.2 is live"):
    """Point the handler's single transport seam at a canned, priced world."""
    sent = {}

    def fake_req(method, url, params=None, host=None, timeout=30):
        sent[(method, host or "api")] = dict(params or {})
        if host == "lookups":
            return {"phone_number": TO, "valid": valid,
                    "country_code": country, "national_format": TO,
                    "line_type_intelligence": {}, "validation_errors": []}
        if host == "pricing":
            if "/Messaging/Countries/" in url:
                return {"price_unit": "USD", "outbound_sms_prices": [
                    {"carrier": "a", "prices": [
                        {"number_type": "mobile",
                         "current_price": sms_price}]}]}
            if "/Voice/Numbers/" in url:
                return {"price_unit": "USD", "outbound_call_price": {
                    "current_price": voice_price}}
            if "/PhoneNumbers/Countries/" in url:
                return {"price_unit": "USD", "phone_number_prices": [
                    {"number_type": "local", "current_price": number_price}]}
        if host == "verify":
            return {"sid": "VE" + "1" * 32, "to": TO, "channel": "sms",
                    "status": "pending", "valid": False}
        if host == "conversations":
            if "/Participants" in url:
                if method in ("POST", "DELETE"):
                    return {"sid": "MB" + "1" * 32}
                return {"participants": [
                    {"sid": "MB%d" % i, "identity": "",
                     "messaging_binding": {"address": TO,
                                           "proxy_address": "+14155550100"}}
                    for i in range(participants)]}
            if "/Messages" in url:
                if method == "POST":
                    return {"sid": "IM" + "1" * 32, "body": "x", "index": 1}
                return {"messages": [{"sid": "IM1", "body": "hi", "index": 0}]}
            return {"sid": "CH" + "1" * 32, "friendly_name": "support-1234",
                    "state": "active"}
        if host == "studio":
            return {"sid": "FN" + "1" * 32, "status": "active",
                    "contact_channel_address": TO}
        if "/Messages/" in url:
            return {"sid": "SM" + "1" * 32, "to": TO, "body": message_body,
                    "price": "-0.0079", "price_unit": "USD"}
        if "/Messages.json" in url:
            return {"sid": "SM" + "1" * 32, "to": TO,
                    "from": "+14155550100",
                    "body": (params or {}).get("Body", ""),
                    "status": "queued", "num_segments": "1"}
        if "/Calls.json" in url or "/Calls/" in url:
            return {"sid": "CA" + "1" * 32, "to": TO, "from": "+14155550100",
                    "status": "queued", "duration": "0", "price": "0"}
        if "/Recordings/" in url:
            return {"sid": "RE" + "1" * 32, "call_sid": "CA" + "1" * 32,
                    "duration": "42"}
        if "/IncomingPhoneNumbers/" in url:
            return {"sid": "PN" + "1" * 32, "phone_number": "+14155550100",
                    "friendly_name": "ops"}
        if "/IncomingPhoneNumbers.json" in url:
            return {"sid": "PN" + "1" * 32, "phone_number": "+14155550199",
                    "capabilities": {}}
        return {"sid": ACCOUNT, "status": "active", "type": "Full"}

    ns["_req"] = fake_req
    return sent


def expect_refusal(fn, inputs, must_contain):
    try:
        ns[fn](dict(inputs), None)
    except RuntimeError as e:
        text = str(e)
        missing = [m for m in must_contain if m.lower() not in text.lower()]
        if missing:
            return "message did not mention %s — got: %s" % (missing, text[:240])
        return None
    except Exception as e:
        return "raised %s, expected RuntimeError: %s" % (type(e).__name__, e)
    return "did NOT refuse — the guard let it through"


def expect_ok(fn, inputs):
    try:
        out, _err = ns[fn](dict(inputs), None)
    except Exception as e:
        return "raised %s: %s" % (type(e).__name__, e)
    if not (isinstance(out, dict) and out.get("ok")):
        return "did not return ok=True: %r" % (out,)
    return None


# ── segment arithmetic — the maths every ceiling rests on ─────────────────

@scenario("160 GSM characters is one segment, 161 is two")
def _():
    seg = ns["_segments"]
    if seg("x" * 160)[0] != 1:
        return "160 chars billed as %d segments" % seg("x" * 160)[0]
    if seg("x" * 161)[0] != 2:
        return "161 chars billed as %d segments, should be 2" % seg("x" * 161)[0]
    return None


@scenario("concatenated GSM segments hold 153, not 160")
def _():
    seg = ns["_segments"]
    if seg("x" * 306)[0] != 2:
        return "306 chars -> %d segments, expected 2 (153x2)" % seg("x" * 306)[0]
    if seg("x" * 307)[0] != 3:
        return "307 chars -> %d segments, expected 3" % seg("x" * 307)[0]
    return None


@scenario("one emoji drops the whole message to UCS-2 at 70 per segment")
def _():
    seg = ns["_segments"]
    n, enc, _u = seg("x" * 100)
    if (n, enc) != (1, "GSM-7"):
        return "plain 100 chars -> %d %s" % (n, enc)
    n2, enc2, _u2 = seg("x" * 100 + "\U0001F600")
    if enc2 != "UCS-2":
        return "an emoji did not force UCS-2 (got %s)" % enc2
    if n2 != 2:
        return ("100 chars + emoji -> %d segments; UCS-2 holds 70 per "
                "segment so it must be 2" % n2)
    return None


@scenario("a curly quote forces UCS-2 — the silent word-processor cost")
def _():
    n, enc, _u = ns["_segments"]("please don’t reply")
    if enc != "UCS-2":
        return "a curly apostrophe did not force UCS-2 (got %s)" % enc
    return None


@scenario("GSM extension characters bill as two units each")
def _():
    seg = ns["_segments"]
    _n, enc, units = seg("{}")
    if enc != "GSM-7":
        return "braces are GSM-7 extension chars, got %s" % enc
    if units != 4:
        return "'{}' should cost 4 units (2 escaped chars), got %d" % units
    return None


@scenario("an astral emoji counts as two UTF-16 units, not one")
def _():
    _n, _e, units = ns["_segments"]("\U0001F600")
    if units != 2:
        return "one astral emoji counted as %d units, should be 2" % units
    return None


# ── the cost ceiling ──────────────────────────────────────────────────────

@scenario("send refuses when the priced cost exceeds the ceiling")
def _():
    install(sms_price="0.30")
    return expect_refusal("twilio_send_sms", {
        "to": TO, "body": "hi", "expected_max_cost_usd": 0.01,
    }, ["over the approved ceiling", "0.30"])


@scenario("send proceeds when the priced cost is under the ceiling")
def _():
    install(sms_price="0.0079")
    return expect_ok("twilio_send_sms",
                     {"to": TO, "body": "hi", "expected_max_cost_usd": 0.05})


@scenario("the ceiling is applied to the SEGMENT count, not the message")
def _():
    # 161 GSM chars = 2 segments. At 0.0079 that is 0.0158, over a 0.01 cap
    # that a single-segment assumption would have passed.
    install(sms_price="0.0079")
    return expect_refusal("twilio_send_sms", {
        "to": TO, "body": "x" * 161, "expected_max_cost_usd": 0.01,
    }, ["over the approved ceiling", "2 segment"])


@scenario("a UCS-2 refusal explains why the segment size dropped")
def _():
    install(sms_price="0.0079")
    return expect_refusal("twilio_send_sms", {
        "to": TO, "body": "x" * 100 + "\U0001F600",
        "expected_max_cost_usd": 0.01,
    }, ["UCS-2", "70 characters"])


@scenario("call refuses when per-minute cost times minutes exceeds the ceiling")
def _():
    install(voice_price="0.50")
    return expect_refusal("twilio_place_call", {
        "to": TO, "twiml": "<Response/>", "expected_minutes": 10,
        "expected_max_cost_usd": 1.00,
    }, ["over the approved ceiling", "0.50"])


@scenario("an unpriceable destination with a ceiling set is refused, not passed")
def _():
    def fake_req(method, url, params=None, host=None, timeout=30):
        if host == "lookups":
            return {"valid": True, "country_code": "ZZ", "phone_number": TO}
        if host == "pricing":
            return {"price_unit": "USD", "outbound_sms_prices": []}
        return {}
    ns["_req"] = fake_req
    return expect_refusal("twilio_send_sms", {
        "to": TO, "body": "hi", "expected_max_cost_usd": 1.00,
    }, ["no SMS price", "cannot be bounded"])


# ── expected_segments ─────────────────────────────────────────────────────

@scenario("send refuses when the message splits into more segments than approved")
def _():
    install()
    return expect_refusal("twilio_send_sms", {
        "to": TO, "body": "x" * 200, "expected_segments": 1,
    }, ["bills as 2 segment", "1 was approved", "GSM-7"])


@scenario("expected_segments passes when the count matches")
def _():
    install()
    return expect_ok("twilio_send_sms",
                     {"to": TO, "body": "short", "expected_segments": 1})


# ── the country allowlist — toll fraud ────────────────────────────────────

@scenario("send refuses a destination outside allowed_countries")
def _():
    install(country="LV")
    return expect_refusal("twilio_send_sms", {
        "to": TO, "body": "hi", "allowed_countries": ["US", "CA"],
    }, ["LV", "not in the approved country list", "premium-rate"])


@scenario("send proceeds for a destination inside allowed_countries")
def _():
    install(country="US")
    return expect_ok("twilio_send_sms", {
        "to": TO, "body": "hi", "allowed_countries": ["US", "CA"]})


@scenario("place_call honours allowed_countries too")
def _():
    install(country="SO")
    return expect_refusal("twilio_place_call", {
        "to": TO, "twiml": "<Response/>", "allowed_countries": ["US"],
    }, ["SO", "approved country list"])


@scenario("start_verification honours allowed_countries — a fraud favourite")
def _():
    install(country="LV")
    return expect_refusal("twilio_start_verification", {
        "service_sid": "VA" + "1" * 32, "to": TO,
        "allowed_countries": ["US"],
    }, ["LV", "approved country list"])


@scenario("an invalid destination is refused before it is billed as an attempt")
def _():
    install(valid=False)
    return expect_refusal("twilio_send_sms", {"to": TO, "body": "hi"},
                          ["not a valid phone number", "billed"])


# ── the confirmation flags ────────────────────────────────────────────────

@scenario("buy_phone_number refuses without confirm_recurring_charge")
def _():
    install()
    return expect_refusal("twilio_buy_phone_number", {
        "country": "US", "area_code": "415",
    }, ["RECURRING", "confirm_recurring_charge", "1.15"])


@scenario("buy_phone_number refuses a monthly price over the ceiling")
def _():
    install(number_price="9.99")
    return expect_refusal("twilio_buy_phone_number", {
        "country": "US", "expected_monthly_cost_usd": 2.00,
        "confirm_recurring_charge": True,
    }, ["9.99", "ceiling"])


@scenario("release_phone_number refuses without confirm_unrecoverable")
def _():
    install()
    return expect_refusal("twilio_release_phone_number", {
        "phone_number_sid": "PN" + "1" * 32,
    }, ["CANNOT be reclaimed", "confirm_unrecoverable"])


@scenario("release_phone_number refuses when the SID is a different number")
def _():
    install()
    return expect_refusal("twilio_release_phone_number", {
        "phone_number_sid": "PN" + "1" * 32,
        "expected_phone_number": "+15550009999",
        "confirm_unrecoverable": True,
    }, ["+14155550100", "+15550009999"])


@scenario("place_call refuses recording without consent confirmation")
def _():
    install()
    return expect_refusal("twilio_place_call", {
        "to": TO, "twiml": "<Response/>", "record": True,
    }, ["consent", "confirm_recording_consent"])


@scenario("place_call records when consent is confirmed")
def _():
    install()
    return expect_ok("twilio_place_call", {
        "to": TO, "twiml": "<Response/>", "record": True,
        "confirm_recording_consent": True})


@scenario("delete_conversation refuses without confirm_deletes_messages")
def _():
    install()
    return expect_refusal("twilio_delete_conversation", {
        "conversation_sid": "CH" + "1" * 32,
    }, ["permanently", "confirm_deletes_messages"])


@scenario("trigger_studio_flow refuses without confirm_may_spend_externally")
def _():
    install()
    return expect_refusal("twilio_trigger_studio_flow", {
        "flow_sid": "FW" + "1" * 32, "to": TO,
    }, ["outside every cost guard", "confirm_may_spend_externally"])


@scenario("trigger_studio_flow REFUSES a cost ceiling it cannot honour")
def _():
    install()
    return expect_refusal("twilio_trigger_studio_flow", {
        "flow_sid": "FW" + "1" * 32, "to": TO,
        "confirm_may_spend_externally": True,
        "expected_max_cost_usd": 5.00,
    }, ["cannot be honoured", "a promise nothing enforces"])


@scenario("trigger_studio_flow reports cost_is_unbounded in its receipt")
def _():
    install()
    try:
        out, _e = ns["twilio_trigger_studio_flow"]({
            "flow_sid": "FW" + "1" * 32, "to": TO,
            "confirm_may_spend_externally": True}, None)
    except Exception as e:
        return "raised %s: %s" % (type(e).__name__, e)
    if out.get("cost_is_unbounded") is not True:
        return "did not flag the spend as unbounded"
    return None


# ── identity and payload guards ───────────────────────────────────────────

@scenario("delete_message_record refuses a body hash that does not match")
def _():
    install(message_body="the real message")
    return expect_refusal("twilio_delete_message_record", {
        "message_sid": "SM" + "1" * 32,
        "expected_body_sha256": ns["_sha"]("a completely different message"),
    }, ["not the message that was reviewed"])


@scenario("delete_recording refuses when it belongs to another call")
def _():
    install()
    return expect_refusal("twilio_delete_recording", {
        "recording_sid": "RE" + "1" * 32,
        "expected_call_sid": "CA" + "9" * 32,
    }, ["belongs to call"])


@scenario("conversation send refuses when the participant count moved")
def _():
    install(participants=5)
    return expect_refusal("twilio_send_conversation_message", {
        "conversation_sid": "CH" + "1" * 32, "body": "hi",
        "expected_participant_count": 2,
    }, ["2 participant", "5 now", "separate billed message"])


# ── E.164 and argument validation ─────────────────────────────────────────

@scenario("a number without a leading + is refused with the reason")
def _():
    install()
    return expect_refusal("twilio_send_sms",
                          {"to": "4155550123", "body": "hi"},
                          ["E.164", "leading '+'"])


@scenario("a number with too few digits is refused")
def _():
    install()
    return expect_refusal("twilio_send_sms", {"to": "+123", "body": "hi"},
                          ["7 to 15 digits"])


@scenario("passing both from_number and messaging_service_sid is refused")
def _():
    install()
    return expect_refusal("twilio_send_sms", {
        "to": TO, "body": "hi", "from_number": "+14155550100",
        "messaging_service_sid": "MG" + "1" * 32,
    }, ["not both", "silently ignores"])


@scenario("place_call refuses when neither twiml nor url is given")
def _():
    install()
    return expect_refusal("twilio_place_call", {"to": TO},
                          ["twiml", "url"])


@scenario("place_call refuses a plaintext http url")
def _():
    install()
    return expect_refusal("twilio_place_call",
                          {"to": TO, "url": "http://x.example/twiml"},
                          ["https"])


@scenario("start_verification refuses a Messaging Service SID by name")
def _():
    install()
    return expect_refusal("twilio_start_verification", {
        "service_sid": "MG" + "1" * 32, "to": TO,
    }, ["VA", "MG"])


@scenario("adding an SMS participant without a proxy number is refused")
def _():
    install()
    return expect_refusal("twilio_add_conversation_participant", {
        "conversation_sid": "CH" + "1" * 32, "phone_number": TO,
    }, ["proxy_number is required"])


# ── credentials ───────────────────────────────────────────────────────────

def expect_creds_refusal(entry, must_contain):
    saved = ns["__rc_helpers__"]["vault_get"]
    ns["__rc_helpers__"]["vault_get"] = lambda p: entry
    try:
        ns["_creds"]()
    except RuntimeError as e:
        text = str(e)
        missing = [m for m in must_contain if m.lower() not in text.lower()]
        if missing:
            return "message did not mention %s — got: %s" % (missing, text[:220])
        return None
    except Exception as e:
        return "raised %s, expected RuntimeError: %s" % (type(e).__name__, e)
    finally:
        ns["__rc_helpers__"]["vault_get"] = saved
    return "did NOT refuse — the guard let it through"


@scenario("a Service SID pasted as the account_sid is refused by name")
def _():
    return expect_creds_refusal(
        {"account_sid": "MG" + "1" * 32, "auth_token": "x" * 32},
        ["AC", "Service SID"])


@scenario("half an API key pair is refused rather than silently ignored")
def _():
    return expect_creds_refusal(
        {"account_sid": ACCOUNT, "auth_token": "x" * 32,
         "api_key_sid": "SK" + "1" * 32},
        ["must be set together", "api_key_secret"])


@scenario("an API key pair is PREFERRED over the account auth token")
def _():
    saved = ns["__rc_helpers__"]["vault_get"]
    ns["__rc_helpers__"]["vault_get"] = lambda p: {
        "account_sid": ACCOUNT, "auth_token": "x" * 32,
        "api_key_sid": "SK" + "1" * 32, "api_key_secret": "secret" * 5}
    try:
        c = ns["_creds"]()
    except Exception as e:
        return "raised %s: %s" % (type(e).__name__, e)
    finally:
        ns["__rc_helpers__"]["vault_get"] = saved
    if c["auth_method"] != "api_key":
        return "used %r when a scoped API key was available" % c["auth_method"]
    if c["user"] != "SK" + "1" * 32:
        return "did not authenticate as the API key"
    return None


@scenario("a non-https base_url is refused")
def _():
    return expect_creds_refusal(
        {"account_sid": ACCOUNT, "auth_token": "x" * 32,
         "base_url": "http://api.twilio.com"},
        ["https", "Basic"])


# ── transport: form encoding, redaction, retry ────────────────────────────

def _drive_real_req(responses=None, exc=None, status=None, body=None,
                    capture=None):
    """Run the REAL _req with a faked urlopen."""
    import urllib.error as _ue
    import urllib.request as _u
    import time as _t

    ns2 = {
        "__name__": "railcall_module_twilio",
        "__rc_helpers__": {"vault_get": lambda p: VAULT,
                           "airlock_payload_hash": lambda c, i: "h"},
    }
    exec(compile(src, "handler.py", "exec"), ns2)

    slept, calls = [], {"n": 0}

    class _Resp:
        def __init__(self, b):
            self._b = json.dumps(b).encode()

        def read(self):
            return self._b

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeBody:
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

    class _Hdrs(dict):
        def get(self, k, d=None):
            return dict.get(self, k.lower(), d)

    def fake_urlopen(req, timeout=None):
        if capture is not None:
            capture["url"] = req.full_url
            capture["data"] = req.data
            capture["headers"] = dict(req.headers)
        if exc is not None:
            raise exc(req.full_url)
        if responses is not None:
            i = calls["n"]
            calls["n"] += 1
            item = responses[min(i, len(responses) - 1)]
            if isinstance(item, tuple):
                code, retry_after = item
                raise _ue.HTTPError(
                    req.full_url, code, "err",
                    _Hdrs({"retry-after": str(retry_after)}) if retry_after
                    else _Hdrs({}),
                    _FakeBody(json.dumps({"code": 20429,
                                          "message": "too many"}).encode()))
            return _Resp(item)
        if status is not None:
            raise _ue.HTTPError(req.full_url, status, "err", _Hdrs({}),
                                _FakeBody(json.dumps(body or {}).encode()))
        return _Resp(body or {})

    real_u, real_s = _u.urlopen, _t.sleep
    _u.urlopen = fake_urlopen
    _t.sleep = lambda s: slept.append(s)
    try:
        out = ns2["_req"]("POST", "/2010-04-01/Accounts/%s/Messages.json"
                          % ACCOUNT, params={"To": TO, "Body": "hi",
                                             "MediaUrl": ["a", "b"]})
        return out, slept, calls["n"], None
    except RuntimeError as e:
        return None, slept, calls["n"], str(e)
    finally:
        _u.urlopen, _t.sleep = real_u, real_s


@scenario("write bodies are FORM-encoded, not JSON — Twilio rejects JSON")
def _():
    cap = {}
    _drive_real_req(body={"sid": "SM1"}, capture=cap)
    ct = (cap.get("headers") or {}).get("Content-type") or \
         (cap.get("headers") or {}).get("Content-Type") or ""
    if "x-www-form-urlencoded" not in ct:
        return "Content-Type was %r, not form-encoded" % ct
    data = (cap.get("data") or b"").decode()
    if data.startswith("{"):
        return "body was sent as JSON, which Twilio rejects on writes"
    if "To=" not in data or "Body=hi" not in data:
        return "form body did not carry the parameters: %r" % data[:120]
    return None


@scenario("array parameters are sent as repeated keys, Twilio's form")
def _():
    cap = {}
    _drive_real_req(body={"sid": "SM1"}, capture=cap)
    data = (cap.get("data") or b"").decode()
    if data.count("MediaUrl=") != 2:
        return ("MediaUrl should appear twice for two media items, got %d: %r"
                % (data.count("MediaUrl="), data[:160]))
    return None


@scenario("the auth token never appears in a network error")
def _():
    import urllib.error as _ue
    _o, _s, _n, err = _drive_real_req(
        exc=lambda url: _ue.URLError("failed reaching " + url))
    if err is None:
        return "no error raised"
    if AUTH_TOKEN in err:
        return "THE AUTH TOKEN LEAKED into the error: %s" % err[:200]
    return None


@scenario("errors name the path, never the full URL")
def _():
    _o, _s, _n, err = _drive_real_req(status=400, body={
        "code": 21211, "message": "Invalid 'To'"})
    if err is None:
        return "no error raised"
    if "https://" in err:
        return "the error carried a full URL: %s" % err[:200]
    if "/Messages.json" not in err:
        return "the error did not name the path: %s" % err[:200]
    return None


@scenario("a Twilio error code is glossed with what to actually do")
def _():
    _o, _s, _n, err = _drive_real_req(status=400, body={
        "code": 21408, "message": "Permission to send has not been enabled"})
    if err is None:
        return "no error raised"
    if "geo-permission" not in err.lower():
        return ("error 21408 was not glossed — it is a REGION setting, not a "
                "bad number, and nothing in Twilio's message says so: %s"
                % err[:200])
    return None


@scenario("the trial-account error explains the account state")
def _():
    _o, _s, _n, err = _drive_real_req(status=400, body={
        "code": 21219, "message": "not verified"})
    if err is None or "trial" not in err.lower():
        return "error 21219 did not explain that this is a trial account"
    return None


@scenario("a 429 is retried after Retry-After, then succeeds")
def _():
    _o, slept, n, err = _drive_real_req(responses=[(429, 2), {"sid": "SM1"}])
    if err:
        return "gave up instead of retrying: %s" % err[:160]
    if slept != [2.0]:
        return "did not wait Twilio's Retry-After (slept %r)" % slept
    if n != 2:
        return "expected 2 attempts, made %d" % n
    return None


@scenario("a 500 is NOT retried — the message may already be queued")
def _():
    _o, slept, n, err = _drive_real_req(responses=[(500, 1)])
    if not err:
        return "did not fail"
    if n != 1:
        return ("retried a 5xx (%d attempts) — the send may already have been "
                "queued, and a replay would bill twice" % n)
    return None


def run():
    failed = 0
    for label, fn in results:
        problem = fn()
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
