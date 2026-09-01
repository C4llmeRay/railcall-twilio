# -*- coding: utf-8 -*-
"""Save the Twilio credential into the station vault, without any secret
touching a shell history, a command line, or a chat transcript.

    python tools/save_credential.py            # prompts, writes, verifies
    python tools/save_credential.py --check     # just report what is stored
    python tools/save_credential.py --remove    # delete the twilio entry

Secrets are read with getpass (no echo). Non-secret identifiers — the account
SID, the API key SID, the sending number — are read normally, because hiding
a value that is not secret only makes it harder to spot a typo.

Everything is written to
~/.railcall/station/.railcall_workspace/credentials.local.json in the same
shape Studio's Integrations tab produces, and the file is chmod 0600.
Nothing prints a secret — confirmations show `……9f2e` only.

WHY THIS MATTERS MORE FOR TWILIO THAN FOR MOST PROVIDERS. A leaked Slack
token embarrasses you. A leaked Twilio credential *spends your money*, and
the people who steal them have a business model: they send to premium-rate
ranges they collect revenue from, at machine speed, until someone notices the
balance. The credential is the whole authentication story — there is no
second factor on an API call — so the only real controls are keeping it out
of every log and using a key you can revoke on its own.

WHY THIS FILE EXISTS AT ALL. The natural way to test a Twilio credential is
to paste it into a curl command, which lands it in shell history, terminal
scrollback, and any transcript of the session. This script exists so it goes
from the clipboard to the vault without stopping anywhere in between.

WHY NOT THE STUDIO UI. Studio validates a credential's provider against a
vault allowlist built from the built-in providers plus the `credential_spec`
of every *loaded* module. That works once `ray9/twilio` is installed from the
marketplace — it is published free and `license_required: false`, so Studio
manages the credential normally from then on. This script is for the case
before that: developing against the source tree, where the module is not
installed and `twilio` has not reached the allowlist yet.
"""
import argparse
import getpass
import json
import os
import re
import sys
import time

WS = os.path.expanduser("~/.railcall/station/.railcall_workspace")
VAULT = os.path.join(WS, "credentials.local.json")
PROVIDER = "twilio"

# Twilio SIDs are a two-letter type prefix followed by 32 hex characters.
# The prefix is the type: AC an account, SK an API key, MG a messaging
# service. Checking it here turns "pasted the wrong SID" — which are all the
# same length and shape — into a message that names which one was expected.
SID_RE = re.compile(r"^[A-Z]{2}[0-9a-fA-F]{32}$")
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

SID_KINDS = {
    "AC": "an Account SID",
    "SK": "an API Key SID",
    "MG": "a Messaging Service SID",
    "PN": "a phone number SID",
    "SM": "a message SID",
    "CA": "a call SID",
    "FW": "a Studio flow SID",
    "VA": "a Verify service SID",
}


def mask(secret):
    """Show enough to recognise it, never enough to use it."""
    if not secret:
        return "(none)"
    if len(secret) <= 8:
        return "…"
    return "…" + secret[-4:]


def load():
    if not os.path.isfile(VAULT):
        return {}
    try:
        return json.load(open(VAULT, encoding="utf-8"))
    except Exception as e:
        print("Could not read the vault: %s" % e)
        sys.exit(1)


def save(data):
    tmp = VAULT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, VAULT)
    try:
        os.chmod(VAULT, 0o600)
    except OSError:
        # Windows ignores POSIX modes; the file inherits the user profile ACL.
        pass


def check_sid(value, want_prefix, label):
    """Return None if fine, else an actionable complaint."""
    if not SID_RE.match(value):
        return ("%s should be %r followed by 32 hex characters (34 total) — "
                "got %d characters." % (label, want_prefix, len(value)))
    got = value[:2].upper()
    if got != want_prefix:
        kind = SID_KINDS.get(got)
        return ("%s should start with %r, but this starts with %r%s. They are "
                "all 34 characters, so they are easy to mix up."
                % (label, want_prefix, got,
                   " — that is %s" % kind if kind else ""))
    return None


def check():
    data = load()
    entry = data.get(PROVIDER)
    if not entry:
        print("No `twilio` credential stored.")
        print("  vault: %s" % VAULT)
        print("  providers present: %s" % (sorted(data) or "none"))
        return 1
    creds = entry.get("credentials") or {}
    cid = entry.get("default") or (sorted(creds)[0] if creds else None)
    fields = ((creds.get(cid) or {}).get("fields") or {})

    print("twilio credential present")
    print("  vault:      %s" % VAULT)
    print("  cred id:    %s" % cid)

    acct = str(fields.get("account_sid") or "")
    problem = check_sid(acct, "AC", "account_sid") if acct else "not set"
    print("  account_sid:  %s  (%s)"
          % (acct or "(none)", problem or "well-formed"))

    key_sid = str(fields.get("api_key_sid") or "")
    key_secret = str(fields.get("api_key_secret") or "")
    print("  auth_token:   %s" % mask(str(fields.get("auth_token") or "")))
    print("  api_key_sid:  %s" % (key_sid or "(none)"))
    print("  api_key_secret: %s" % mask(key_secret))

    if bool(key_sid) != bool(key_secret):
        print("  WARNING: half an API key pair is stored. The handler refuses "
              "this rather than silently falling back to the auth token.")
    elif key_sid:
        print("  -> will authenticate with the API KEY (scoped, revocable)")
    else:
        print("  -> will authenticate with the ACCOUNT AUTH TOKEN. An API key "
              "is scoped and individually revocable; consider one.")

    for k in ("default_from", "messaging_service_sid", "base_url"):
        if fields.get(k):
            print("  %-16s %s" % (k + ":", fields[k]))
    return 0


def remove():
    data = load()
    if PROVIDER not in data:
        print("Nothing to remove — no `twilio` entry in the vault.")
        return 0
    del data[PROVIDER]
    save(data)
    print("Removed the `twilio` credential from %s" % VAULT)
    return 0


def ask_sid(label, want_prefix, required, hint):
    """Prompt for a SID until it is well-formed, or blank when optional."""
    while True:
        value = input("  %s%s: " % (label, "" if required else " (optional)")
                      ).strip()
        if not value:
            if required:
                print("     %s is required. %s" % (label, hint))
                continue
            return ""
        problem = check_sid(value, want_prefix, label)
        if problem:
            print("     %s" % problem)
            continue
        return value


def prompt_and_store():
    os.makedirs(WS, exist_ok=True)

    print("Twilio Console -> dashboard for the Account SID; Account -> API keys")
    print("& tokens to create an API key.")
    print("(secrets are hidden as you type and are never echoed or logged)")
    print()

    account_sid = ask_sid(
        "account_sid", "AC", True,
        "It is on the Console dashboard and starts with AC.")

    print()
    print("  The auth token is the MASTER credential for the whole account:")
    print("  it can rotate itself, reach every subaccount, and cannot be")
    print("  revoked without breaking everything else using it. It is still")
    print("  required here, but an API key below is what will be used.")
    auth_token = getpass.getpass("  auth_token: ").strip()
    if not auth_token:
        print("\nNothing entered — auth_token is required. Aborted.")
        return 1
    if auth_token == account_sid:
        print("\nThat is the account SID again, not the auth token. The token "
              "sits next to it in the Console, behind a 'show' toggle.")
        return 1
    if SID_RE.match(auth_token) and auth_token[:2].upper() in SID_KINDS:
        print("\nThat looks like %s, not the auth token — the auth token is "
              "32 hex characters with NO two-letter prefix."
              % SID_KINDS[auth_token[:2].upper()])
        return 1

    print()
    print("  An API key (SK…) is scoped and individually revocable. Leave")
    print("  blank to use the auth token, or paste a key pair to prefer it.")
    key_sid = ask_sid("api_key_sid", "SK", False,
                      "Console -> Account -> API keys & tokens.")
    key_secret = ""
    if key_sid:
        key_secret = getpass.getpass("  api_key_secret: ").strip()
        if not key_secret:
            # Refuse rather than store half a pair. A key SID with no secret
            # would quietly fall back to the auth token, which is the exact
            # opposite of what someone configuring a key intended.
            print("\nAn api_key_sid with no secret is refused: it would "
                  "silently fall back to the auth token, defeating the point "
                  "of creating a key. The secret is shown ONCE, at creation — "
                  "if it is lost, create a new key.")
            return 1
        if key_secret == auth_token:
            print("\nThe API key secret is the same as the auth token, which "
                  "means one of the two was pasted twice.")
            return 1

    print()
    default_from = ""
    while True:
        default_from = input("  default_from (optional, e.g. +14155550100): "
                             ).strip()
        if not default_from:
            break
        if not E164_RE.match(default_from):
            print("     Must be E.164: '+', country code, digits — no spaces, "
                  "dashes or parentheses, and no leading zero.")
            continue
        break

    msg_service = ask_sid("messaging_service_sid", "MG", False,
                          "Console -> Messaging -> Services.")

    fields = {"account_sid": account_sid, "auth_token": auth_token}
    if key_sid:
        fields["api_key_sid"] = key_sid
        fields["api_key_secret"] = key_secret
    if default_from:
        fields["default_from"] = default_from
    if msg_service:
        fields["messaging_service_sid"] = msg_service

    data = load()
    cid = "twilio-default"
    data[PROVIDER] = {
        "default": cid,
        "credentials": {
            cid: {
                "id": cid,
                "label": "Twilio account",
                "fields": fields,
                "created_at": int(time.time()),
            }
        },
    }
    save(data)

    print()
    print("Saved to %s (mode 0600)" % VAULT)
    print("  account_sid:  %s" % account_sid)
    print("  auth_token:   %s" % mask(auth_token))
    if key_sid:
        print("  api_key_sid:  %s" % key_sid)
        print("  api_key_secret: %s" % mask(key_secret))
        print("  -> authenticating with the API KEY")
    else:
        print("  -> authenticating with the ACCOUNT AUTH TOKEN")
    print()
    print("Now run the free smoke test — it costs nothing:")
    print("  python tools/live_acceptance.py")
    print("It calls verify_credential first. Check `is_trial`: on a trial")
    print("account every send fails with error 21219 unless the destination")
    print("is verified in the Console, which reads like a bad phone number.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report what is stored, without changing it")
    ap.add_argument("--remove", action="store_true",
                    help="delete the twilio entry from the vault")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.remove:
        return remove()
    return prompt_and_store()


if __name__ == "__main__":
    sys.exit(main())
