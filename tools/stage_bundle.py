# -*- coding: utf-8 -*-
"""Stage the publish directory and show exactly what the signature will cover.

    python tools/stage_bundle.py            # stage + preview the signed tree
    python tools/stage_bundle.py --verify   # also verify an existing module.sig

The staging dir mirrors railcall-modules/odoo: the bundle files plus
LISTING.md and workflow/, both of which .moduleignore keeps OUT of the
signed tree. That is deliberate — `railcall market publish` reads
LISTING.md via --description and the workflow is its own listing, but
neither belongs in the bytes a buyer installs.

The tree walk here is a faithful reimplementation of the CLI's
`_module_tree_walk` / `_module_tree_manifest_bytes` and the station's
`_module_tree_manifest_bytes`, which are byte-identical to each other.
Running this before signing means the contents of the signature are a
decision, not a surprise.
"""
import fnmatch
import hashlib
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(_HERE, os.pardir))
SRC = os.path.join(REPO, "module")
STAGE = os.path.abspath(os.path.join(REPO, os.pardir, "twilio"))

# Verbatim from railcall_cli._MODULE_DEFAULT_IGNORE / studio_server.
DEFAULT_IGNORE = (
    "__pycache__/", "*.pyc", "*.pyo", "*.pyd",
    ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
    ".git/", ".gitignore",
    ".env", ".env.*", "*.env",
    ".railcall/", ".railcall_workspace/",
    "node_modules/",
    "*.log", ".DS_Store",
    "module.sig",
)


def read_moduleignore(module_dir):
    patterns = list(DEFAULT_IGNORE)
    p = os.path.join(module_dir, ".moduleignore")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    return patterns


def path_matches(rel_path, patterns):
    parts = rel_path.replace("\\", "/").split("/")
    for pat in patterns:
        if pat.endswith("/"):
            if pat[:-1] in parts:
                return True
        elif fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(parts[-1], pat):
            return True
    return False


def tree_walk(module_dir):
    patterns = read_moduleignore(module_dir)
    root = os.path.abspath(module_dir)
    files, excluded = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            rel = os.path.relpath(os.path.join(dirpath, d), root)
            rel = rel.replace("\\", "/") + "/"
            if path_matches(rel, patterns):
                excluded.append(rel)
            else:
                keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if path_matches(rel, patterns):
                excluded.append(rel)
                continue
            with open(full, "rb") as f:
                b = f.read()
            files.append((rel, hashlib.sha256(b).hexdigest(), len(b)))
    files.sort(key=lambda t: t[0])
    return files, sorted(excluded)


def tree_manifest_bytes(files):
    return "".join("%s\t%s\n" % (rel, sha)
                   for rel, sha, _n in files).encode("utf-8")


def canonical(manifest):
    m = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(m, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def stage():
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE)
    shutil.copytree(SRC, STAGE)
    shutil.copy2(os.path.join(REPO, "LISTING.md"),
                 os.path.join(STAGE, "LISTING.md"))
    shutil.copytree(os.path.join(REPO, "workflow"),
                    os.path.join(STAGE, "workflow"))
    # A stale __pycache__ from a local import would land in the tree if the
    # defaults ever changed; drop it rather than rely on the ignore list.
    for dirpath, dirnames, _f in os.walk(STAGE):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d))
                dirnames.remove(d)
    return STAGE


def main():
    verify_only = "--verify" in sys.argv
    if verify_only:
        # Do NOT re-stage here. Staging wipes the directory, and by this
        # point it holds the module.sig that `railcall market module sign`
        # just wrote — re-copying would delete the very thing we are being
        # asked to check, and then report "no module.sig yet".
        if not os.path.isdir(STAGE):
            print("nothing staged at %s — run without --verify first" % STAGE)
            return 1
        target = STAGE
        print("verifying (not re-staging): %s" % target)
        # A signature verifying against a STALE staging dir is the easiest
        # way to publish code you already fixed. Compare the staged bundle
        # against the repo's module/ and refuse to call it good if they
        # differ: `module sign` rewrites module.json, so that file is
        # expected to differ in line endings only.
        drift = []
        ignore = read_moduleignore(SRC)
        for root, _dirs, files in os.walk(SRC):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), SRC)
                rel = rel.replace(chr(92), "/")
                if path_matches(rel, ignore):
                    continue   # never signed, so drift there is irrelevant
                a = os.path.join(SRC, rel)
                b = os.path.join(STAGE, rel)
                if not os.path.isfile(b):
                    drift.append(rel + " (missing from staging)")
                    continue
                ba, bb = open(a, "rb").read(), open(b, "rb").read()
                crlf, lf = bytes([13, 10]), bytes([10])
                if ba != bb and ba.replace(crlf, lf) != bb.replace(crlf, lf):
                    drift.append("%s (repo %d B, staged %d B)"
                                 % (rel, len(ba), len(bb)))
        if drift:
            print("")
            print("STALE STAGING DIRECTORY - the signature below covers OLD bytes:")
            for x in drift:
                print("  " + x)
            print("Re-stage and re-sign before publishing:")
            print("  python tools/stage_bundle.py")
            print("  railcall market module sign %s" % STAGE)
            return 1
    else:
        target = stage()
    manifest = json.load(open(os.path.join(target, "module.json"),
                              encoding="utf-8"))
    files, excluded = tree_walk(target)
    tm = tree_manifest_bytes(files)
    payload = canonical(manifest) + b"\n" + tm

    if not verify_only:
        print("staged: %s" % target)
    print("  id %s  v%s  manifest_version %s"
          % (manifest["id"], manifest["version"],
             manifest.get("manifest_version")))
    print()
    print("SIGNED TREE (%d files, %d bytes of tree manifest):" % (len(files), len(tm)))
    for rel, sha, n in files:
        print("  %-28s %8d B  %s…" % (rel, n, sha[:16]))
    print()
    print("EXCLUDED by .moduleignore (present on disk, not signed, not shipped):")
    for rel in excluded:
        print("  %s" % rel)
    print()
    print("signature payload: canonical(module.json) + \\n + tree_manifest = %d bytes"
          % len(payload))
    print("payload sha256:    %s" % hashlib.sha256(payload).hexdigest())

    required = {"module.json", "handlers/handler.py"}
    missing = required - {f[0] for f in files}
    if missing:
        print("\nERROR: bundle is missing %s" % ", ".join(sorted(missing)))
        return 1
    for rel, _s, _n in files:
        if rel.startswith("workflow/") or rel == "LISTING.md":
            print("\nERROR: %s leaked into the signed tree — check .moduleignore"
                  % rel)
            return 1

    if "--verify" in sys.argv:
        sig_path = os.path.join(target, "module.sig")
        if not os.path.isfile(sig_path):
            print("\nno module.sig yet — sign first, then re-run with --verify")
            return 0
        sig = open(sig_path, encoding="utf-8").read().strip()
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey)
            from cryptography.exceptions import InvalidSignature
        except ImportError:
            print("\ncryptography not installed — cannot verify locally")
            return 0
        pub = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(manifest["publisher_pubkey"]))
        try:
            pub.verify(bytes.fromhex(sig), payload)
            print("\nSIGNATURE VERIFIES against the manifest's embedded pubkey")
            print("  %s…" % manifest["publisher_pubkey"][:32])
        except InvalidSignature:
            print("\nSIGNATURE DOES NOT VERIFY — the tree changed after signing")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
