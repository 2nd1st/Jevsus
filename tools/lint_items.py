#!/usr/bin/env python3
"""lint_items.py — item bank gate. Exit 1 on any error.   python3 tools/lint_items.py

The hard rule: every statement is in FACTUAL form. "should" / "ought to" are rejected everywhere
(stmt phrasings, choice stems, choice options): normative sentences measured refusal 0.94 — they test
whether the model dodges normative judgement, not what it holds to be the case.
"""
import glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NORMATIVE = re.compile(r"\b(should|shouldn't|ought\s+to)\b", re.I)
NORMATIVE_ZH = re.compile(r"应该|应当|理应该")
REQUIRED = ("id", "template_id", "entity", "category", "type", "text_en", "phrasings_en")


def lint(items):
    errs, seen = [], set()
    for it in items:
        iid = it.get("id", "?")
        for k in REQUIRED:
            if k not in it:
                errs.append("%s: missing field %r" % (iid, k))
        if iid in seen:
            errs.append("%s: duplicate id" % iid)
        seen.add(iid)
        if it.get("type") not in ("stmt", "choice"):
            errs.append("%s: type must be stmt|choice" % iid)
        if it.get("type") == "choice" and len(it.get("options") or []) < 2:
            errs.append("%s: choice item needs >= 2 options" % iid)
        if it.get("category") == "battery":
            continue
        if it.get("category") == "anchor" and it.get("truth") not in (0, 1):
            errs.append("%s: anchor needs truth 0|1" % iid)
        for lang in ("en", "zh"):
            phs = it.get("phrasings_" + lang)
            if phs is None:
                continue
            if not 1 <= len(phs) <= 3:
                errs.append("%s: phrasings_%s must have 1-3 entries" % (iid, lang))
            for s in list(phs) + (list(it.get("options") or []) if lang == "en" else []):
                if (NORMATIVE if lang == "en" else NORMATIVE_ZH).search(s):
                    errs.append("%s: normative wording (should / ought to) — rewrite in factual form: %r" % (iid, s))
        if it.get("visibility", "public") != "public":
            errs.append("%s: only visibility=public items belong in this dataset" % iid)
    return errs


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(ROOT, "items", "*.json")))
    items = []
    for f in files:
        items += json.load(open(f, encoding="utf-8"))
    errs = lint(items)
    for e in errs:
        print("ERROR", e, file=sys.stderr)
    print("%d items, %d error(s)" % (len(items), len(errs)))
    sys.exit(1 if errs else 0)
