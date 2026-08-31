"""twilio v1.0.0 — governed Twilio messaging, voice and telephony spend.

Vault entry `twilio`:
    {
      "account_sid":           "AC…",   # required, 'AC' + 32 hex
      "auth_token":            "…",     # required
      "api_key_sid":           "SK…",   # optional, PREFERRED — see below
      "api_key_secret":        "…",     # optional, pairs with api_key_sid
      "default_from":          "+1…",   # optional convenience default
      "messaging_service_sid": "MG…",   # optional convenience default
      "base_url":              "https://api.twilio.com"   # optional
    }

USE AN API KEY IF YOU CAN. The account auth token is the master credential:
it can rotate itself, read every subaccount, and cannot be revoked without
breaking everything else using it. An API key (SK…/secret) is scoped,
individually revocable, and leaves the auth token untouched when it leaks.
This module uses the API key whenever both halves are present and reports
which one it used in `verify_credential.auth_method`, so an operator can see
at a glance which credential is actually in play.

WHAT MAKES THIS MODULE DIFFERENT. Slack and Telegram can embarrass you.
Twilio bills you. Every send, call, verification and phone number is a real
charge, and the spread is enormous — a US SMS segment is a fraction of a
cent while some international destinations are over thirty cents, and
premium-rate ranges exist specifically to drain systems that send without
looking. So the central guard here is not about reach, it is about money,
and it is enforced against Twilio's own prices rather than a guess.

THE COST PIPELINE. Every priced command runs the same three steps before it
spends anything:

    1. resolve the destination country      (Lookup v2, free, cached per call)
    2. price it                             (Pricing API, real current price)
    3. compute the exact billable quantity  (segments for SMS, minutes for voice)

and only then compares against `expected_max_cost_usd`. Because the price
comes from Twilio and the segment count comes from the actual message bytes,
a refusal is arithmetic rather than opinion.

SEGMENTS ARE WHERE THE SURPRISES LIVE. An SMS is billed per 160-character
segment — but only if every character is in the GSM-7 alphabet. One
character outside it (a curly quote pasted from a document, an emoji, an
accented name) switches the whole message to UCS-2, where a segment is
**70 characters**. A 100-character message costs one segment; the same
message with a single emoji costs two; a 161-character message costs two
when the author was sure it was one. `_segments()` implements the real
GSM-7 table including the escape-extension characters that count double, so
the number in the receipt is the number on the invoice.

allowed_countries IS THE TOLL-FRAUD GUARD. The classic attack is not
stealing your token, it is getting your system to send to numbers the
attacker earns revenue from. A destination allowlist turns that from an
open-ended bill into a refusal.

TWILIO'S GEO PERMISSIONS ARE SEPARATE AND WILL BITE YOU. By default a
Twilio account cannot send to most countries at all; error 21408 means the
region is disabled in the Console, not that the number is wrong. That is
glossed explicitly because the message Twilio returns does not say it.

BODIES ARE FORM-ENCODED, NOT JSON. This is the single most common way a
hand-rolled Twilio client fails: the API returns JSON but only accepts
`application/x-www-form-urlencoded` on writes, and a JSON body produces a
confusing 400 about missing required parameters. `_req` encodes correctly in
one place, including the repeated-key form Twilio wants for arrays
(`MediaUrl=a&MediaUrl=b`).

WHAT IS NOT GUARDED, AND SAID PLAINLY. `trigger_studio_flow` starts a flow
that can itself send messages and place calls, through Twilio, outside every
check in this module. Its cost cannot be bounded from here. It is flagged
irreversible, it returns `cost_is_unbounded: true`, and it requires
`confirm_may_spend_externally` — because the honest thing to do with a limit
you cannot enforce is to say so rather than imply one.
"""

import hashlib as _hashlib
import json as _json

_DEFAULT_BASE = "https://api.twilio.com"
_API_VERSION = "2010-04-01"

_HOSTS = {
    "api": "https://api.twilio.com",
    "lookups": "https://lookups.twilio.com",
    "verify": "https://verify.twilio.com",
    "pricing": "https://pricing.twilio.com",
    "conversations": "https://conversations.twilio.com",
    "studio": "https://studio.twilio.com",
    "messaging": "https://messaging.twilio.com",
}

_MAX_LIMIT = 100

# A destination costing more than this per SMS segment, or per voice minute,
# is reported as `is_high_cost`. Not a refusal on its own — it is the number
# that should make a human look twice at an approval preview. Ordinary
# US/CA/UK traffic sits two orders of magnitude below it.
_HIGH_COST_SMS = 0.10
_HIGH_COST_VOICE = 0.25

_RETRY_MAX_ATTEMPTS = 3
_RETRY_MAX_WAIT = 45.0

# The GSM 03.38 basic alphabet. Anything outside this (plus the extension
# table below) forces the whole message to UCS-2 at 70 chars per segment.
_GSM_BASIC = (
    "@£$¥èéùìòÇ\nØø\r"
    "ÅåΔ_ΦΓΛΩΠΨΣΘ"
    "ΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
    "abcdefghijklmnopqrstuvwxyzäöñüà"
)
# These are sent as an escape sequence and therefore bill as TWO characters.
_GSM_EXTENDED = "^{}\\[~]|€"

_ERROR_GLOSS = {
    20003: "authentication failed — check account_sid and auth_token, or the "
           "API key pair if one is configured",
    20404: "the resource does not exist, or belongs to a different account",
    20429: "too many requests — Twilio is throttling this account",
    21211: "the 'To' number is not a valid phone number in E.164 form "
           "(+countrycode then digits, no spaces or punctuation)",
    21214: "the 'To' number is not reachable from this account",
    21219: "TRIAL ACCOUNT — you can only send to numbers verified in the "
           "Console. Verify the destination, or upgrade the account.",
    21408: "your account does not have permission to send to this REGION. "
           "This is a Twilio geo-permission setting, not a bad number — "
           "enable the country under Messaging → Geo permissions in the "
           "Console. Most international destinations are OFF by default.",
    21606: "the 'From' number is not one you own, or is not SMS-capable",
    21610: "that recipient replied STOP and is unsubscribed. Twilio blocks "
           "further messages to them and you cannot override it from the API.",
    21612: "Twilio cannot route to that number from the 'From' you used",
    21614: "the 'To' number is not a valid MOBILE number, so SMS cannot be "
           "delivered to it",
    30003: "the destination handset is unreachable (off, or out of coverage)",
    30007: "the carrier filtered this message as spam",
    30008: "delivery failed for an unknown carrier reason",
    63016: "WhatsApp: outside the 24-hour customer service window, so a "
           "free-form message is not allowed — use an approved template via "
           "content_sid",
    63018: "WhatsApp: rate limited by Meta, not by Twilio",
}


# ── credentials + transport ────────────────────────────────────────────────

def _creds():
    helpers = __rc_helpers__  # noqa: F821
    entry = helpers["vault_get"]("twilio")
    if not isinstance(entry, dict):
        raise RuntimeError(
            "no Twilio credential saved — configure the `twilio` vault entry "
            "with account_sid and auth_token")

    account_sid = str(entry.get("account_sid") or "").strip()
    auth_token = str(entry.get("auth_token") or "").strip()
    key_sid = str(entry.get("api_key_sid") or "").strip()
    key_secret = str(entry.get("api_key_secret") or "").strip()

    if not account_sid:
        raise RuntimeError("Twilio credential missing: account_sid")
    if not account_sid.startswith("AC") or len(account_sid) != 34:
        raise RuntimeError(
            "Twilio account_sid should be 'AC' followed by 32 hex characters "
            "(34 total) — got %d characters starting with %r. The Account SID "
            "is on the Console dashboard; do not paste a Service SID (MG…, "
            "VA…) or an API key SID (SK…) here."
            % (len(account_sid), account_sid[:2]))

    # An API key is scoped and individually revocable; the auth token is the
    # master credential for the whole account. Prefer the key whenever both
    # halves are present.
    if key_sid and key_secret:
        if not key_sid.startswith("SK"):
            raise RuntimeError(
                "Twilio api_key_sid should start with 'SK' — got %r"
                % key_sid[:4])
        user, password, method = key_sid, key_secret, "api_key"
    elif key_sid or key_secret:
        raise RuntimeError(
            "Twilio api_key_sid and api_key_secret must be set together — "
            "only %s is present. Remove it to fall back to the auth token, "
            "or add the other half."
            % ("api_key_sid" if key_sid else "api_key_secret"))
    else:
        if not auth_token:
            raise RuntimeError(
                "Twilio credential missing: auth_token (and no API key pair "
                "is configured)")
        user, password, method = account_sid, auth_token, "auth_token"

    base = str(entry.get("base_url") or _DEFAULT_BASE).strip().rstrip("/")
    if not base.startswith("https://"):
        raise RuntimeError(
            "Twilio base_url must start with https:// (got: %s). The "
            "credential travels as an HTTP Basic header on every request."
            % base[:60])

    return {
        "account_sid": account_sid,
        "user": user,
        "password": password,
        "auth_method": method,
        "base": base,
        "default_from": str(entry.get("default_from") or "").strip(),
        "messaging_service_sid": str(
            entry.get("messaging_service_sid") or "").strip(),
    }


def _redact(text):
    """Strip credentials out of anything that reaches a human.

    Twilio's own error bodies quote the parameters you sent, and a media URL
    or status callback can legitimately contain a credential. Belt and
    braces: the live secrets by value, then anything token-shaped.
    """
    import re as _re
    out = str(text)
    try:
        c = _creds()
        for secret in (c["password"],):
            if secret and len(secret) > 8:
                out = out.replace(secret, "<REDACTED>")
    except Exception:
        pass
    out = _re.sub(r"\bSK[0-9a-fA-F]{32}\b", "SK<REDACTED>", out)
    out = _re.sub(r"://[^:/@\s]+:[^@\s]+@", "://<REDACTED>@", out)
    return out


def _describe(body, status):
    """Turn Twilio's error envelope into one readable, actionable line.

    Shape: {code, message, more_info, status}. The numeric `code` is the
    useful part — Twilio's messages are generic where its codes are precise.
    """
    if not isinstance(body, dict):
        return "HTTP %s" % status
    code = body.get("code")
    msg = str(body.get("message") or "").strip()
    line = "HTTP %s" % status
    if code:
        line += " [%s]" % code
    line += ": " + (msg or "no message")
    try:
        gloss = _ERROR_GLOSS.get(int(code))
    except Exception:
        gloss = None
    if gloss:
        line += " — " + gloss
    return _redact(line)[:700]


def _req(method, url, params=None, host=None, timeout=30):
    """The single outbound chokepoint.

    Bodies are FORM-ENCODED. Twilio returns JSON but accepts only
    application/x-www-form-urlencoded on writes, and a JSON body fails with a
    confusing 400 about missing parameters. Arrays are sent as repeated keys,
    which is the form Twilio expects for MediaUrl and friends.

    Retries a 429 with Retry-After, because Twilio throttles before acting.
    A 5xx is never retried: there the message may already have been queued
    for delivery, and a replay would send it twice — and bill twice.
    """
    import urllib.request as _u
    import urllib.error as _ue
    import urllib.parse as _up
    import base64 as _b64
    import time as _time

    c = _creds()
    if host:
        url = _HOSTS[host] + url
    elif not url.startswith("http"):
        url = c["base"] + url

    pairs = []
    for k, v in (params or {}).items():
        if v is None or v == "":
            continue
        if isinstance(v, (list, tuple)):
            for item in v:
                if item is not None and item != "":
                    pairs.append((k, str(item)))
        elif isinstance(v, bool):
            pairs.append((k, "true" if v else "false"))
        else:
            pairs.append((k, str(v)))
    encoded = _up.urlencode(pairs)

    data = None
    if method in ("POST", "PUT"):
        data = encoded.encode("utf-8")
    elif encoded:
        url += ("&" if "?" in url else "?") + encoded

    basic = _b64.b64encode(
        ("%s:%s" % (c["user"], c["password"])).encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": "Basic " + basic,
        "Accept": "application/json",
        "User-Agent": "RailCall-Station-module-twilio/1.0",
    }
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    waited, attempt = 0.0, 0
    while True:
        attempt += 1
        req = _u.Request(url, data=data, method=method, headers=headers)
        try:
            with _u.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if not raw:
                    return {}
                try:
                    return _json.loads(raw.decode("utf-8"))
                except Exception:
                    raise RuntimeError(_redact(
                        "Twilio %s returned a non-JSON body (HTTP %s): %s"
                        % (method, r.getcode(),
                           raw[:200].decode("utf-8", "replace"))))
        except _ue.HTTPError as e:
            raw = b""
            try:
                raw = e.read()
            except Exception:
                pass
            try:
                body = _json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}

            if e.code == 429:
                retry_after = 0.0
                try:
                    retry_after = float(
                        str(e.headers.get("Retry-After") or "").strip() or 0)
                except Exception:
                    retry_after = 0.0
                if (attempt <= _RETRY_MAX_ATTEMPTS and retry_after > 0
                        and waited + retry_after <= _RETRY_MAX_WAIT):
                    _time.sleep(retry_after)
                    waited += retry_after
                    continue
                raise RuntimeError(_redact(
                    "Twilio %s %s failed — rate limited%s%s"
                    % (method, _path_only(url),
                       "; Twilio asked for %gs" % retry_after
                       if retry_after else "",
                       " (already waited %gs)" % waited if waited else "")))

            raise RuntimeError(_redact(
                "Twilio %s %s failed — %s"
                % (method, _path_only(url), _describe(body, e.code))))
        except _ue.URLError as e:
            raise RuntimeError(_redact(
                "Twilio %s %s failed — network error: %s"
                % (method, _path_only(url), e.reason)))


def _path_only(url):
    """Errors name the path, never the full URL — a URL can carry a token."""
    try:
        import urllib.parse as _up
        return _up.urlparse(url).path
    except Exception:
        return "(url)"


def _acct(path):
    """Build a core-API path under this account."""
    c = _creds()
    return "/%s/Accounts/%s%s" % (_API_VERSION, c["account_sid"], path)


# ── input coercion ─────────────────────────────────────────────────────────

def _req_str(inputs, field):
    v = inputs.get(field)
    if not isinstance(v, str) or not v.strip():
        raise RuntimeError("%s must be a non-empty string" % field)
    return v.strip()


def _opt_str(inputs, field):
    v = inputs.get(field)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _opt_num(inputs, field):
    v = inputs.get(field)
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise RuntimeError("%s must be a number, not a boolean" % field)
    try:
        return float(v)
    except Exception:
        raise RuntimeError("%s must be a number" % field)


def _opt_int(inputs, field):
    v = _opt_num(inputs, field)
    return None if v is None else int(v)


def _opt_bool(inputs, field, default=None):
    v = inputs.get(field)
    if v is None or v == "":
        return default
    return bool(v)


def _opt_list(inputs, field):
    v = inputs.get(field)
    if v is None or v == "":
        return None
    if not isinstance(v, list):
        raise RuntimeError("%s must be an array" % field)
    return v


def _opt_dict(inputs, field):
    v = inputs.get(field)
    if v is None or v == "":
        return None
    if not isinstance(v, dict):
        raise RuntimeError("%s must be an object" % field)
    return v


def _limit(inputs, default=50):
    v = _opt_num(inputs, "limit")
    if v is None:
        return default
    v = int(v)
    if v <= 0:
        raise RuntimeError("limit must be a positive integer")
    return min(v, _MAX_LIMIT)


def _sha(text):
    return _hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _e164(value, field="to"):
    """Validate E.164, because Twilio's own error for a bad one is generic.

    A number without the leading '+' is the single most common cause of
    error 21211, and 'it looked fine' is what everyone says about it.
    """
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("%s must be a non-empty string" % field)
    v = value.strip().replace(" ", "").replace("-", "").replace("(", "") \
        .replace(")", "")
    if v.startswith("whatsapp:"):
        inner = v[len("whatsapp:"):]
        return "whatsapp:" + _e164(inner, field)
    if not v.startswith("+"):
        raise RuntimeError(
            "%s must be in E.164 form — a leading '+' then country code then "
            "digits, e.g. +14155552671. Got %r. Twilio rejects anything else "
            "with a generic 'not a valid phone number'." % (field, v[:24]))
    digits = v[1:]
    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        raise RuntimeError(
            "%s is not a valid E.164 number — expected 7 to 15 digits after "
            "the '+', got %d (%r)" % (field, len(digits), v[:24]))
    return v


def _bare(number):
    """Strip a channel prefix, e.g. whatsapp:+1… -> +1…"""
    return number.split(":", 1)[1] if ":" in number else number


def _ok(payload):
    out = {"ok": True, "loaded_from": "module:twilio"}
    out.update(payload)
    return out, None


# ══════════════════════════════════════════════════════════════════════════
#  THE COST PIPELINE — segments, country, price, ceiling
# ══════════════════════════════════════════════════════════════════════════

def _segments(body):
    """Compute the exact billable segment count and encoding.

    This is the arithmetic that decides the bill, so it implements the real
    GSM 03.38 rules rather than an approximation:

      - if every character is in the GSM-7 alphabet, the message is GSM-7.
        Characters in the extension table (^{}\\[~]|€) are sent as a two-byte
        escape sequence and count DOUBLE.
        A single segment holds 160 units; once it splits, each segment holds
        153, because 7 units go to the concatenation header.

      - one character outside that alphabet switches the ENTIRE message to
        UCS-2, where a single segment holds 70 UTF-16 code units and each
        concatenated segment holds 67. Note "code units", not characters:
        an emoji outside the Basic Multilingual Plane costs two.

    The practical consequences, which are the reason this exists:
      a 161-character plain message costs two segments, not one;
      a 100-character message with one emoji costs two, not one;
      a curly quote pasted from a word processor triples the cost of a
      160-character message.
    """
    body = body or ""
    is_gsm = all((ch in _GSM_BASIC or ch in _GSM_EXTENDED) for ch in body)

    if is_gsm:
        units = sum(2 if ch in _GSM_EXTENDED else 1 for ch in body)
        if units == 0:
            return 1, "GSM-7", 0
        segments = 1 if units <= 160 else -(-units // 153)
        return segments, "GSM-7", units

    # UTF-16 code units: astral characters (most emoji) take two.
    units = sum(2 if ord(ch) > 0xFFFF else 1 for ch in body)
    if units == 0:
        return 1, "UCS-2", 0
    segments = 1 if units <= 70 else -(-units // 67)
    return segments, "UCS-2", units


def _country_of(number):
    """Resolve a phone number's ISO country via Lookup v2.

    Basic Lookup carries no charge — only the optional data packages
    (line_type_intelligence, caller_name) are billed — so this runs freely
    in front of every priced send.
    """
    num = _bare(_e164(number, "to"))
    import urllib.parse as _up
    res = _req("GET", "/v2/PhoneNumbers/" + _up.quote(num), host="lookups")
    if not res.get("valid", True):
        raise RuntimeError(
            "refusing to proceed — Twilio Lookup says %s is not a valid "
            "phone number. Sending to it would be billed as a failed "
            "attempt." % num)
    return (res.get("country_code") or "").upper(), res


def _sms_price(iso_country):
    """The current outbound SMS price for a country, from Twilio's own API.

    Twilio prices per destination carrier, so a country has a range rather
    than a single number. The MAXIMUM is used for the ceiling check: the
    carrier is not known until the message routes, and a guard that assumes
    the cheapest route is a guard that lets the expensive case through.
    """
    res = _req("GET", "/v1/Messaging/Countries/" + iso_country, host="pricing")
    unit = res.get("price_unit", "USD")
    prices = []
    for carrier in (res.get("outbound_sms_prices") or []):
        for p in (carrier.get("prices") or []):
            try:
                prices.append(float(p.get("current_price")))
            except Exception:
                continue
    if not prices:
        raise RuntimeError(
            "Twilio returned no SMS price for country %s, so the cost of "
            "this send cannot be bounded" % iso_country)
    return max(prices), min(prices), unit


def _voice_price(number):
    """The current outbound per-minute price for a destination number."""
    import urllib.parse as _up
    res = _req("GET", "/v1/Voice/Numbers/" + _up.quote(_bare(number)),
               host="pricing")
    unit = res.get("price_unit", "USD")
    p = res.get("outbound_call_price") or {}
    try:
        return float(p.get("current_price")), unit
    except Exception:
        raise RuntimeError(
            "Twilio returned no voice price for %s, so the cost of this call "
            "cannot be bounded" % _bare(number))


def _guard_country(iso_country, inputs, verb):
    """Refuse a destination outside the approved list.

    This is the toll-fraud guard. The attack is not stealing the credential,
    it is persuading a system to send to numbers the attacker collects
    revenue from — so the defence is a destination allowlist, not a stronger
    secret.
    """
    allowed = _opt_list(inputs, "allowed_countries")
    if not allowed:
        return
    want = {str(x).strip().upper() for x in allowed if str(x).strip()}
    if iso_country not in want:
        raise RuntimeError(
            "refusing to %s — the destination resolves to %s, which is not in "
            "the approved country list (%s). Premium-rate and high-cost "
            "international ranges are the usual way an automated sender is "
            "turned into someone else's revenue."
            % (verb, iso_country or "an unknown country",
               ", ".join(sorted(want))))


def _guard_cost(estimated, inputs, verb, detail=""):
    """Refuse a spend over the approved ceiling.

    The ceiling is compared against a price Twilio just quoted and a quantity
    computed from the actual payload, so this is arithmetic rather than an
    estimate. A ceiling that cannot be evaluated is a refusal, never a pass:
    an unpriceable send is exactly the one worth stopping.
    """
    ceiling = _opt_num(inputs, "expected_max_cost_usd")
    if ceiling is None:
        return
    if estimated is None:
        raise RuntimeError(
            "refusing to %s — expected_max_cost_usd was set but the cost "
            "could not be determined, so the ceiling cannot be enforced"
            % verb)
    if float(estimated) > float(ceiling) + 1e-9:
        raise RuntimeError(
            "refusing to %s — the estimated cost is %.4f, over the approved "
            "ceiling of %.4f.%s"
            % (verb, float(estimated), float(ceiling),
               (" " + detail) if detail else ""))


def _guard_segments(segments, inputs):
    """Refuse when the message splits into more segments than approved.

    Separate from the cost ceiling on purpose. Someone approving a message
    is approving a piece of text; if that text silently became three
    segments because of one pasted character, the cost tripled AND the
    recipient may see it arrive as fragments on older handsets.
    """
    expected = _opt_int(inputs, "expected_segments")
    if expected is None:
        return
    if segments != expected:
        raise RuntimeError(
            "refusing to send — this message bills as %d segment(s), but %d "
            "was approved. A single character outside the GSM-7 alphabet (a "
            "curly quote, an emoji, an accent) drops the segment size from "
            "160 characters to 70 and can multiply the cost."
            % (segments, expected))


# ══════════════════════════════════════════════════════════════════════════
#  READS — no approval
# ══════════════════════════════════════════════════════════════════════════

def twilio_verify_credential(inputs, stamp):
    """Smoke test: does this credential work, and what kind of account is it.

    `is_trial` is the field to read first. A trial account can only send to
    numbers verified in the Console and silently fails everything else with
    error 21219, which reads like a bad number rather than an account state.
    """
    c = _creds()
    res = _req("GET", _acct(".json"))
    status = res.get("status", "")
    acct_type = res.get("type", "")
    return _ok({
        "valid": True,
        "account_sid": res.get("sid", c["account_sid"]),
        "friendly_name": res.get("friendly_name", ""),
        "status": status,
        "type": acct_type,
        "is_trial": acct_type.lower() == "trial",
        "auth_method": c["auth_method"],
        "date_created": res.get("date_created", ""),
        "auth_method_note": (
            "using the account auth token — consider an API key (SK…), which "
            "is scoped and individually revocable"
            if c["auth_method"] == "auth_token" else
            "using a scoped API key rather than the account auth token"),
        "trial_note": (
            "TRIAL ACCOUNT: sends only reach numbers verified in the Console; "
            "everything else fails with error 21219"
            if acct_type.lower() == "trial" else ""),
    })


def twilio_get_balance(inputs, stamp):
    """The account's remaining balance — read this before any bulk run."""
    c = _creds()
    res = _req("GET", _acct("/Balance.json"))
    try:
        balance = float(res.get("balance"))
    except Exception:
        balance = None
    return _ok({
        "account_sid": res.get("account_sid", c["account_sid"]),
        "balance": balance,
        "currency": res.get("currency", "USD"),
        "as_of": "",
    })


def twilio_get_usage(inputs, stamp):
    """Spend to date, by category."""
    params = {
        "Category": _opt_str(inputs, "category"),
        "StartDate": _opt_str(inputs, "start_date"),
        "EndDate": _opt_str(inputs, "end_date"),
        "PageSize": _limit(inputs),
    }
    res = _req("GET", _acct("/Usage/Records.json"), params=params)
    records, total = [], 0.0
    currency = "USD"
    for r in (res.get("usage_records") or []):
        try:
            price = float(r.get("price") or 0)
        except Exception:
            price = 0.0
        total += price
        currency = r.get("price_unit") or currency
        records.append({
            "category": r.get("category", ""),
            "description": r.get("description", ""),
            "count": r.get("count"),
            "usage": r.get("usage"),
            "price": price,
            "start_date": r.get("start_date", ""),
            "end_date": r.get("end_date", ""),
        })
    return _ok({
        "records": records,
        "count": len(records),
        "total_price": round(total, 4),
        "currency": currency,
        "period": "%s..%s" % (_opt_str(inputs, "start_date") or "account start",
                              _opt_str(inputs, "end_date") or "today"),
    })


def twilio_price_message(inputs, stamp):
    """Price an SMS WITHOUT sending it.

    The command to run before approving anything. It resolves the country,
    fetches Twilio's current price, and computes the real segment count from
    the actual body — so an approver sees the true cost and the true segment
    split rather than assuming 160 characters and one segment.
    """
    to = _e164(inputs.get("to"), "to")
    body = _opt_str(inputs, "body") or ""
    iso, _lookup = _country_of(to)
    hi, lo, unit = _sms_price(iso)
    segments, encoding, units = _segments(body)
    estimated = round(hi * segments, 5)
    return _ok({
        "to": to,
        "country": iso,
        "segments": segments,
        "encoding": encoding,
        "characters": len(body),
        "billable_units": units,
        "price_per_segment": hi,
        "cheapest_carrier_price": lo,
        "estimated_cost": estimated,
        "currency": unit,
        "is_high_cost": hi > _HIGH_COST_SMS,
        "note": (
            "this message is UCS-2, so a segment holds 70 characters instead "
            "of 160 — one non-GSM character (emoji, curly quote, accent) "
            "does that to the whole message"
            if encoding == "UCS-2" else ""),
    })


def twilio_price_call(inputs, stamp):
    """Price an outbound call per minute WITHOUT placing it."""
    to = _e164(inputs.get("to"), "to")
    minutes = _opt_num(inputs, "minutes")
    minutes = 1.0 if minutes is None else float(minutes)
    iso, _lookup = _country_of(to)
    per_minute, unit = _voice_price(to)
    return _ok({
        "to": to,
        "country": iso,
        "price_per_minute": per_minute,
        "minutes": minutes,
        "estimated_cost": round(per_minute * minutes, 5),
        "currency": unit,
        "is_high_cost": per_minute > _HIGH_COST_VOICE,
    })


def twilio_lookup_number(inputs, stamp):
    """Validate a number and optionally read its carrier and line type.

    Basic lookup is free. `include_carrier` requests the
    line_type_intelligence package, which Twilio BILLS per lookup — so it is
    off by default and the receipt records which kind was performed.
    """
    num = _e164(inputs.get("phone_number"), "phone_number")
    want_carrier = _opt_bool(inputs, "include_carrier", False)
    import urllib.parse as _up
    params = {"Fields": "line_type_intelligence"} if want_carrier else None
    res = _req("GET", "/v2/PhoneNumbers/" + _up.quote(_bare(num)),
               params=params, host="lookups")
    lti = res.get("line_type_intelligence") or {}
    line_type = lti.get("type", "")
    return _ok({
        "phone_number": res.get("phone_number", num),
        "valid": bool(res.get("valid")),
        "country_code": res.get("country_code", ""),
        "national_format": res.get("national_format", ""),
        "carrier_name": lti.get("carrier_name", ""),
        "line_type": line_type,
        "is_mobile": line_type.lower() in ("mobile", "personal"),
        "billed_lookup": bool(want_carrier),
        "validation_errors": res.get("validation_errors") or [],
    })


def twilio_list_messages(inputs, stamp):
    """List messages, with what they actually cost."""
    params = {
        "To": _opt_str(inputs, "to"),
        "From": _opt_str(inputs, "from_number"),
        "DateSent>": _opt_str(inputs, "date_sent_after"),
        "DateSent<": _opt_str(inputs, "date_sent_before"),
        "PageSize": _limit(inputs),
    }
    res = _req("GET", _acct("/Messages.json"), params=params)
    out, total, currency = [], 0.0, "USD"
    for m in (res.get("messages") or []):
        try:
            price = abs(float(m.get("price") or 0))
        except Exception:
            price = 0.0
        total += price
        currency = m.get("price_unit") or currency
        body = m.get("body") or ""
        out.append({
            "message_sid": m.get("sid", ""),
            "to": m.get("to", ""),
            "from": m.get("from", ""),
            "body": body,
            "body_sha256": _sha(body),
            "status": m.get("status", ""),
            "num_segments": m.get("num_segments"),
            "price": price,
            "error_code": m.get("error_code"),
            "date_sent": m.get("date_sent", ""),
        })
    return _ok({
        "messages": out,
        "count": len(out),
        "total_price": round(total, 4),
        "currency": currency,
        "next_page": res.get("next_page_uri") or "",
    })


def twilio_get_message(inputs, stamp):
    """One message, its delivery status and its real cost."""
    sid = _req_str(inputs, "message_sid")
    m = _req("GET", _acct("/Messages/%s.json" % sid))
    body = m.get("body") or ""
    try:
        price = abs(float(m.get("price") or 0))
    except Exception:
        price = None
    code = m.get("error_code")
    gloss = ""
    try:
        gloss = _ERROR_GLOSS.get(int(code), "") if code else ""
    except Exception:
        gloss = ""
    return _ok({
        "message_sid": m.get("sid", sid),
        "to": m.get("to", ""),
        "from": m.get("from", ""),
        "body": body,
        "body_sha256": _sha(body),
        "status": m.get("status", ""),
        "error_code": code,
        "error_message": (m.get("error_message") or "") +
                         ((" — " + gloss) if gloss else ""),
        "num_segments": m.get("num_segments"),
        "price": price,
        "currency": m.get("price_unit") or "USD",
        "date_sent": m.get("date_sent", ""),
    })


def twilio_list_calls(inputs, stamp):
    """List calls, with duration and cost."""
    params = {
        "To": _opt_str(inputs, "to"),
        "From": _opt_str(inputs, "from_number"),
        "Status": _opt_str(inputs, "status"),
        "StartTime>": _opt_str(inputs, "start_time_after"),
        "PageSize": _limit(inputs),
    }
    res = _req("GET", _acct("/Calls.json"), params=params)
    out, total, seconds, currency = [], 0.0, 0, "USD"
    for c in (res.get("calls") or []):
        try:
            price = abs(float(c.get("price") or 0))
        except Exception:
            price = 0.0
        try:
            dur = int(c.get("duration") or 0)
        except Exception:
            dur = 0
        total += price
        seconds += dur
        currency = c.get("price_unit") or currency
        out.append({
            "call_sid": c.get("sid", ""),
            "to": c.get("to", ""),
            "from": c.get("from", ""),
            "status": c.get("status", ""),
            "duration_seconds": dur,
            "price": price,
            "direction": c.get("direction", ""),
            "start_time": c.get("start_time", ""),
        })
    return _ok({
        "calls": out,
        "count": len(out),
        "total_price": round(total, 4),
        "total_minutes": round(seconds / 60.0, 2),
        "currency": currency,
    })


def twilio_get_call(inputs, stamp):
    """One call's status, duration and cost."""
    sid = _req_str(inputs, "call_sid")
    c = _req("GET", _acct("/Calls/%s.json" % sid))
    try:
        price = abs(float(c.get("price") or 0))
    except Exception:
        price = None
    return _ok({
        "call_sid": c.get("sid", sid),
        "to": c.get("to", ""),
        "from": c.get("from", ""),
        "status": c.get("status", ""),
        "duration_seconds": int(c.get("duration") or 0),
        "price": price,
        "currency": c.get("price_unit") or "USD",
        "direction": c.get("direction", ""),
        "answered_by": c.get("answered_by", ""),
        "start_time": c.get("start_time", ""),
        "end_time": c.get("end_time", ""),
    })


def twilio_list_recordings(inputs, stamp):
    """List call recordings."""
    params = {
        "CallSid": _opt_str(inputs, "call_sid"),
        "DateCreated>": _opt_str(inputs, "date_created_after"),
        "PageSize": _limit(inputs),
    }
    res = _req("GET", _acct("/Recordings.json"), params=params)
    out, seconds = [], 0
    for r in (res.get("recordings") or []):
        try:
            dur = int(r.get("duration") or 0)
        except Exception:
            dur = 0
        seconds += dur
        out.append({
            "recording_sid": r.get("sid", ""),
            "call_sid": r.get("call_sid", ""),
            "duration_seconds": dur,
            "channels": r.get("channels"),
            "status": r.get("status", ""),
            "date_created": r.get("date_created", ""),
        })
    return _ok({
        "recordings": out,
        "count": len(out),
        "total_duration": seconds,
    })


def twilio_get_recording(inputs, stamp):
    """One recording's metadata and its media URL.

    The media URL needs the same HTTP Basic credential to fetch, so it is
    not a shareable link — but it also is not self-evidently protected,
    which is why `url_requires_auth` is stated rather than assumed.
    """
    sid = _req_str(inputs, "recording_sid")
    r = _req("GET", _acct("/Recordings/%s.json" % sid))
    c = _creds()
    return _ok({
        "recording_sid": r.get("sid", sid),
        "call_sid": r.get("call_sid", ""),
        "duration_seconds": int(r.get("duration") or 0),
        "channels": r.get("channels"),
        "status": r.get("status", ""),
        "media_url": "%s/%s/Accounts/%s/Recordings/%s.mp3"
                     % (c["base"], _API_VERSION, c["account_sid"],
                        r.get("sid", sid)),
        "url_requires_auth": True,
        "date_created": r.get("date_created", ""),
    })


def twilio_list_phone_numbers(inputs, stamp):
    """The numbers this account owns, and what they cost each month."""
    params = {
        "PhoneNumber": _opt_str(inputs, "phone_number"),
        "FriendlyName": _opt_str(inputs, "friendly_name"),
        "PageSize": _limit(inputs),
    }
    res = _req("GET", _acct("/IncomingPhoneNumbers.json"), params=params)
    out = []
    for p in (res.get("incoming_phone_numbers") or []):
        out.append({
            "phone_number_sid": p.get("sid", ""),
            "phone_number": p.get("phone_number", ""),
            "friendly_name": p.get("friendly_name", ""),
            "capabilities": p.get("capabilities") or {},
            "sms_url": p.get("sms_url", ""),
            "voice_url": p.get("voice_url", ""),
            "status": p.get("status", ""),
        })
    return _ok({
        "phone_numbers": out,
        "count": len(out),
        "estimated_monthly_cost": None,
        "currency": "USD",
        "cost_note": ("per-number monthly price varies by country and type; "
                      "search_available_numbers reports it for a given "
                      "country"),
    })


def twilio_search_available_numbers(inputs, stamp):
    """Search numbers available to buy, with the monthly price.

    The price is fetched separately from the Pricing API, because the
    AvailablePhoneNumbers resource does not include it — which is how people
    end up buying a number without knowing its recurring cost.
    """
    country = _req_str(inputs, "country").upper()
    ntype = (_opt_str(inputs, "number_type") or "Local")
    if ntype.lower() not in ("local", "mobile", "tollfree"):
        raise RuntimeError(
            "number_type must be Local, Mobile or TollFree (got %r)" % ntype)
    ntype = {"local": "Local", "mobile": "Mobile",
             "tollfree": "TollFree"}[ntype.lower()]

    params = {
        "AreaCode": _opt_str(inputs, "area_code"),
        "Contains": _opt_str(inputs, "contains"),
        "SmsEnabled": _opt_bool(inputs, "sms_enabled", None),
        "VoiceEnabled": _opt_bool(inputs, "voice_enabled", None),
        "PageSize": _limit(inputs, default=20),
    }
    res = _req("GET",
               _acct("/AvailablePhoneNumbers/%s/%s.json" % (country, ntype)),
               params=params)

    monthly, currency = None, "USD"
    try:
        pr = _req("GET", "/v1/PhoneNumbers/Countries/" + country, host="pricing")
        currency = pr.get("price_unit", "USD")
        for p in (pr.get("phone_number_prices") or []):
            if str(p.get("number_type", "")).lower() == ntype.lower():
                monthly = float(p.get("current_price"))
                break
    except Exception:
        pass

    out = []
    for a in (res.get("available_phone_numbers") or []):
        out.append({
            "phone_number": a.get("phone_number", ""),
            "friendly_name": a.get("friendly_name", ""),
            "locality": a.get("locality", ""),
            "region": a.get("region", ""),
            "capabilities": a.get("capabilities") or {},
        })
    return _ok({
        "available": out,
        "count": len(out),
        "country": country,
        "number_type": ntype,
        "monthly_price": monthly,
        "currency": currency,
        "cost_note": ("this is a RECURRING monthly charge that continues "
                      "until the number is released"),
    })


def twilio_list_messaging_services(inputs, stamp):
    """List Messaging Services."""
    res = _req("GET", "/v1/Services", params={"PageSize": _limit(inputs)},
               host="messaging")
    out = []
    for s_ in (res.get("services") or []):
        out.append({
            "service_sid": s_.get("sid", ""),
            "friendly_name": s_.get("friendly_name", ""),
            "use_case": s_.get("use_case", ""),
        })
    return _ok({"services": out, "count": len(out)})


def twilio_list_conversations(inputs, stamp):
    """List Conversations."""
    params = {"State": _opt_str(inputs, "state"), "PageSize": _limit(inputs)}
    res = _req("GET", "/v1/Conversations", params=params, host="conversations")
    out = []
    for c in (res.get("conversations") or []):
        out.append({
            "conversation_sid": c.get("sid", ""),
            "friendly_name": c.get("friendly_name", ""),
            "unique_name": c.get("unique_name", ""),
            "state": c.get("state", ""),
            "date_created": c.get("date_created", ""),
        })
    return _ok({"conversations": out, "count": len(out)})


def _conversation_participants(sid):
    res = _req("GET", "/v1/Conversations/%s/Participants" % sid,
               params={"PageSize": _MAX_LIMIT}, host="conversations")
    out = []
    for p in (res.get("participants") or []):
        binding = p.get("messaging_binding") or {}
        out.append({
            "participant_sid": p.get("sid", ""),
            "identity": p.get("identity") or "",
            "phone_number": binding.get("address", ""),
            "proxy_number": binding.get("proxy_address", ""),
        })
    return out


def twilio_get_conversation(inputs, stamp):
    """One Conversation and who is in it."""
    sid = _req_str(inputs, "conversation_sid")
    c = _req("GET", "/v1/Conversations/" + sid, host="conversations")
    participants = _conversation_participants(sid)
    return _ok({
        "conversation_sid": c.get("sid", sid),
        "friendly_name": c.get("friendly_name", ""),
        "state": c.get("state", ""),
        "participant_count": len(participants),
        "participants": participants,
        "date_created": c.get("date_created", ""),
    })


def twilio_list_conversation_messages(inputs, stamp):
    """Messages inside a Conversation."""
    sid = _req_str(inputs, "conversation_sid")
    res = _req("GET", "/v1/Conversations/%s/Messages" % sid,
               params={"PageSize": _limit(inputs)}, host="conversations")
    out = []
    for m in (res.get("messages") or []):
        body = m.get("body") or ""
        out.append({
            "message_sid": m.get("sid", ""),
            "author": m.get("author", ""),
            "body": body,
            "body_sha256": _sha(body),
            "index": m.get("index"),
            "date_created": m.get("date_created", ""),
        })
    return _ok({
        "conversation_sid": sid,
        "messages": out,
        "count": len(out),
    })


def twilio_list_studio_flows(inputs, stamp):
    """List Studio flows."""
    res = _req("GET", "/v2/Flows", params={"PageSize": _limit(inputs)},
               host="studio")
    out = []
    for f in (res.get("flows") or []):
        out.append({
            "flow_sid": f.get("sid", ""),
            "friendly_name": f.get("friendly_name", ""),
            "status": f.get("status", ""),
            "revision": f.get("revision"),
        })
    return _ok({"flows": out, "count": len(out)})


def twilio_get_studio_execution(inputs, stamp):
    """One Studio flow execution."""
    flow = _req_str(inputs, "flow_sid")
    ex = _req_str(inputs, "execution_sid")
    e = _req("GET", "/v2/Flows/%s/Executions/%s" % (flow, ex), host="studio")
    return _ok({
        "flow_sid": flow,
        "execution_sid": e.get("sid", ex),
        "status": e.get("status", ""),
        "contact_channel_address": e.get("contact_channel_address", ""),
        "date_created": e.get("date_created", ""),
        "date_updated": e.get("date_updated", ""),
    })


# ══════════════════════════════════════════════════════════════════════════
#  MESSAGING — approval-gated
# ══════════════════════════════════════════════════════════════════════════

def _resolve_from(inputs, c, allow_service=True):
    """Pick the sender, and refuse ambiguity rather than guessing."""
    frm = _opt_str(inputs, "from_number")
    svc = _opt_str(inputs, "messaging_service_sid")
    if not frm and not svc:
        frm = c["default_from"] or None
        svc = c["messaging_service_sid"] or None if allow_service else None
    if not frm and not svc:
        raise RuntimeError(
            "no sender — pass from_number or messaging_service_sid, or set "
            "default_from in the twilio vault entry")
    if frm and svc:
        raise RuntimeError(
            "pass either from_number or messaging_service_sid, not both — "
            "Twilio picks the service and silently ignores the number, which "
            "means the message goes out from a sender nobody approved")
    return frm, svc


def twilio_send_sms(inputs, stamp):
    """Send an SMS or MMS. Irreversible: delivered and billed.

    The full cost pipeline runs before anything is sent — country, price,
    segments, allowlist, ceiling — so a refusal costs nothing and a send is
    already known to be within budget.
    """
    c = _creds()
    to = _e164(inputs.get("to"), "to")
    body = _req_str(inputs, "body")
    media = _opt_list(inputs, "media_urls")
    frm, svc = _resolve_from(inputs, c)

    segments, encoding, units = _segments(body)
    _guard_segments(segments, inputs)

    iso, _lookup = _country_of(to)
    _guard_country(iso, inputs, "send")

    per_segment, cheapest, currency = _sms_price(iso)
    estimated = round(per_segment * segments, 5)
    _guard_cost(
        estimated, inputs, "send",
        "%d segment(s) of %s at %.5f %s each, to %s."
        % (segments, encoding, per_segment, currency, iso)
        + (" This message is UCS-2, so a segment is 70 characters rather "
           "than 160." if encoding == "UCS-2" else ""))

    params = {
        "To": to,
        "Body": body,
        "MediaUrl": media,
        "StatusCallback": _opt_str(inputs, "status_callback"),
    }
    if svc:
        params["MessagingServiceSid"] = svc
    else:
        params["From"] = frm

    m = _req("POST", _acct("/Messages.json"), params=params, timeout=45)
    return _ok({
        "message_sid": m.get("sid", ""),
        "to": m.get("to", to),
        "from": m.get("from", frm or ""),
        "body": m.get("body", body),
        "body_sha256": _sha(body),
        "status": m.get("status", ""),
        "num_segments": int(m.get("num_segments") or segments),
        "encoding": encoding,
        "estimated_cost": estimated,
        "price_per_segment": per_segment,
        "currency": currency,
        "country": iso,
        "messaging_service_sid": svc or "",
    })


def twilio_send_whatsapp(inputs, stamp):
    """Send a WhatsApp message. Irreversible: delivered and billed.

    WhatsApp has a rule with no SMS equivalent: outside a 24-hour window
    from the recipient's last message, only an approved TEMPLATE may be
    sent. A free-form body fails with error 63016. Passing `content_sid`
    uses a template; without one this is a free-form message and will only
    land inside the window.
    """
    c = _creds()
    to_raw = str(inputs.get("to") or "")
    to = _e164(to_raw if to_raw.startswith("whatsapp:")
               else "whatsapp:" + to_raw, "to")
    body = _req_str(inputs, "body")
    content_sid = _opt_str(inputs, "content_sid")

    frm = _opt_str(inputs, "from_number") or c["default_from"]
    if not frm:
        raise RuntimeError(
            "no sender — pass from_number (a WhatsApp-enabled Twilio number) "
            "or set default_from in the vault entry")
    if not frm.startswith("whatsapp:"):
        frm = "whatsapp:" + _e164(frm, "from_number")

    iso, _lookup = _country_of(to)
    _guard_country(iso, inputs, "send a WhatsApp message")

    # WhatsApp is priced per conversation rather than per segment, and the
    # Pricing API does not expose it. Rather than invent a number, the SMS
    # price for the destination is used as a floor for the ceiling check and
    # labelled as such — an approximation that is stated is usable; one that
    # is silent is not.
    estimated, currency = None, "USD"
    try:
        per_segment, _lo, currency = _sms_price(iso)
        estimated = round(per_segment, 5)
    except Exception:
        pass
    _guard_cost(estimated, inputs, "send a WhatsApp message",
                "WhatsApp is billed per 24-hour conversation, which the "
                "Pricing API does not expose; this ceiling was checked "
                "against the destination's SMS price as a floor.")

    params = {"To": to, "From": frm, "Body": body,
              "MediaUrl": _opt_list(inputs, "media_urls")}
    if content_sid:
        params["ContentSid"] = content_sid
        cv = _opt_dict(inputs, "content_variables")
        if cv:
            params["ContentVariables"] = _json.dumps(cv)

    m = _req("POST", _acct("/Messages.json"), params=params, timeout=45)
    return _ok({
        "message_sid": m.get("sid", ""),
        "to": m.get("to", to),
        "from": m.get("from", frm),
        "body": m.get("body", body),
        "body_sha256": _sha(body),
        "status": m.get("status", ""),
        "estimated_cost": estimated,
        "currency": currency,
        "country": iso,
        "used_template": bool(content_sid),
        "cost_is_approximate": True,
        "window_note": (
            "no template was used, so this only delivers inside the 24-hour "
            "customer service window; outside it Twilio returns error 63016"
            if not content_sid else ""),
    })


def twilio_delete_message_record(inputs, stamp):
    """Delete a message from Twilio's logs. Irreversible.

    This destroys the record of a message that really was sent and really
    was billed — the recipient still has it, and the charge still appears on
    the invoice. What is lost is your ability to prove what you sent.
    """
    sid = _req_str(inputs, "message_sid")
    m = _req("GET", _acct("/Messages/%s.json" % sid))
    body = m.get("body") or ""
    actual = _sha(body)

    expected = _opt_str(inputs, "expected_body_sha256")
    if expected and actual != expected.strip().lower():
        raise RuntimeError(
            "refusing to delete — the message body hashes to %s… but %s… was "
            "approved. This is not the message that was reviewed."
            % (actual[:12], expected.strip()[:12]))

    try:
        price = abs(float(m.get("price") or 0))
    except Exception:
        price = None

    _req("DELETE", _acct("/Messages/%s.json" % sid))
    return _ok({
        "message_sid": sid,
        "deleted": True,
        "was_to": m.get("to", ""),
        "was_body_sha256": actual,
        "was_price": price,
        "note": ("the recipient still has this message and the charge still "
                 "appears on the invoice — only your record of it is gone"),
    })


# ══════════════════════════════════════════════════════════════════════════
#  VOICE — approval-gated
# ══════════════════════════════════════════════════════════════════════════

def twilio_place_call(inputs, stamp):
    """Place an outbound call. Irreversible and billed per minute.

    A call's cost is open-ended in a way a message's is not: it bills until
    somebody hangs up. `expected_minutes` is what the ceiling is evaluated
    against, and `timeout_seconds` bounds how long Twilio rings before
    giving up.

    RECORDING IS A LEGAL QUESTION, NOT A TECHNICAL ONE. Many jurisdictions
    require all-party consent, and a recording made without it is both a
    liability and inadmissible. `record: true` therefore requires
    `confirm_recording_consent` in the approved payload.
    """
    c = _creds()
    to = _e164(inputs.get("to"), "to")
    frm = _opt_str(inputs, "from_number") or c["default_from"]
    if not frm:
        raise RuntimeError(
            "no caller ID — pass from_number, or set default_from in the "
            "twilio vault entry")
    frm = _e164(frm, "from_number")

    twiml = _opt_str(inputs, "twiml")
    url = _opt_str(inputs, "url")
    if not twiml and not url:
        raise RuntimeError(
            "pass either twiml (inline instructions) or url (a webhook "
            "returning TwiML) — Twilio needs to know what the call should do "
            "once it connects")
    if twiml and url:
        raise RuntimeError("pass either twiml or url, not both")
    if url and not url.startswith("https://"):
        raise RuntimeError(
            "url must be https:// — Twilio fetches call instructions from it, "
            "so plaintext lets anyone on the path control the call")

    record = _opt_bool(inputs, "record", False)
    if record and not _opt_bool(inputs, "confirm_recording_consent", False):
        raise RuntimeError(
            "refusing to place the call — recording is enabled, and many "
            "jurisdictions require every party to consent. Set "
            "confirm_recording_consent: true in the approved payload to "
            "confirm that consent exists.")

    iso, _lookup = _country_of(to)
    _guard_country(iso, inputs, "call")

    per_minute, currency = _voice_price(to)
    minutes = _opt_num(inputs, "expected_minutes")
    minutes = 1.0 if minutes is None else float(minutes)
    estimated = round(per_minute * minutes, 5)
    _guard_cost(
        estimated, inputs, "place the call",
        "%.4f %s/minute to %s over %g expected minute(s). A call bills until "
        "it ends, so set timeout_seconds and expected_minutes deliberately."
        % (per_minute, currency, iso, minutes))

    params = {
        "To": to,
        "From": frm,
        "Twiml": twiml,
        "Url": url,
        "Record": record,
        "Timeout": _opt_int(inputs, "timeout_seconds"),
        "MachineDetection": _opt_str(inputs, "machine_detection"),
    }
    call = _req("POST", _acct("/Calls.json"), params=params, timeout=45)
    return _ok({
        "call_sid": call.get("sid", ""),
        "to": call.get("to", to),
        "from": call.get("from", frm),
        "status": call.get("status", ""),
        "price_per_minute": per_minute,
        "estimated_cost": estimated,
        "expected_minutes": minutes,
        "currency": currency,
        "country": iso,
        "recording_enabled": bool(record),
    })


def twilio_hangup_call(inputs, stamp):
    """End an in-progress call. Stops the meter."""
    sid = _req_str(inputs, "call_sid")
    before = ""
    try:
        before = (_req("GET", _acct("/Calls/%s.json" % sid))
                  .get("status", ""))
    except Exception:
        pass
    call = _req("POST", _acct("/Calls/%s.json" % sid),
                params={"Status": "completed"})
    try:
        price = abs(float(call.get("price") or 0))
    except Exception:
        price = None
    return _ok({
        "call_sid": sid,
        "status": call.get("status", ""),
        "was_status": before,
        "duration_seconds": int(call.get("duration") or 0),
        "price": price,
    })


def twilio_delete_recording(inputs, stamp):
    """Delete a call recording permanently.

    Irreversible, and worth pausing over in both directions: a recording may
    be evidence you are required to keep, or personal data you are required
    to erase. This module cannot tell which, so it records what was deleted.
    """
    sid = _req_str(inputs, "recording_sid")
    r = _req("GET", _acct("/Recordings/%s.json" % sid))
    call_sid = r.get("call_sid", "")

    expected = _opt_str(inputs, "expected_call_sid")
    if expected and call_sid != expected.strip():
        raise RuntimeError(
            "refusing to delete — recording %s belongs to call %s, not the "
            "%s that was approved." % (sid, call_sid or "(unknown)",
                                       expected.strip()))

    _req("DELETE", _acct("/Recordings/%s.json" % sid))
    return _ok({
        "recording_sid": sid,
        "deleted": True,
        "was_call_sid": call_sid,
        "was_duration_seconds": int(r.get("duration") or 0),
    })


# ══════════════════════════════════════════════════════════════════════════
#  PHONE NUMBERS — approval-gated
# ══════════════════════════════════════════════════════════════════════════

def twilio_buy_phone_number(inputs, stamp):
    """Buy a phone number. The only command here that keeps charging.

    Every other spend in this module is a one-off. This one creates a
    RECURRING monthly charge that continues until somebody releases the
    number — including on accounts nobody is watching any more. That is why
    it carries its own `confirm_recurring_charge` on top of the approval.
    """
    number = _opt_str(inputs, "phone_number")
    country = (_opt_str(inputs, "country") or "US").upper()
    area = _opt_str(inputs, "area_code")
    if not number and not area and not _opt_str(inputs, "country"):
        raise RuntimeError(
            "pass phone_number (a specific number from "
            "search_available_numbers) or country plus area_code")

    monthly, currency = None, "USD"
    try:
        pr = _req("GET", "/v1/PhoneNumbers/Countries/" + country,
                  host="pricing")
        currency = pr.get("price_unit", "USD")
        for p in (pr.get("phone_number_prices") or []):
            if str(p.get("number_type", "")).lower() == "local":
                monthly = float(p.get("current_price"))
                break
    except Exception:
        pass

    expected = _opt_num(inputs, "expected_monthly_cost_usd")
    if expected is not None:
        if monthly is None:
            raise RuntimeError(
                "refusing to buy — expected_monthly_cost_usd was set but the "
                "monthly price for %s could not be read, so the ceiling "
                "cannot be enforced" % country)
        if monthly > float(expected) + 1e-9:
            raise RuntimeError(
                "refusing to buy — %s numbers cost %.4f %s per month, over "
                "the approved ceiling of %.4f"
                % (country, monthly, currency, float(expected)))

    if not _opt_bool(inputs, "confirm_recurring_charge", False):
        raise RuntimeError(
            "refusing to buy — this creates a RECURRING monthly charge of "
            "%s that continues until the number is released, unlike every "
            "other spend in this module. Set confirm_recurring_charge: true "
            "in the approved payload to acknowledge that."
            % ("%.4f %s" % (monthly, currency) if monthly is not None
               else "an amount that could not be read"))

    params = {
        "PhoneNumber": number,
        "AreaCode": None if number else area,
        "FriendlyName": _opt_str(inputs, "friendly_name"),
        "SmsUrl": _opt_str(inputs, "sms_url"),
        "VoiceUrl": _opt_str(inputs, "voice_url"),
    }
    p = _req("POST", _acct("/IncomingPhoneNumbers.json"), params=params,
             timeout=45)
    return _ok({
        "phone_number_sid": p.get("sid", ""),
        "phone_number": p.get("phone_number", ""),
        "friendly_name": p.get("friendly_name", ""),
        "country": country,
        "monthly_cost": monthly,
        "currency": currency,
        "capabilities": p.get("capabilities") or {},
        "recurring": True,
        "note": ("this charge repeats every month until release_phone_number "
                 "is run against it"),
    })


def twilio_release_phone_number(inputs, stamp):
    """Release a number back to Twilio's pool. Irreversible.

    The number returns to general availability and somebody else can buy it
    within minutes. You cannot get it back. Anything routed to it — 2FA
    codes, published contact numbers, webhook callbacks — stops working
    permanently, and the people trying to reach it will not be told why.
    """
    sid = _req_str(inputs, "phone_number_sid")
    p = _req("GET", _acct("/IncomingPhoneNumbers/%s.json" % sid))
    number = p.get("phone_number", "")

    expected = _opt_str(inputs, "expected_phone_number")
    if expected and number != expected.strip():
        raise RuntimeError(
            "refusing to release — SID %s is %s, not the %s that was "
            "approved. A phone number SID says nothing about which number it "
            "is." % (sid, number or "(unknown)", expected.strip()))

    if not _opt_bool(inputs, "confirm_unrecoverable", False):
        raise RuntimeError(
            "refusing to release — %s goes back to Twilio's pool and CANNOT "
            "be reclaimed; someone else may hold it within minutes, and "
            "everything routed to it stops working. Set "
            "confirm_unrecoverable: true in the approved payload to "
            "acknowledge that." % (number or sid))

    _req("DELETE", _acct("/IncomingPhoneNumbers/%s.json" % sid))
    return _ok({
        "phone_number_sid": sid,
        "phone_number": number,
        "released": True,
        "reclaimable": False,
        "monthly_cost_ended": None,
        "note": ("this number is now available for anyone else to buy; "
                 "traffic to it will not reach you again"),
    })


def twilio_update_phone_number(inputs, stamp):
    """Change a number's webhooks or friendly name. Reversible."""
    sid = _req_str(inputs, "phone_number_sid")
    p = _req("GET", _acct("/IncomingPhoneNumbers/%s.json" % sid))
    number = p.get("phone_number", "")

    expected = _opt_str(inputs, "expected_phone_number")
    if expected and number != expected.strip():
        raise RuntimeError(
            "refusing to update — SID %s is %s, not the %s that was approved."
            % (sid, number or "(unknown)", expected.strip()))

    params, updated = {}, []
    for field, api in (("friendly_name", "FriendlyName"),
                       ("sms_url", "SmsUrl"), ("voice_url", "VoiceUrl"),
                       ("status_callback", "StatusCallback")):
        v = _opt_str(inputs, field)
        if v is not None:
            if field.endswith("_url") or field == "status_callback":
                if not v.startswith("https://"):
                    raise RuntimeError(
                        "%s must be https:// — Twilio posts call and message "
                        "data to it" % field)
            params[api] = v
            updated.append(field)
    if not params:
        raise RuntimeError(
            "pass at least one of friendly_name, sms_url, voice_url or "
            "status_callback")

    res = _req("POST", _acct("/IncomingPhoneNumbers/%s.json" % sid),
               params=params)
    return _ok({
        "phone_number_sid": sid,
        "phone_number": res.get("phone_number", number),
        "updated_fields": updated,
        "friendly_name": res.get("friendly_name", ""),
    })


# ══════════════════════════════════════════════════════════════════════════
#  VERIFY — approval-gated
# ══════════════════════════════════════════════════════════════════════════

def twilio_start_verification(inputs, stamp):
    """Send a verification code. Irreversible: a real message, really billed.

    Verify is priced per attempt on top of the underlying SMS or call, and
    it is a favourite target for toll fraud precisely because it will send
    to any number given to it — so the country allowlist matters more here
    than almost anywhere else.
    """
    service = _req_str(inputs, "service_sid")
    if not service.startswith("VA"):
        raise RuntimeError(
            "service_sid should be a Verify Service SID starting with 'VA' "
            "(got %r). A Messaging Service SID starts with 'MG' and will not "
            "work here." % service[:4])
    to = _e164(inputs.get("to"), "to")
    channel = (_opt_str(inputs, "channel") or "sms").lower()
    if channel not in ("sms", "call", "email", "whatsapp"):
        raise RuntimeError(
            "channel must be sms, call, email or whatsapp (got %r)" % channel)

    iso, _lookup = _country_of(to)
    _guard_country(iso, inputs, "send a verification")

    estimated, currency = None, "USD"
    try:
        if channel == "call":
            per, currency = _voice_price(to)
            estimated = round(per, 5)
        else:
            per, _lo, currency = _sms_price(iso)
            estimated = round(per, 5)
    except Exception:
        pass
    _guard_cost(estimated, inputs, "send a verification",
                "this is the underlying %s price to %s; Verify adds its own "
                "per-attempt fee on top, which the Pricing API does not "
                "expose." % (channel, iso))

    v = _req("POST", "/v2/Services/%s/Verifications" % service,
             params={"To": to, "Channel": channel,
                     "Locale": _opt_str(inputs, "locale")},
             host="verify", timeout=45)
    return _ok({
        "verification_sid": v.get("sid", ""),
        "to": v.get("to", to),
        "channel": v.get("channel", channel),
        "status": v.get("status", ""),
        "country": iso,
        "estimated_cost": estimated,
        "currency": currency,
        "valid": bool(v.get("valid")),
        "cost_is_approximate": True,
    })


def twilio_check_verification(inputs, stamp):
    """Check a verification code. Cheap, and not destructive."""
    service = _req_str(inputs, "service_sid")
    to = _e164(inputs.get("to"), "to")
    code = _req_str(inputs, "code")
    v = _req("POST", "/v2/Services/%s/VerificationCheck" % service,
             params={"To": to, "Code": code}, host="verify")
    return _ok({
        "verification_sid": v.get("sid", ""),
        "to": v.get("to", to),
        "status": v.get("status", ""),
        "valid": bool(v.get("valid")),
    })


# ══════════════════════════════════════════════════════════════════════════
#  CONVERSATIONS — approval-gated
# ══════════════════════════════════════════════════════════════════════════

def twilio_create_conversation(inputs, stamp):
    """Create a Conversation. Costs nothing until someone is added."""
    params = {
        "FriendlyName": _opt_str(inputs, "friendly_name"),
        "UniqueName": _opt_str(inputs, "unique_name"),
    }
    attrs = _opt_dict(inputs, "attributes")
    if attrs:
        params["Attributes"] = _json.dumps(attrs)
    c = _req("POST", "/v1/Conversations", params=params, host="conversations")
    return _ok({
        "conversation_sid": c.get("sid", ""),
        "friendly_name": c.get("friendly_name", ""),
        "unique_name": c.get("unique_name", ""),
        "state": c.get("state", ""),
        "date_created": c.get("date_created", ""),
    })


def twilio_send_conversation_message(inputs, stamp):
    """Post into a Conversation. Irreversible, and fans out to every SMS
    participant at that participant's own destination price."""
    sid = _req_str(inputs, "conversation_sid")
    body = _req_str(inputs, "body")

    participants = _conversation_participants(sid)
    expected = _opt_int(inputs, "expected_participant_count")
    if expected is not None and len(participants) != expected:
        raise RuntimeError(
            "refusing to send — this Conversation had %d participant(s) when "
            "it was approved and has %d now. Every SMS participant is a "
            "separate billed message."
            % (expected, len(participants)))

    params = {"Body": body, "Author": _opt_str(inputs, "author"),
              "MediaSid": _opt_str(inputs, "media_sid")}
    m = _req("POST", "/v1/Conversations/%s/Messages" % sid, params=params,
             host="conversations", timeout=45)
    return _ok({
        "conversation_sid": sid,
        "message_sid": m.get("sid", ""),
        "body": m.get("body", body),
        "body_sha256": _sha(body),
        "author": m.get("author", ""),
        "index": m.get("index"),
        "participant_count": len(participants),
        "sms_participants": sum(1 for p in participants if p["phone_number"]),
    })


def twilio_add_conversation_participant(inputs, stamp):
    """Add a participant. Irreversible in the way that matters.

    Adding an SMS participant means every subsequent message in the
    conversation is billed to that destination — an ongoing cost, not a
    one-off — and the new participant can read what follows.
    """
    sid = _req_str(inputs, "conversation_sid")
    phone = _opt_str(inputs, "phone_number")
    identity = _opt_str(inputs, "identity")
    if not phone and not identity:
        raise RuntimeError("pass either phone_number or identity")
    if phone and identity:
        raise RuntimeError("pass either phone_number or identity, not both")

    before = _conversation_participants(sid)
    expected = _opt_int(inputs, "expected_participant_count")
    if expected is not None and len(before) != expected:
        raise RuntimeError(
            "refusing to add — this Conversation had %d participant(s) when "
            "it was approved and has %d now." % (expected, len(before)))

    params = {}
    if phone:
        params["MessagingBinding.Address"] = _e164(phone, "phone_number")
        proxy = _opt_str(inputs, "proxy_number")
        if not proxy:
            raise RuntimeError(
                "proxy_number is required when adding an SMS participant — "
                "it is the Twilio number the conversation speaks from, and "
                "Twilio rejects the binding without it")
        params["MessagingBinding.ProxyAddress"] = _e164(proxy, "proxy_number")
    else:
        params["Identity"] = identity

    p = _req("POST", "/v1/Conversations/%s/Participants" % sid, params=params,
             host="conversations")
    return _ok({
        "conversation_sid": sid,
        "participant_sid": p.get("sid", ""),
        "phone_number": phone or "",
        "identity": identity or "",
        "participant_count_before": len(before),
        "participant_count_after": len(before) + 1,
        "is_sms_participant": bool(phone),
        "note": ("every future message in this conversation is now billed to "
                 "this destination as well" if phone else ""),
    })


def twilio_remove_conversation_participant(inputs, stamp):
    """Remove a participant. Reversible by adding them back."""
    sid = _req_str(inputs, "conversation_sid")
    psid = _req_str(inputs, "participant_sid")
    _req("DELETE", "/v1/Conversations/%s/Participants/%s" % (sid, psid),
         host="conversations")
    remaining = _conversation_participants(sid)
    return _ok({
        "conversation_sid": sid,
        "participant_sid": psid,
        "removed": True,
        "participant_count_after": len(remaining),
    })


def twilio_delete_conversation(inputs, stamp):
    """Delete a Conversation and every message in it. Irreversible."""
    sid = _req_str(inputs, "conversation_sid")
    c = _req("GET", "/v1/Conversations/" + sid, host="conversations")
    name = c.get("friendly_name", "")

    expected = _opt_str(inputs, "expected_friendly_name")
    if expected and name != expected.strip():
        raise RuntimeError(
            "refusing to delete — conversation %s is named %r, not the %r "
            "that was approved." % (sid, name or "(unnamed)", expected.strip()))

    count = 0
    try:
        res = _req("GET", "/v1/Conversations/%s/Messages" % sid,
                   params={"PageSize": _MAX_LIMIT}, host="conversations")
        count = len(res.get("messages") or [])
    except Exception:
        pass

    if not _opt_bool(inputs, "confirm_deletes_messages", False):
        raise RuntimeError(
            "refusing to delete — this destroys the conversation and at "
            "least %d message(s) in it, permanently. Set "
            "confirm_deletes_messages: true in the approved payload to "
            "acknowledge that." % count)

    _req("DELETE", "/v1/Conversations/" + sid, host="conversations")
    return _ok({
        "conversation_sid": sid,
        "deleted": True,
        "was_friendly_name": name,
        "messages_destroyed": count,
    })


# ══════════════════════════════════════════════════════════════════════════
#  STUDIO — approval-gated
# ══════════════════════════════════════════════════════════════════════════

def twilio_trigger_studio_flow(inputs, stamp):
    """Start a Studio flow. THE ONE COMMAND WHOSE COST CANNOT BE BOUNDED.

    Every other spend here is priced and capped before it happens. A Studio
    flow is different in kind: it is a program living in Twilio's console
    that can send messages, place calls, loop, and branch — all billed, none
    of it visible to this module, and none of it subject to
    expected_max_cost_usd.

    The honest response to a limit that cannot be enforced is to say so.
    This command requires `confirm_may_spend_externally`, returns
    `cost_is_unbounded: true`, and does not accept a cost ceiling it would
    be unable to honour.
    """
    flow = _req_str(inputs, "flow_sid")
    if not flow.startswith("FW"):
        raise RuntimeError(
            "flow_sid should be a Studio Flow SID starting with 'FW' (got %r)"
            % flow[:4])
    to = _e164(inputs.get("to"), "to")
    c = _creds()
    frm = _opt_str(inputs, "from_number") or c["default_from"]
    if not frm:
        raise RuntimeError(
            "no sender — pass from_number, or set default_from in the twilio "
            "vault entry")
    frm = _e164(frm, "from_number")

    if "expected_max_cost_usd" in inputs and inputs.get("expected_max_cost_usd"):
        raise RuntimeError(
            "refusing to run — expected_max_cost_usd cannot be honoured for a "
            "Studio flow. The flow runs inside Twilio and can send messages "
            "and place calls this module never sees, so a ceiling here would "
            "be a promise nothing enforces. Bound the spend inside the flow "
            "itself, or use the individual commands.")

    iso, _lookup = _country_of(to)
    _guard_country(iso, inputs, "trigger a Studio flow toward")

    if not _opt_bool(inputs, "confirm_may_spend_externally", False):
        raise RuntimeError(
            "refusing to run — a Studio flow can send messages and place "
            "calls of its own, billed to this account and outside every cost "
            "guard in this module. Set confirm_may_spend_externally: true in "
            "the approved payload to acknowledge that the spend is unbounded "
            "from here.")

    params = {"To": to, "From": frm}
    p = _opt_dict(inputs, "parameters")
    if p:
        params["Parameters"] = _json.dumps(p)

    e = _req("POST", "/v2/Flows/%s/Executions" % flow, params=params,
             host="studio", timeout=45)
    return _ok({
        "flow_sid": flow,
        "execution_sid": e.get("sid", ""),
        "to": e.get("contact_channel_address", to),
        "from": frm,
        "status": e.get("status", ""),
        "country": iso,
        "cost_is_unbounded": True,
        "note": ("this flow's own messages and calls are billed to this "
                 "account and are not visible to this module — watch "
                 "get_usage"),
    })


def twilio_stop_studio_execution(inputs, stamp):
    """Stop a running Studio execution. Stops further spend from that flow."""
    flow = _req_str(inputs, "flow_sid")
    ex = _req_str(inputs, "execution_sid")
    before = ""
    try:
        before = (_req("GET", "/v2/Flows/%s/Executions/%s" % (flow, ex),
                       host="studio").get("status", ""))
    except Exception:
        pass
    e = _req("POST", "/v2/Flows/%s/Executions/%s" % (flow, ex),
             params={"Status": "ended"}, host="studio")
    return _ok({
        "flow_sid": flow,
        "execution_sid": ex,
        "status": e.get("status", "ended"),
        "was_status": before,
    })
