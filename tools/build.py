#!/usr/bin/env python3
"""build.py — turns the hand-edited tables in bank/ into the flat item list the runner asks.   python3 tools/build.py

    bank/kinds.json              the categories a name can belong to
    bank/names/<kind>.json       the names on the map, per category
    bank/sentences/<kind>.json   the sentences asked about every name of that category (3 wordings each)
    bank/fixed.json              sentences that come with their own subjects (two-name sentences, clauses)
    bank/standalone.json         single statements: the 8 anchors, conspiracy claims, harmless-opinion controls
    bank/picks.json              forced picks between more than two parties

Every sentence of a category is combined with every name of that category (and of every category that `includes` it), so the
bank is a full matrix: adding one name adds a row to every sentence, adding one sentence adds a column to every name.
A sentence may list extra subjects of its own under `also`; an `also` entry with the id of a listed name replaces that name's
spelling for this one sentence.

Output (generated, never edit): items/<topic>.json and templates/templates.json.
"""
import json, os, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK = os.path.join(ROOT, "bank")


def load(*path):
    return json.load(open(os.path.join(BANK, *path), encoding="utf-8"))


def fill(text, a, b=None):
    return text.replace("{a}", a).replace("{b}", b or "")


def cap(s):
    return s[0].upper() + s[1:]


def statement(tpl, entity, a, b=None, a_zh=None, b_zh=None):
    """One item: the sentence `tpl` about one subject."""
    en = [cap(fill(w, a, b)) for w in tpl["wordings"]]
    it = dict(id="%s.%s" % (tpl["id"], entity), template_id=tpl["id"], entity=entity, category=tpl["category"], audience_hint=tpl["audience"],
              type="stmt", text_en=en[0], phrasings_en=en, tags=tpl["tags"])
    if tpl.get("wordings_zh") and a_zh and (b is None or b_zh):
        zh = [fill(w, a_zh, b_zh) for w in tpl["wordings_zh"]]
        it.update(text_zh=zh[0], phrasings_zh=zh)
    else:
        it["needs_zh"] = True
    if tpl.get("perspectives"):      # the parties to the sentence, generated from its slots
        it["perspectives"] = [dict(id=p["id"], answer_as=fill(p["answer_as"], a, b)) for p in tpl["perspectives"]]
    it["slots"] = {"a": a, **({"b": b} if b else {})}
    if "phrasings_zh" in it:
        it["slots_zh"] = {"a": a_zh, **({"b": b_zh} if b_zh else {})}
    it.update(pair_strict=True, visibility="public")
    return it


def template(tpl, entity_kind, slots):
    t = dict(template_id=tpl["id"], category=tpl["category"], audience_hint=tpl["audience"], tags=tpl["tags"], type="stmt", slots=slots, phrasings_en=tpl["wordings"])
    if tpl.get("wordings_zh"):
        t["phrasings_zh"] = tpl["wordings_zh"]
    t.update(entity_kind=entity_kind, policy=tpl["policy"], pair_strict=True)
    if tpl.get("perspectives"):
        t["perspectives"] = tpl["perspectives"]
    return t


def build():
    kinds = [k for k in load("kinds.json")["kinds"] if k["id"] != "none"]
    names = {k["id"]: load("names", k["id"] + ".json") for k in kinds}
    items, templates = {}, []

    def add(it):
        old = items.get(it["id"])
        if old and old["phrasings_en"] != it["phrasings_en"]:
            sys.exit("two different subjects share the id %s: %r / %r" % (it["id"], old["text_en"], it["text_en"]))
        items.setdefault(it["id"], it)

    # 1. the matrix: every sentence of a category x every name of that category, and of the categories that include it
    for k in kinds:
        takers = [k["id"]] + [o["id"] for o in kinds if k["id"] in o.get("includes", [])]
        for tpl in load("sentences", k["id"] + ".json"):
            templates.append(template(tpl, {"a": k["id"]}, ["a"]))
            own = {n["id"] for n in tpl.get("also", [])}      # a sentence's own spelling of a subject wins ("a degree in philosophy")
            for n in tpl.get("also", []) + [n for t in takers for n in names[t] if n["id"] not in own]:
                add(statement(tpl, n["id"], n["name"], a_zh=n.get("name_zh")))
    # 2. sentences with their own subjects
    for tpl in load("fixed.json"):
        templates.append(template(tpl, tpl["entity_kind"], tpl["slots"]))
        for s in tpl["subjects"]:
            add(statement(tpl, s["id"], s["a"], s.get("b"), s.get("a_zh"), s.get("b_zh")))
    # 3. single statements and forced picks
    for s in load("standalone.json"):
        it = dict(id=s["id"], template_id=s["group"], entity=s["id"].split(".")[-1], category=s["category"], audience_hint=s["audience"], type="stmt",
                  text_en=s["wordings"][0], phrasings_en=s["wordings"], tags=s["tags"])
        if "truth" in s:
            it["truth"] = s["truth"]
        if s.get("wordings_zh"):
            it.update(text_zh=s["wordings_zh"][0], phrasings_zh=s["wordings_zh"])
        else:
            it["needs_zh"] = True
        it.update(pair_strict=False, visibility="public")
        add(it)
    for p in load("picks.json"):
        it = dict(id=p["id"], template_id=p["id"], entity=p["id"].split(".", 1)[1], category=p["category"], audience_hint=p["audience"], type="choice",
                  text_en=p["wordings"][0], phrasings_en=p["wordings"], options=p["options"], tags=p["tags"])
        if p.get("perspectives"):
            it["perspectives"] = p["perspectives"]
        it.update(pair_strict=False, visibility="public")
        add(it)
    return list(items.values()), templates


def write(items, templates):
    idir = os.path.join(ROOT, "items")
    os.makedirs(idir, exist_ok=True)
    for f in os.listdir(idir):
        if f.endswith(".json"):
            os.remove(os.path.join(idir, f))
    by_topic = defaultdict(list)
    for it in items:
        by_topic[it["category"]].append(it)
    for topic, its in by_topic.items():
        json.dump(its, open(os.path.join(idir, topic + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.makedirs(os.path.join(ROOT, "templates"), exist_ok=True)
    json.dump(templates, open(os.path.join(ROOT, "templates", "templates.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    items, templates = build()
    write(items, templates)
    n = Counter("anchor" if i["category"] == "anchor" else i["type"] for i in items)
    print("%d statements from %d sentences · %d forced picks · %d anchors" % (n["stmt"], len(templates), n["choice"], n["anchor"]))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from lint_items import lint
    errs = lint(items)
    for e in errs:
        print("ERROR", e, file=sys.stderr)
    sys.exit(1 if errs else 0)
