# -*- coding: utf-8 -*-
"""Audit the GSM 03.38 table against the standard, transcribed independently.

WHY THIS IS SEPARATE FROM test_guards.py. That suite checks the *arithmetic*:
given the module's alphabet, do 161 characters split into two segments. This
one checks the *alphabet itself*, against the standard written out from
scratch below.

The distinction matters because the two fail differently. Wrong arithmetic
produces obviously silly numbers. A wrong alphabet produces a confidently
wrong one: a single character misfiled as GSM-7 makes the module price a
UCS-2 message at 160 characters per segment, so every ceiling on every
message containing it is out by a factor of more than two — and it under-
counts, so the guard passes a send that costs more than approved.

Live testing would not catch it either. Pricing one destination confirms the
price per segment, not the segment count, and the count is the half this
module computes itself.

Everything here runs offline. No account, no credential, no network.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(_HERE, os.pardir)

ns = {"__name__": "railcall_module_twilio",
      "__rc_helpers__": {"vault_get": lambda p: {},
                         "airlock_payload_hash": lambda a, b: ""}}
exec(compile(open(os.path.join(ROOT, "module", "handlers", "handler.py"),
                  encoding="utf-8").read(), "handler.py", "exec"), ns)

_segments = ns["_segments"]

# GSM 03.38 basic character set, transcribed from the standard row by row.
# Position 0x1B is ESC, which introduces the extension table and is not a
# character in its own right, so 127 encodable characters remain.
OFFICIAL_BASIC = (
    "@£$¥èéùìòÇ"
    "\nØø\rÅå"
    "Δ_ΦΓΛΩΠΨΣΘΞ"
    "ÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
OFFICIAL_EXTENDED = "^{}\\[~]|€"

results = []


def scenario(label):
    def deco(fn):
        results.append((label, fn))
        return fn
    return deco


@scenario("the basic alphabet matches GSM 03.38 exactly")
def _():
    mod = set(ns["_GSM_BASIC"])
    off = set(OFFICIAL_BASIC)
    missing = sorted(off - mod)
    extra = sorted(mod - off)
    if missing:
        return ("missing %r — these would be priced as UCS-2 when the carrier "
                "bills them as GSM-7 (over-counting: safe but wrong)"
                % "".join(missing))
    if extra:
        return ("has %r, which is NOT in GSM 03.38 — these would be priced at "
                "160/segment when the carrier re-encodes the whole message to "
                "UCS-2 at 70. That UNDER-counts, so a ceiling passes a send "
                "costing more than was approved" % "".join(extra))
    return None


@scenario("the extension table matches, and each of its chars costs 2 units")
def _():
    if set(ns["_GSM_EXTENDED"]) != set(OFFICIAL_EXTENDED):
        return ("extension set is %r, standard is %r"
                % (ns["_GSM_EXTENDED"], OFFICIAL_EXTENDED))
    for ch in OFFICIAL_EXTENDED:
        _n, enc, units = _segments(ch)
        if enc != "GSM-7":
            return "%r priced as %s; it is a GSM-7 extension char" % (ch, enc)
        if units != 2:
            return ("%r counted as %d unit(s); extension characters are sent "
                    "as an escape sequence and bill as two" % (ch, units))
    return None


@scenario("the alphabet is 127 encodable characters")
def _():
    n = len(set(ns["_GSM_BASIC"]))
    if n != 127:
        return ("%d characters; GSM 03.38 has 128 positions of which one is "
                "ESC, leaving 127 encodable" % n)
    return None


# ── the cases that actually bite in production ─────────────────────────────

CASES = [
    # body,                    segments, encoding, why it is here
    ("x" * 160, 1, "GSM-7", "160 is exactly one segment"),
    ("x" * 161, 2, "GSM-7", "161 splits, and split segments hold 153 not 160"),
    ("x" * 306, 2, "GSM-7", "exactly two concatenated segments"),
    ("x" * 307, 3, "GSM-7", "307 needs a third"),
    ("x" * 70, 1, "GSM-7", "70 plain chars is still one GSM-7 segment"),
    ("é" * 70, 1, "GSM-7", "70 e-acutes too: it is in GSM-7"),
    ("x" * 100 + "\U0001F600", 2, "UCS-2",
     "ONE emoji re-encodes the whole message at 70/segment"),
    ("please don’t reply", 1, "UCS-2",
     "a curly apostrophe out of a word processor forces UCS-2"),
    ("please don't reply", 1, "GSM-7", "the straight one does not"),
    ("café", 1, "GSM-7", "e-acute IS in GSM-7 — a common false alarm"),
    ("crêpe", 1, "UCS-2", "e-circumflex is NOT"),
    ("ΔΩ", 1, "GSM-7", "Greek capitals are in GSM-7"),
    ("δω", 1, "UCS-2", "Greek lowercase is not"),
    ("€", 1, "GSM-7", "the euro sign is an extension char"),
]

for _body, _seg, _enc, _why in CASES:
    def _make(body=_body, seg=_seg, enc=_enc, why=_why):
        @scenario("%s -> %d %s (%s)"
                  % (repr(body if len(body) <= 20 else "%d chars" % len(body)),
                     seg, enc, why))
        def _():
            n, e, _u = _segments(body)
            if (n, e) != (seg, enc):
                return "got %d segment(s) %s" % (n, e)
            return None
    _make()


@scenario("an astral emoji counts as 2 UTF-16 units, not 1")
def _():
    _n, _e, units = _segments("\U0001F600")
    if units != 2:
        return ("counted %d; an emoji outside the BMP is a surrogate pair, so "
                "it occupies two UTF-16 code units and a 70-unit UCS-2 segment "
                "holds only 35 of them" % units)
    return None


@scenario("a 35-emoji message is one segment and a 36-emoji message is two")
def _():
    a, _e, _u = _segments("\U0001F600" * 35)
    b, _e2, _u2 = _segments("\U0001F600" * 36)
    if a != 1:
        return "35 emoji gave %d segments" % a
    if b != 2:
        return ("36 emoji gave %d segments; at 2 units each that is 72 units, "
                "over the 70-unit single-segment limit" % b)
    return None


@scenario("an empty body is one segment, not zero")
def _():
    n, _e, _u = _segments("")
    if n != 1:
        return ("%d — an empty message still bills as one segment, and "
                "returning 0 would let a ceiling of $0 pass" % n)
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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(run())
