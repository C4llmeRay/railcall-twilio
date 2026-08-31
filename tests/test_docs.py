# -*- coding: utf-8 -*-
"""Docs must not drift from the manifest.

Every count and every command name in the prose is a claim about module.json.
On the next version someone will add a command and update four of the five
files; this is what catches the fifth.
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(_HERE, os.pardir)

manifest = json.load(
    open(os.path.join(ROOT, "module", "module.json"), encoding="utf-8"))
ids = {c["id"] for c in manifest["commands"]}
short = {i.split(".", 1)[1] for i in ids}
irreversible = {c["id"] for c in manifest["commands"] if c.get("irreversible")}
reads = {c["id"] for c in manifest["commands"] if c["mode"] == "read_only"}
gated = ids - reads

# `twilio.com` is the hostname and `twilio.api` shows up in prose about the Web
# API; neither is a command. The lookbehind below keeps the repo/remote name
# out of it too — `railcall-twilio.git` is not a reference to `twilio.git`.
NOT_COMMANDS = {"com", "api", "org"}
CMD_RE = re.compile(r"(?<![-\w])twilio\.([a-z_]+)")

DOCS = ["README.md", "TESTING.md", "CHANGELOG.md", "LISTING.md",
        "PUBLISHING.md", os.path.join("module", "docs", "SETUP.md")]


def read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


fails = []

# 1. No document may reference a command that does not exist. ──────────────
for rel in DOCS:
    txt = read(rel)
    for ref in sorted(set(CMD_RE.findall(txt)) - NOT_COMMANDS):
        if ref not in short:
            fails.append("%s references twilio.%s, which is not a command"
                         % (rel, ref))

# 2. The headline counts must match the manifest. ─────────────────────────
WORDS = {
    3: "three", 5: "five", 6: "six", 8: "eight", 9: "nine", 11: "eleven",
    12: "twelve", 14: "fourteen", 18: "eighteen", 19: "nineteen",
    20: "twenty", 23: "twenty-three", 24: "twenty-four", 32: "thirty-two",
    38: "thirty-eight",
}
n_all, n_reads, n_gated, n_irrev = len(ids), len(reads), len(gated), len(irreversible)

for rel in ("LISTING.md", "README.md", os.path.join("module", "docs", "SETUP.md")):
    txt = read(rel).lower()
    word = WORDS.get(n_all)
    if str(n_all) not in txt and (not word or word not in txt):
        fails.append("%s never states the command count (%d)" % (rel, n_all))

listing = read("LISTING.md").lower()
if WORDS.get(n_reads, "") and WORDS[n_reads] not in listing and \
        str(n_reads) not in listing:
    fails.append("LISTING.md does not state the read-only count (%d)" % n_reads)

desc = manifest["description"].lower()
for label, count in (("read-only", n_reads), ("irreversible", n_irrev)):
    word = WORDS.get(count, "")
    if word and word not in desc and str(count) not in desc:
        fails.append("manifest description does not state the %s count (%d)"
                     % (label, count))

# 3. The three guards must be named in the listing, the readme and setup. ──
GUARDS = ("expected_max_cost_usd", "expected_segments",
          "allowed_countries", "confirm_recurring_charge",
          "confirm_recording_consent", "confirm_unrecoverable",
          "confirm_may_spend_externally")
# LISTING.md is bounded at 2000 characters by the marketplace, so it cannot
# name all seven. It must carry the ones a buyer decides on.
LISTING_GUARDS = ("expected_max_cost_usd", "allowed_countries",
                  "confirm_recurring_charge", "confirm_unrecoverable")
for rel in ("README.md", os.path.join("module", "docs", "SETUP.md")):
    txt = read(rel)
    for g in GUARDS:
        if g not in txt:
            fails.append("%s never mentions %s" % (rel, g))
_listing = read("LISTING.md")
for g in LISTING_GUARDS:
    if g not in _listing:
        fails.append("LISTING.md never mentions %s" % g)

# 4. Every guard named in the docs must be a real input on some command. ───
declared = set()
for c in manifest["commands"]:
    declared.update(c["input_schema"].keys())
for g in GUARDS:
    if g not in declared:
        fails.append("docs promise %s but no command declares it as an input" % g)

# 5. The pricing reads must stay read_only. ──────────────────────────────
# price_message and price_call are how a human sees the cost BEFORE
# approving anything. If either ever became a write, the approval preview
# would need an approval of its own and the whole flow breaks.
for _name in ("twilio.price_message", "twilio.price_call",
              "twilio.get_balance"):
    _c = next((c for c in manifest["commands"] if c["id"] == _name), None)
    if _c is None:
        fails.append("%s is missing — the docs build the cost story on it"
                     % _name)
    elif _c["mode"] != "read_only":
        fails.append("%s is no longer read_only, but it is what an approver "
                     "runs to price a send before approving it" % _name)

# trigger_studio_flow must NOT advertise a cost ceiling it cannot honour.
_studio = next(c for c in manifest["commands"]
               if c["id"] == "twilio.trigger_studio_flow")
if "expected_max_cost_usd" in _studio["input_schema"]:
    fails.append("trigger_studio_flow declares expected_max_cost_usd, but "
                 "README.md and LISTING.md both state it refuses one because "
                 "a Studio flow spends outside this module's guards")

# 6. Every irreversible command must be justified somewhere in the prose. ──
readme = read("README.md") + read("CHANGELOG.md") + read("LISTING.md")
for cid in sorted(irreversible):
    name = cid.split(".", 1)[1]
    if name not in readme:
        fails.append("%s is flagged irreversible but no doc explains why" % cid)

# 7. The segment trap must be explained, not just named. ─────────────────
# expected_segments is worthless if a reader does not know WHY a short
# message can cost double. Every doc that mentions it must show the number.
for rel in ("README.md", os.path.join("module", "docs", "SETUP.md"),
            "LISTING.md"):
    txt = read(rel)
    if "70" not in txt or "160" not in txt:
        fails.append("%s talks about segments but never shows the 160 vs 70 "
                     "character split that makes them cost money" % rel)

# Every Twilio error code the handler glosses should be reachable prose —
# SETUP.md carries the operator-facing table.
_handler = open(os.path.join(ROOT, "module", "handlers", "handler.py"),
                encoding="utf-8").read()
_setup = read(os.path.join("module", "docs", "SETUP.md"))
for _code in ("21408", "21219", "21610"):
    if _code not in _setup:
        fails.append("SETUP.md does not document Twilio error %s, which the "
                     "handler glosses because operators hit it" % _code)

# 8. The manifest's network allowlist must match what the docs promise. ────
net = manifest["requires"]["network"]
if "api.twilio.com" not in net:
    fails.append("manifest network allowlist does not include api.twilio.com")
if manifest["requires"]["subprocess"] is not False:
    fails.append("manifest does not deny subprocess, but LISTING.md says it does")
if manifest["requires"]["filesystem_writes"] != []:
    fails.append("manifest does not deny filesystem writes, but LISTING.md "
                 "says it does")


# 9. The marketplace caps --description at 2000 characters. ───────────────
# LISTING.md is passed via --description=<file>; the CLI (and the server-side
# gate behind it) rejects anything outside 40..2000. This was found the hard
# way on zernio, where the listing had to be cut from 3049. Enforcing it here
# means a publish is never aborted by prose nobody measured.
MARKET_DESC_MIN, MARKET_DESC_MAX = 40, 2000
_listing_len = len(read("LISTING.md"))
if not (MARKET_DESC_MIN <= _listing_len <= MARKET_DESC_MAX):
    fails.append("LISTING.md is %d characters; the marketplace --description "
                 "bound is %d..%d and the publish would be rejected"
                 % (_listing_len, MARKET_DESC_MIN, MARKET_DESC_MAX))

# The manifest description is used as the fallback when no --description file
# is passed, so it is under the same cap.
_desc_len = len(manifest["description"])
if not (MARKET_DESC_MIN <= _desc_len <= MARKET_DESC_MAX):
    fails.append("manifest description is %d characters; bound is %d..%d"
                 % (_desc_len, MARKET_DESC_MIN, MARKET_DESC_MAX))

# The workflow carries its own description and is published without a
# --description file, so it is capped too.
_wf = json.load(open(os.path.join(ROOT, "workflow",
                                  "twilio-spend-airlock.json"),
                     encoding="utf-8"))
_wf_len = len(_wf.get("description") or "")
if not (MARKET_DESC_MIN <= _wf_len <= MARKET_DESC_MAX):
    fails.append("workflow description is %d characters; bound is %d..%d"
                 % (_wf_len, MARKET_DESC_MIN, MARKET_DESC_MAX))

# 10. The category must be one the marketplace actually knows. ────────────
# An unknown category is not an error at publish time — it is silently
# dropped from the browse filters, which is worse: the listing publishes
# and is then invisible to anyone browsing by category.
MARKET_CATEGORIES = {"Data", "Eng", "Finance", "HR/IT", "Marketing", "Ops",
                     "Revenue", "Risk", "Success", "Support", "Governance"}
if _wf.get("category") not in MARKET_CATEGORIES:
    fails.append("workflow category %r is not a marketplace category — it "
                 "would be dropped from the browse filters. Valid: %s"
                 % (_wf.get("category"), ", ".join(sorted(MARKET_CATEGORIES))))

# 11. A free listing must not require a license. ──────────────────────────
# license_required gates loading behind a marketplace entitlement. Publishing
# does not grant yourself one, so a free module with license_required: true
# cannot be loaded even by its own author.
if manifest.get("license_required"):
    fails.append("license_required is true, but this module is published "
                 "free — the entitlement gate would block loading it, "
                 "including for the publisher")


def run():
    print("manifest: %d commands (%d read-only / %d gated / %d irreversible)"
          % (n_all, n_reads, n_gated, n_irrev))
    if fails:
        print("\nDOC DRIFT (%d):" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("docs agree with the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(run())
