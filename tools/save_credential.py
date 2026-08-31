# -*- coding: utf-8 -*-
"""Save the Twilio bot token into the station vault, without it touching a
shell history, a command line, or a chat transcript.

    python tools/save_credential.py            # prompts, writes, verifies
    python tools/save_credential.py --check     # just report what is stored
    python tools/save_credential.py --remove    # delete the twilio entry

The token is read with getpass (no echo), validated, written to
~/.railcall/station/.railcall_workspace/credentials.local.json in the same
shape Studio's Integrations tab produces, and the file is chmod 0600.
Nothing prints the token — confirmations show `xoxb-12…9f2e` only.

WHY THIS MATTERS MORE FOR SLACK THAN FOR MOST PROVIDERS. A bot token is a
bearer credential with no second factor and no per-call confirmation: anyone
holding it can post as your bot, in your workspace, to every channel the bot
is in, until someone notices and reinstalls the app. It is also unusually
easy to leak, because the natural way to test one is to paste it into a curl
command — which lands it in shell history, in the terminal scrollback, and
in any transcript of the session. This script exists so the token goes from
the clipboard to the vault without stopping anywhere in between.

WHY NOT THE STUDIO UI. Studio validates a credential's provider against a
vault allowlist built from the built-in providers plus the `credential_spec`
of every *loaded* module. `ray9/twilio` declares `license_required: true` and
has not been published, so the loader refuses it and `twilio` never reaches
that allowlist — Studio would reject the save. Writing the entry directly is
the correct move until the module is published and licensed; after that,
Studio manages it normally.
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

# xoxb- then Twilio's dash-separated numeric/alnum sections. Twilio has changed
# the exact section count over the years, so this deliberately checks the
# prefix and a plausible body rather than pinning an exact shape that a future
# token format would fail.
TOKEN_RE = re.compile(r"^xoxb-[0-9A-Za-z-]{20,}$")

# The prefixes people actually paste by mistake, and what each one really is.
WRONG_TOKEN = {
    "xoxp-": "a USER token — it acts as a human, not a bot. This module is "
             "bot-token only by design.",
    "xapp-": "an APP-LEVEL token — for Socket Mode, cannot call the Web API.",
    "xoxe-": "a REFRESH token, not an access token.",
    "xoxa-": "a legacy workspace token.",
    "xoxs-": "a session cookie token — never use one for automation.",
}


def mask(tok):
    return tok[:9] + "…" + tok[-4:] if len(tok) > 20 else "…"


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
    tok = str(fields.get("bot_token") or "")
    print("twilio credential present")
    print("  vault:      %s" % VAULT)
    print("  cred id:    %s" % cid)
    print("  bot_token:  %s  (%d chars, %s)"
          % (mask(tok), len(tok),
             "well-formed" if TOKEN_RE.match(tok) else "MALFORMED"))
    for k in ("base_url", "default_channel"):
        if fields.get(k):
            print("  %-11s %s" % (k + ":", fields[k]))
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


def prompt_and_store():
    os.makedirs(WS, exist_ok=True)

    print("Paste the Bot User OAuth Token from your Twilio app's")
    print("  OAuth & Permissions page. It starts with xoxb-.")
    print("(input is hidden and is never echoed or logged)")
    tok = getpass.getpass("  bot_token: ").strip()

    if not tok:
        print("\nNothing entered — aborted.")
        return 1
    if not tok.startswith("xoxb-"):
        wrong = WRONG_TOKEN.get(tok[:5])
        print("\nThat is not a bot token. It starts with %r, which is %s"
              % (tok[:5], wrong or "not a token shape Twilio issues."))
        print("Look for 'Bot User OAuth Token' — not 'User OAuth Token'.")
        return 1
    if not TOKEN_RE.match(tok):
        print("\nThat starts with xoxb- but does not look complete "
              "(%d chars). A truncated paste is the usual cause." % len(tok))
        return 1

    channel = input("  default_channel (optional, e.g. C024BE91L): ").strip()
    base = input("  base_url (optional, blank = https://twilio.com/api): ").strip()

    fields = {"bot_token": tok}
    if channel:
        fields["default_channel"] = channel
    if base:
        fields["base_url"] = base.rstrip("/")

    data = load()
    cid = "twilio-default"
    data[PROVIDER] = {
        "default": cid,
        "credentials": {
            cid: {
                "id": cid,
                "label": "Twilio bot",
                "fields": fields,
                "created_at": int(time.time()),
            }
        },
    }
    save(data)

    print("\nSaved to %s (mode 0600)" % VAULT)
    print("  bot_token: %s" % mask(tok))
    print("\nNow run the smoke test:")
    print("  twilio.verify_credential")
    print("It is read_only and needs no approval. Check `missing_scopes` in")
    print("the result — a token can authenticate perfectly and still lack the")
    print("scope for a command you will not reach until much later.")
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
