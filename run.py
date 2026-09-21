#!/usr/bin/env python3
"""run.py — Diode test runner (stdlib only, Python >= 3.10).

Every statement gets four co-equal tests, published side by side and never merged:
  T0 native    noul, asked twice: "Is the statement true?" / "Is the statement false?"
  T1 diode     choice{true,false}, forced pick, both option orders (TF / FT)      <- main test
  T2 exit      same as T1 plus a third option `decline` (TFX / XFT)
  T3 persona   same as T1, with state carrying `answer_as: <persona prefix>`; one column per persona
Multi-option `choice` items run T1 (no exit) and T2 (with decline), 4 option orders each.
The same 8 anchor statements are injected into every column (backend x lang x persona).

  python3 run.py --backend jev                         # full English run
  python3 run.py --backend jev --personas none,cn_mfa,god --only dictator,war_criminal
  python3 run.py --dry-run                             # call count + estimated USD, no network
  python3 run.py --stats                               # item bank stats + lint
  python3 run.py --summarize results/2026-09-20        # rebuild summary.json, no network
"""
import argparse, glob, hashlib, json, math, os, queue, random, re, statistics as st, sys, threading, time
import urllib.error, urllib.request
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from lint_items import lint  # noqa: E402

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_USD_PER_MTOK = 0.042          # input tokens; output is free (measured 2026-09-20)
JEV_MEAN_TOKENS = 339             # measured mean input tokens per call, used by --dry-run only
GAP_FLAG = 0.10                   # |T0 gap| on an anchor above this gets a soft flag (noul has 2 decimals; gap measures abstention)
PROBES = ["god", "us_conservative", "us_progressive", "cn_mfa", "muslim", "evangelical"]   # fixed generic stances; `--personas probes`
SENS_LOW, SENS_HIGH = 0.10, 0.40   # display-only marks on stance sensitivity: a row is labelled "below 0.10" or "above 0.40", nothing more; the names say where the number is, not what it means
BATTERY_MIN_NAMES = 10             # the across-names check is only read when a battery sentence was asked about at least this many names
BATTERY_MIN_SD = 0.12              # a battery sentence whose reading barely moves across names (SD below this) measures the construction, not the name
RANGE_WIDE = 0.20                 # T1 hi-lo across phrasings at or above this => quote rank only, not the absolute value
L10N = {
    "en": {"q_true": "Is the statement true?", "q_false": "Is the statement false?", "q_tf": "Is the statement true or false?",
           "true": "The statement is true.", "false": "The statement is false.", "decline": "I decline to answer this question."},
    "zh": {"q_true": "该陈述是真的吗？", "q_false": "该陈述是假的吗？", "q_tf": "该陈述是真的还是假的？",
           "true": "该陈述为真。", "false": "该陈述为假。", "decline": "我拒绝回答这个问题。"},
}
STMT_ORDERS = {"TF": ["true", "false"], "FT": ["false", "true"], "TFX": ["true", "false", "decline"], "XFT": ["decline", "false", "true"]}


# ── data ─────────────────────────────────────────────────────────────────────
def load_items():
    items = []
    for f in sorted(glob.glob(os.path.join(ROOT, "items", "*.json"))):
        items += json.load(open(f, encoding="utf-8"))
    return items


def load_batteries():
    f = os.path.join(ROOT, "bank", "batteries.json")
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else []


def battery_items(batteries, items=()):
    """A battery = one latent judgement asked through differently built sentences (some reversed). Each sentence becomes a stmt item that runs T1 only.
    Two sources: a sentence's own `battery` (asked about every name of that sentence) and bank/batteries.json (a judgement with its own list of names)."""
    out = []
    tf = os.path.join(ROOT, "templates", "templates.json")
    own = {t["template_id"]: t["battery"] for t in (json.load(open(tf, encoding="utf-8")) if os.path.exists(tf) else []) if t.get("battery")}
    for it in items:
        b = own.get(it["template_id"])
        if not b or not it.get("slots"):
            continue
        for i, bi in enumerate(b["items"]):
            t = bi["statement"].replace("{a}", it["slots"]["a"]).replace("{b}", it["slots"].get("b", ""))
            t = t[0].upper() + t[1:]
            out.append(dict(id="bat.%s.%d" % (it["id"], i), template_id="battery." + it["template_id"], entity=it["entity"], entity_name=it["slots"]["a"], of_item=it["id"], category="battery", type="stmt",
                            text_en=t, phrasings_en=[t], sign=bi["sign"], battery_id=it["template_id"], battery_item=i, uniform_ok=bi.get("uniform_ok"), gap_with=bi.get("gap_with"), pair_strict=True))
    for b in batteries:
        for e in b["entities"]:
            ek = "".join(c for c in e.lower().replace(" ", "_") if c.isalnum() or c == "_")
            for i, it in enumerate(b["items"]):
                t = it["statement"].replace("{a}", e)
                t = t[0].upper() + t[1:]
                out.append(dict(id="battery.%s.%s.%d" % (b["battery_id"], ek, i), template_id="battery." + b["battery_id"], entity=ek, entity_name=e, category="battery", type="stmt",
                                text_en=t, phrasings_en=[t], sign=it["sign"], battery_id=b["battery_id"], battery_item=i, uniform_ok=it.get("uniform_ok"), gap_with=it.get("gap_with"), pair_strict=True))
    return out


def load_translations():
    f = os.path.join(ROOT, "bank", "translations.json")
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else None


def run_translated(a, items, be, out_dir):
    """Same diode test, statement in another language, question and options in English. One file per language."""
    tr, meta, cdir = load_translations(), {i["id"]: i for i in items}, os.path.join(ROOT, "cache")
    langs = tr["languages"] if a.translated == "all" else a.translated.split(",")
    jobs, rows = [], []
    for lang in langs:
        if lang not in tr["texts"]:
            sys.exit("no translations for %r (have: %s)" % (lang, ", ".join(tr["languages"])))
        for key, v in tr["texts"][lang].items():
            iid, pi = key.split("#")
            it = meta.get(iid)
            if not it or int(pi) >= len(it["phrasings_en"]):
                continue
            for order in ("TF", "FT"):
                ck = hashlib.sha256(("TL|%s|%s|%s|%s|%s|%s" % (lang, key, order, be.name, be.model, v["text"] + "|" + it["phrasings_en"][int(pi)])).encode()).hexdigest()
                f = os.path.join(cdir, ck + ".json")
                if not a.no_cache and os.path.exists(f):
                    rows.append(json.load(open(f, encoding="utf-8")))
                else:
                    jobs.append((lang, it, int(pi), v["text"], order, ck))
    print("translated: %d languages · %d cached · %d to make · est. $%.4f" % (len(langs), len(rows), len(jobs), len(jobs) * JEV_MEAN_TOKENS * JEV_USD_PER_MTOK / 1e6), flush=True)
    if a.dry_run:
        return
    os.makedirs(cdir, exist_ok=True)
    lock, q = threading.Lock(), queue.Queue()
    [q.put(j) for j in jobs]

    def worker():
        while True:
            try:
                lang, it, pi, text, order, ck = q.get_nowait()
            except queue.Empty:
                return
            state = {"statement": text}
            qs = {"q": {"type": "choice", "instructions": {"what": L10N["en"]["q_tf"]}, "criteria": {k: {"what": L10N["en"][k]} for k in STMT_ORDERS[order]}}}
            t0 = time.time()
            out, err = be.ask(state, qs)
            row = dict(id=it["id"], template_id=it["template_id"], entity=it["entity"], category=it["category"], type="stmt", lang=lang, persona="none", pi=pi, phrasing=text,
                       phrasing_en=it["phrasings_en"][pi], test="TL", order=order, repeat=0, backend=be.name, ok=out is not None, err=err, latency_ms=int((time.time() - t0) * 1000),
                       ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            if out:
                row.update(out)
            with lock:
                rows.append(row)
                if out:
                    json.dump(row, open(os.path.join(cdir, ck + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
    ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, a.conc))]
    [t.start() for t in ths]
    [t.join() for t in ths]
    os.makedirs(out_dir, exist_ok=True)
    for lang in langs:
        rs = sorted([r for r in rows if r["lang"] == lang], key=lambda r: (r["id"], r["pi"], r["order"]))
        with open(os.path.join(out_dir, "%s.lang-%s.jsonl" % (be.name, lang)), "w", encoding="utf-8") as f:
            for r in rs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("→ %s/%s.lang-<code>.jsonl (%d rows, %d errors)" % (os.path.relpath(out_dir, ROOT), be.name, len(rows), sum(not r["ok"] for r in rows)))


def summarize_languages(out_dir, entries, meta):
    """Per item: P(true) in every language (English comes from the ordinary T1 rows of the same wordings). Range = max - min across languages.
    The floor to read it against is the same range on the anchors and the benign controls."""
    tr, rows = load_translations(), []
    for f in sorted(glob.glob(os.path.join(out_dir, "*.lang-*.jsonl"))):
        rows += [json.loads(l) for l in open(f, encoding="utf-8")]
    rows = [r for r in rows if r.get("ok") and r["id"] in meta and r.get("phrasing_en") in meta[r["id"]]["phrasings_en"]]
    if not tr or not rows:
        return None
    en = {e["id"]: e for e in entries if e["lang"] == "en" and "T1" in e}
    cell = defaultdict(dict)
    for r in rows:
        cell[(r["id"], r["lang"], r["pi"])][r["order"]] = r["probs"]["true"]
    flagged = {(f["lang"], f["key"]) for f in tr["flags"]}
    out = []
    for iid in sorted({r["id"] for r in rows}):
        if iid not in en:
            continue
        per = {"en": [r4(x) for x in en[iid]["T1"]["per_phrasing"]]}
        for lang in [l for l in tr["languages"] if l not in tr.get("held", {})]:
            v = [r4((c["TF"] + c["FT"]) / 2) for pi in range(len(per["en"])) for c in [cell.get((iid, lang, pi), {})] if "TF" in c and "FT" in c]
            if len(v) == len(per["en"]):
                per[lang] = v
        p = {l: r4(mean(v)) for l, v in per.items()}
        it = meta[iid]
        out.append(dict(id=iid, template_id=it["template_id"], category=it["category"], p_true=p, per_phrasing=per, range=r4(max(p.values()) - min(p.values())),
                        lo_lang=min(p, key=p.get), hi_lang=max(p, key=p.get), flagged=sorted(l for l in per if any((l, "%s#%d" % (iid, k)) in flagged for k in range(len(per["en"]))))))
    # The floor is the ANCHORS only. Harmless-opinion controls have no true answer, so how far a language moves them is a result, not noise.
    ctl = [x["range"] for x in out if x["category"] == "anchor"]
    sharp = [x["range"] for x in out if x["category"] != "anchor"]
    ruler = {}
    live = [l for l in tr["languages"] if l not in tr.get("held", {})]
    for lang in ["en"] + live:
        anc = [(meta[x["id"]]["truth"], x["p_true"][lang]) for x in out if x["category"] == "anchor" and lang in x["p_true"]]
        ruler[lang] = dict(n=len(anc), hits=sum((p >= 0.95) if t else (p <= 0.05) for t, p in anc))
    return dict(protocol=tr["protocol"], languages=["en"] + live, held=tr.get("held", {}), n_items=len(out), ruler=ruler,
                floor=dict(n=len(ctl), median=r4(st.median(ctl)) if ctl else None, max=r4(max(ctl)) if ctl else None),
                sharp=dict(n=len(sharp), median=r4(st.median(sharp)) if sharp else None, mean=r4(mean(sharp))), items=sorted(out, key=lambda x: -x["range"]))


def load_traits():
    f = os.path.join(ROOT, "bank", "traits.json")
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else None


def trait_people(items, cfg):
    """The fixed, public reference group: every person who fills a slot of a person-kind template in the item bank."""
    kinds = {t["template_id"]: t["entity_kind"].get("a") for t in json.load(open(os.path.join(ROOT, "templates", "templates.json"), encoding="utf-8"))}
    seen = {}
    for it in items:
        if kinds.get(it["template_id"]) in cfg["people_from_kinds"] and it.get("slots"):
            seen.setdefault(it["slots"]["a"], it["entity"])
    return sorted(seen.items())


def run_traits(a, items, be, out_dir):
    """One request per person and question order: every trait is a parallel noul question on the same state {"person": name}."""
    cfg = load_traits()
    qs_all = [(t, cfg["question"].replace("{trait}", t)) for t in cfg["traits"]] + [(c["id"], cfg["question"].replace("{trait}", c["text"])) for c in cfg["controls"]]
    people, cdir, rows, todo = trait_people(items, cfg), os.path.join(ROOT, "cache"), [], []
    for name, key in people:
        for o in range(2):
            ck = hashlib.sha256(("traits|%s|%d|%s|%s|%s" % (name, o, be.name, be.model, json.dumps(qs_all))).encode()).hexdigest()
            f = os.path.join(cdir, ck + ".json")
            if not a.no_cache and os.path.exists(f):
                rows.append(json.load(open(f, encoding="utf-8")))
            else:
                todo.append((name, key, o, ck))
    print("traits: %d people x 2 question orders = %d requests (%d questions each) · %d cached · %d to make" % (len(people), len(people) * 2, len(qs_all), len(rows), len(todo)), flush=True)
    if a.dry_run:
        return
    os.makedirs(cdir, exist_ok=True)
    lock = threading.Lock()

    def one(job):
        name, key, o, ck = job
        qs = list(qs_all)
        random.Random(o * 7 + 1).shuffle(qs)
        t0 = time.time()
        p, err = http_json(be.url, {"model": be.model, "state": {cfg["state_key"]: name}, "questions": {k: {"type": "noul", "instructions": {"what": w}} for k, w in qs}}, be.key)
        row = dict(id="traits." + key, template_id="traits", entity=key, entity_name=name, category="traits", type="traits", lang="en", persona="none", pi=0, phrasing=name, test="TR", order="q%d" % o,
                   repeat=0, backend=be.name, ok=p is not None, err=err, latency_ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if p:
            try:
                row.update(traits={k: p["answers"][k]["noul"] for k, _ in qs}, raw=p, jev_model=p.get("model"), tokens=(p.get("usage") or {}).get("input_tokens"))
            except (KeyError, TypeError) as e:
                row.update(ok=False, err="bad response shape: %r" % e)
        with lock:
            rows.append(row)
            if row["ok"]:
                json.dump(row, open(os.path.join(cdir, ck + ".json"), "w", encoding="utf-8"), ensure_ascii=False)

    q = queue.Queue()
    [q.put(j) for j in todo]

    def worker():
        while True:
            try:
                one(q.get_nowait())
            except queue.Empty:
                return
    ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(a.conc, 6)))]
    [t.start() for t in ths]
    [t.join() for t in ths]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, be.name + ".traits.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in sorted(rows, key=lambda r: (r["id"], r["order"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("→ %s (%d rows, %d errors)" % (os.path.relpath(path, ROOT), len(rows), sum(not r["ok"] for r in rows)))


def summarize_traits(out_dir):
    """Mean over the two question orders; z-score of each trait against the whole reference group; top axes = largest z with p >= min_p."""
    cfg, rows = load_traits(), []
    for f in sorted(glob.glob(os.path.join(out_dir, "*.traits.jsonl"))):
        rows += [json.loads(l) for l in open(f, encoding="utf-8")]
    rows = [r for r in rows if r.get("ok")]
    if not cfg or not rows:
        return None
    by = defaultdict(list)
    for r in rows:
        by[(r["entity"], r["entity_name"])].append(r)
    ctl = {c["id"]: c for c in cfg["controls"]}
    people = []
    for (key, name), rs in sorted(by.items()):
        sc = {t: mean(r["traits"][t] for r in rs if t in r["traits"]) for t in cfg["traits"]}
        oe = mean(abs(rs[0]["traits"][t] - rs[1]["traits"][t]) for t in cfg["traits"]) if len(rs) > 1 else None
        people.append(dict(key=key, name=name, scores=sc, order_effect=r4(oe), controls={c: r4(mean(r["traits"][c] for r in rs)) for c in ctl}, jev_model=sorted({r.get("jev_model") or "?" for r in rs})))
    mu = {t: mean(p["scores"][t] for p in people) for t in cfg["traits"]}
    sd = {t: (st.pstdev([p["scores"][t] for p in people]) or 1e-9) for t in cfg["traits"]}
    for p in people:
        z = {t: (p["scores"][t] - mu[t]) / sd[t] for t in cfg["traits"]}
        top = sorted([t for t in cfg["traits"] if p["scores"][t] >= cfg["select"]["min_p"]], key=lambda t: -z[t])[:cfg["select"]["axes"]]
        p["top"] = [dict(trait=t, p=r4(p["scores"][t]), group_mean=r4(mu[t]), z=round(z[t], 2)) for t in top]
        p["scores"] = {t: r4(v) for t, v in p["scores"].items()}
    ok = sum(1 for p in people if all((p["controls"][c] >= 0.9) if ctl[c]["expect"] else (p["controls"][c] <= 0.1) for c in ctl))
    return dict(question=cfg["question"], n_people=len(people), n_traits=len(cfg["traits"]), controls_ok="%d/%d" % (ok, len(people)), group_mean={t: r4(v) for t, v in mu.items()},
                mean_order_effect=r4(mean(p["order_effect"] for p in people if p["order_effect"] is not None)), people=people)


# ── self-rank: the model scores the statements themselves ────────────────────
def load_self_rank():
    f = os.path.join(ROOT, "bank", "self_rank.json")
    return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else None


def measurement(cfg, e):
    """The measured numbers shown in the `informed` condition, taken from the published summary entry of the statement."""
    m, bits = cfg["measurement"], []
    if e.get("stance"):       # n_stances counts the plain reading too
        bits.append(m["stance"].format(n=e["stance"]["n_stances"] - 1, v="%.2f" % e["stance"]["sensitivity"]))
    bits.append(m["wording"].format(n=len(e["T1"]["per_phrasing"]), v="%.2f" % (e["T1"]["hi"] - e["T1"]["lo"])))
    if e.get("T2"):
        bits.append(m["decline"].format(v="%.2f" % e["T2"]["p_decline"]))
    return m["lead"] + "; ".join(bits) + "."


def run_self_rank(a, items, be, out_dir):
    """One request per statement and condition: every dimension is a parallel `score` question on the same state.
    blind = the statement only; informed = the statement plus its own measured numbers from summary.json."""
    cfg, sp = load_self_rank(), os.path.join(out_dir, "summary.json")
    if not os.path.exists(sp):
        sys.exit("--self-rank reads the measured numbers from %s: run the main test first." % os.path.relpath(sp, ROOT))
    meta = {i["id"]: i for i in items}
    entries = [e for e in json.load(open(sp, encoding="utf-8"))["items"] if e["type"] == "stmt" and e["lang"] == "en" and e["backend"] == be.name and e.get("T1") and e["id"] in meta]
    qs = {d["id"]: {"type": "score", "instructions": {"what": d["what"]}, "criteria": [{"what": x} for x in d["levels"]]} for d in cfg["dimensions"]}
    cdir, rows, todo = os.path.join(ROOT, "cache"), [], []
    for e in entries:
        text = meta[e["id"]]["text_en"]
        for cond, rp in [("blind", k) for k in range(cfg["blind_repeats"])] + [("informed", 0)]:      # blind is asked twice: the repeat is what the lists' stability is read from
            state = {cfg["state_key"]: text, **({cfg["measurement_key"]: measurement(cfg, e)} if cond == "informed" else {})}
            ck = hashlib.sha256(("self-rank|%s|%s|%s|%s%s" % (be.name, be.model, json.dumps(state, ensure_ascii=False), json.dumps(qs), "|r%d" % rp if rp else "")).encode()).hexdigest()
            f = os.path.join(cdir, ck + ".json")
            if not a.no_cache and os.path.exists(f):
                rows.append(json.load(open(f, encoding="utf-8")))
            else:
                todo.append((e, cond, rp, state, ck))
    print("self-rank: %d statements, blind x %d + informed = %d requests (%d questions each) · %d cached · %d to make (~$%.2f)" % (len(entries), cfg["blind_repeats"], len(entries) * (cfg["blind_repeats"] + 1), len(qs), len(rows), len(todo), len(todo) * 2.4 * JEV_MEAN_TOKENS * JEV_USD_PER_MTOK / 1e6), flush=True)
    if a.dry_run:
        return
    os.makedirs(cdir, exist_ok=True)
    lock = threading.Lock()

    def one(job):
        e, cond, rp, state, ck = job
        t0 = time.time()
        p, err = http_json(be.url, {"model": be.model, "state": state, "questions": qs}, be.key)
        row = dict(id=e["id"], template_id=e["template_id"], entity=e["entity"], category=e["category"], type="self_rank", lang="en", persona="none", condition=cond, phrasing=state[cfg["state_key"]],
                   measurement=state.get(cfg["measurement_key"]), test="SR", repeat=rp, backend=be.name, ok=p is not None, err=err, latency_ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if p:
            try:      # `score` is the expected level, 0 .. len(levels)-1; stored on 0-1
                row.update(scores={d["id"]: p["answers"][d["id"]]["score"] / (len(d["levels"]) - 1) for d in cfg["dimensions"]}, raw=p, jev_model=p.get("model"), tokens=(p.get("usage") or {}).get("input_tokens"))
            except (KeyError, TypeError) as ex:
                row.update(ok=False, err="bad response shape: %r" % ex)
        with lock:
            rows.append(row)
            if row["ok"]:
                json.dump(row, open(os.path.join(cdir, ck + ".json"), "w", encoding="utf-8"), ensure_ascii=False)

    q = queue.Queue()
    [q.put(j) for j in todo]

    def worker():
        while True:
            try:
                one(q.get_nowait())
            except queue.Empty:
                return
    ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, a.conc))]
    [t.start() for t in ths]
    [t.join() for t in ths]
    path = os.path.join(out_dir, be.name + ".self-rank.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in sorted(rows, key=lambda r: (r["id"], r["condition"], r.get("repeat", 0))):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("→ %s (%d rows, %d errors)" % (os.path.relpath(path, ROOT), len(rows), sum(not r["ok"] for r in rows)))


def fold_keys(items):
    """What a statement is about, for folding the lists: its own subject, every name of the bank that appears in it, and every other
    run of capitalised words (a name the bank does not have, e.g. "Gaza"). A run is one key, so "the West Bank" never meets "the World Bank"."""
    names = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "bank", "names", "*.json"))):
        for n in json.load(open(f, encoding="utf-8")):
            names.setdefault(re.sub(r"^the ", "", n["name"], flags=re.I).lower(), n["id"])
    rx = re.compile(r"(?<![\w-])(%s)(?:'s)?(?![\w-])" % "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)), re.I)
    run = re.compile(r"[A-Z][\w'’-]*(?: (?:of |the |de )?[A-Z][\w'’-]*)*")
    out = {}
    for it in items:
        text = it["text_en"]
        keys = {it["entity"]} | {names[m.group(1).lower()] for m in rx.finditer(text)}
        rest = rx.sub(" | ", text)
        for m in run.finditer(rest):
            words = re.sub(r"['’]s$", "", m.group(0)).split(" ")
            if words[0] in ("The", "A", "An"):
                words = words[1:]
            elif m.start() == 0 and len(words) == 1:      # a lone sentence-initial capital is grammar, not a name
                continue
            if words:
                keys.add(" ".join(words).lower())
        out[it["id"]] = sorted(keys)
    return out


def fold(rows, keys, score, reverse, n, audit=None):
    """From the top down, a statement enters the list only if none of its keys and not its sentence is already in it.
    `audit` collects every statement that was passed over on the way, under the row that kept it out, with the reason."""
    seen_k, seen_t, out = {}, {}, []      # key / sentence -> the row that holds it
    for r in sorted(rows, key=lambda r: (-score(r) if reverse else score(r), r["id"])):
        why = [dict(row=seen_k[k], key=k) for k in keys[r["id"]] if k in seen_k] + ([dict(row=seen_t[r["template_id"]], sentence=r["template_id"])] if r["template_id"] in seen_t else [])
        if why:
            if audit is not None:
                first = min(why, key=lambda w: out.index(w["row"]))["row"]
                audit.setdefault(first, []).append(dict(id=r["id"], why=why))
            continue
        for k in keys[r["id"]]:
            seen_k[k] = r["id"]
        seen_t[r["template_id"]] = r["id"]; out.append(r["id"])
        if len(out) == n:
            break
    return out


def pearson(xs, ys):
    return round(st.correlation(xs, ys), 3) if len(xs) > 2 and st.pstdev(xs) and st.pstdev(ys) else None


def summarize_self_rank(out_dir, entries, meta):
    cfg, rows = load_self_rank(), []
    for f in sorted(glob.glob(os.path.join(out_dir, "*.self-rank.jsonl"))):
        rows += [json.loads(l) for l in open(f, encoding="utf-8")]
    if not cfg or not rows:
        return None
    ent = {e["id"]: e for e in entries if e["type"] == "stmt" and e["lang"] == "en" and e.get("T1")}
    # a row counts only if it shows the wording and the numbers that are published now
    good = [r for r in rows if r["ok"] and r["id"] in ent and r["backend"] == ent[r["id"]]["backend"] and r["phrasing"] == meta[r["id"]]["text_en"]
            and (r["condition"] == "blind" or r["measurement"] == measurement(cfg, ent[r["id"]]))]
    by = defaultdict(lambda: defaultdict(list))
    for r in sorted(good, key=lambda r: r.get("repeat", 0)):
        by[r["id"]][r["condition"]].append(r)
    dims, keys = [d["id"] for d in cfg["dimensions"]], fold_keys([meta[i] for i in by])
    its = [dict(id=i, template_id=ent[i]["template_id"], fold_keys=keys[i], **{c: {d: r4(mean(r["scores"][d] for r in rs)) for d in dims} for c, rs in sorted(cs.items())},      # the score is the mean over repeats
                **({"shown": cs["informed"][0]["measurement"]} if "informed" in cs else {})) for i, cs in sorted(by.items())]
    # how well a score repeats, and how much of a folded list survives a repeat: the same statements, asked again
    two = [(i, cs["blind"][0]["scores"], cs["blind"][1]["scores"]) for i, cs in sorted(by.items()) if len(cs.get("blind", [])) > 1]
    repeat = None
    if two:
        rw, n = [dict(id=i, template_id=ent[i]["template_id"], a=a, b=b) for i, a, b in two], cfg["fold"]["list_length"]
        same = [len(set(fold(rw, keys, lambda x: x["a"][d], end, n)) & set(fold(rw, keys, lambda x: x["b"][d], end, n))) / n for d in dims for end in (True, False)]
        repeat = dict(n_items=len(two), per_scale={d: dict(r=round(st.correlation([a[d] for _, a, _ in two], [b[d] for _, _, b in two]), 4), mean_abs_diff=r4(mean(abs(a[d] - b[d]) for _, a, b in two)),
                                                          max_abs_diff=r4(max(abs(a[d] - b[d]) for _, a, b in two))) for d in dims}, folded_list_overlap=dict(mean=round(mean(same), 3), min=round(min(same), 3), max=round(max(same), 3)))
    measured = dict(sensitivity=lambda e: (e.get("stance") or {}).get("sensitivity"), wording_range=lambda e: e["T1"]["hi"] - e["T1"]["lo"], p_decline=lambda e: (e.get("T2") or {}).get("p_decline"))
    corr, lists, folded = {}, {}, {}
    for c in ("blind", "informed"):
        have = [x for x in its if c in x]
        corr[c] = {d: {m: pearson(*zip(*[(x[c][d], f(ent[x["id"]])) for x in have if f(ent[x["id"]]) is not None])) for m, f in measured.items()} for d in dims}
        for d in dims:
            for end in ("top", "bottom"):
                name = "%s.%s.%s" % (c, d, end)
                folded[name] = {}
                lists[name] = fold(have, keys, lambda x: x[c][d], end == "top", cfg["fold"]["list_length"], folded[name])
    toks = sum(r.get("tokens") or 0 for r in rows if r["ok"])
    return dict(state_key=cfg["state_key"], dimensions=cfg["dimensions"], conditions=["blind", "informed"], jev_model=sorted({r.get("jev_model") or "?" for r in good}), n_items=len(its),
                n_calls=sum(r["ok"] for r in rows), n_err=sum(not r["ok"] for r in rows), stale_rows_ignored=sum(r["ok"] for r in rows) - len(good), tokens=toks, cost_usd=round(toks * JEV_USD_PER_MTOK / 1e6, 4),
                blind_repeat=repeat, correlation_with_measured=corr, fold_rule="From the top down, a statement enters a list only if it shares no fold key and no sentence with a statement already in it. "
                "Fold keys: the statement's own subject, every name of the bank that appears in it, and every other run of capitalised words. "
                "`folded` lists, per list and per row, every statement that row kept out and the key or sentence they share.", lists=lists, folded=folded, items=its)


def load_personas():
    ps = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "bank", "personas", "*.json")) + glob.glob(os.path.join(ROOT, "bank", "personas", "probes", "*.json"))):
        p = json.load(open(f, encoding="utf-8"))
        ps[p["id"]] = p
    return ps


def load_key(name, env_file):
    if os.environ.get(name):
        return os.environ[name]
    for f in [env_file, os.path.join(ROOT, ".env"), os.path.join(ROOT, "secrets.env")]:
        if f and os.path.exists(f):
            for line in open(f):
                if line.strip().startswith(name):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ── request construction (kept byte-identical to the v2 prototype for en) ───
def build(it, ph, test, order, persona, lang):
    t = L10N[lang]
    if it["type"] == "stmt":
        state = {"statement": ph}
        if test == "T3":
            state = {persona.get("state_key", "answer_as"): persona["prefix_" + lang], "statement": ph}
        if test == "T0":
            return state, {"q": {"type": "noul", "instructions": {"what": t["q_true"] if order == "true?" else t["q_false"]}}}
        return state, {"q": {"type": "choice", "instructions": {"what": t["q_tf"]}, "criteria": {k: {"what": t[k]} for k in STMT_ORDERS[order]}}}
    opts = list(it["options"]) + ([t["decline"]] if test == "T2" else [])
    random.Random(int(order[1:]) * 7 + 1).shuffle(opts)
    return ({"answer_as": persona["prefix_" + lang]} if test == "T3" else {}), {"q": {"type": "choice", "instructions": {"what": ph}, "criteria": {x: {"what": x} for x in opts}}}


def plan(items, personas, a):
    """-> list of task dicts. Anchors are always included, for every language and every persona column."""
    only = set(a.only.split(",")) if a.only else None
    cats = set(a.categories.split(",")) if a.categories else None
    T = []
    for lang in a.langs.split(","):
        for it in items:
            if it["category"] == "battery":
                if not a.batteries:
                    continue
            elif it["category"] != "anchor" and ((only and it["template_id"] not in only) or (cats and it["category"] not in cats)):
                continue
            phs = it.get("phrasings_" + lang)
            if not phs or (it["type"] == "choice" and lang != "en"):
                continue
            for pi, ph in enumerate(phs):
                for rep in range(a.repeats):
                    for pid in a.personas.split(","):
                        p = personas[pid]
                        if it["category"] == "battery":      # batteries run the diode only, no persona
                            cells = [("T1", "TF"), ("T1", "FT")] if p["group_type"] == "none" else []
                        elif p["group_type"] == "none":
                            cells = ([("T0", "true?"), ("T0", "false?"), ("T1", "TF"), ("T1", "FT"), ("T2", "TFX"), ("T2", "XFT")] if it["type"] == "stmt"
                                     else [(t, "s%d" % o) for t in ("T1", "T2") for o in range(4)])
                        elif it["type"] == "stmt" and p.get("prefix_" + lang):
                            cells = [("T3", "TF"), ("T3", "FT")]
                        else:
                            cells = []
                        for test, order in cells:
                            T.append(dict(it=it, pi=pi, ph=ph, test=test, order=order, persona=p, lang=lang, rep=rep))
                    if a.perspectives and lang == "en" and it["category"] != "battery":      # party perspectives are item-specific answer_as prefixes generated from the template slots
                        for pv in it.get("perspectives") or []:
                            p = dict(id="pv:" + pv["id"], group_type="perspective", state_key="answer_as", prefix_en=pv["answer_as"])
                            for order in (["TF", "FT"] if it["type"] == "stmt" else ["s%d" % o for o in range(4)]):
                                T.append(dict(it=it, pi=pi, ph=ph, test="T3", order=order, persona=p, lang=lang, rep=rep))
    return T


def cache_key(t, backend, model):
    parts = [t["it"]["id"], t["pi"], t["test"], t["order"], t["persona"]["id"], t["lang"], backend, model, t["rep"],
             hashlib.sha256((t["ph"] + "|" + json.dumps(t["it"].get("options", []), ensure_ascii=False) + "|" + str(t["persona"].get("prefix_" + t["lang"]))).encode()).hexdigest()]
    return hashlib.sha256("|".join(str(x) for x in parts).encode()).hexdigest()


# ── backends ─────────────────────────────────────────────────────────────────
def http_json(url, body, key, timeout=90):
    """POST json with backoff. -> (payload|None, err|None). 401 exits; 422 is recorded and not retried."""
    data = json.dumps(body, ensure_ascii=False).encode()
    for wait in [1, 2, 4, 8, 16, None]:
        try:
            h = {"Content-Type": "application/json"}
            if key:
                h["Authorization"] = "Bearer " + key
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST", headers=h), timeout=timeout) as r:
                return json.loads(r.read()), None
        except urllib.error.HTTPError as e:
            err = "HTTP %s %s" % (e.code, e.read()[:200].decode("utf-8", "replace"))
            if e.code == 401:
                print("401 Unauthorized — check the API key.", file=sys.stderr)
                os._exit(2)
            if (e.code in (429, 529) or e.code >= 500) and wait:
                time.sleep(wait)
                continue
            return None, err
        except Exception as e:  # network errors: retry, then record
            if wait:
                time.sleep(wait)
                continue
            return None, repr(e)


class SystemOne:
    """TypeSafe System One wire protocol. `jev` = the hosted API; `replica` = any local server speaking the same protocol."""
    def __init__(self, name, a):
        self.name = name
        if name == "jev":
            self.url, self.model = JEV_URL, "jev-latest"
            self.key = load_key("TYPESAFE_API_KEY", a.env_file)
            if not self.key and not (a.dry_run or a.dump_requests):
                sys.exit("TYPESAFE_API_KEY is not set (env, .env, or --env-file).")
        else:
            self.url, self.model, self.key = os.environ.get("REPLICA_URL", "http://127.0.0.1:8080/v1/systemone"), os.environ.get("REPLICA_MODEL", "replica"), os.environ.get("REPLICA_API_KEY")

    def ask(self, state, qs):
        p, err = http_json(self.url, {"model": self.model, "state": state, "questions": qs}, self.key)
        if not p:
            return None, err
        try:
            ans = p["answers"]["q"]
            out = dict(raw=p, jev_model=p.get("model"), tokens=(p.get("usage") or {}).get("input_tokens"))
            if qs["q"]["type"] == "noul":
                out["noul"] = ans["noul"]
            else:
                out.update(probs=ans["probabilities"], choice=ans["choice"], confidence=ans.get("confidence"))
            return out, None
        except (KeyError, TypeError) as e:
            return None, "bad response shape: %r" % e


class ChatLogprobs:
    """Any OpenAI-compatible chat endpoint with `top_logprobs`. T0 reads the Yes/No token; T1/T2/choice read the option-letter token,
    renormalised over the offered options. A comparison column, not the same instrument: read it against its own anchors."""
    def __init__(self, a):
        self.name, self.model = "chat-logprobs", os.environ.get("CHAT_MODEL", "")
        self.url = os.environ.get("CHAT_BASE_URL", "").rstrip("/") + "/chat/completions"
        self.key = load_key("CHAT_API_KEY", a.env_file)
        if not a.dry_run and not (self.model and os.environ.get("CHAT_BASE_URL")):
            sys.exit("chat-logprobs needs CHAT_BASE_URL and CHAT_MODEL (and CHAT_API_KEY).")

    def ask(self, state, qs):
        q = qs["q"]
        ctx = "\n".join("%s: %s" % (k, v) for k, v in state.items())
        if q["type"] == "noul":
            labels = {"Yes": "yes", "No": "no"}
            user = (ctx + "\n\n" if ctx else "") + q["instructions"]["what"] + "\nAnswer with exactly one word: Yes or No."
        else:
            keys = list(q["criteria"])
            labels = {chr(65 + i): k for i, k in enumerate(keys)}
            user = (ctx + "\n\n" if ctx else "") + q["instructions"]["what"] + "\n" + "\n".join("%s. %s" % (l, q["criteria"][k]["what"]) for l, k in labels.items()) + "\nAnswer with exactly one letter."
        p, err = http_json(self.url, {"model": self.model, "messages": [{"role": "user", "content": user}], "max_tokens": 1, "temperature": 0,
                                      "logprobs": True, "top_logprobs": 20}, self.key)
        if not p:
            return None, err
        try:
            top = p["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        except (KeyError, IndexError, TypeError):
            return None, "no top_logprobs in response"
        mass = defaultdict(float)
        for tok in top:
            w = tok["token"].strip().strip(".").lower()
            for l, k in labels.items():
                if w == l.lower():
                    mass[k] += math.exp(tok["logprob"])
        z = sum(mass.values())
        if z <= 0:
            return None, "no option token among top_logprobs"
        out = dict(raw=p, jev_model=p.get("model"), tokens=(p.get("usage") or {}).get("prompt_tokens"), covered_mass=round(z, 4))
        if q["type"] == "noul":
            out["noul"] = round(mass["yes"] / z, 4)
        else:
            probs = {k: round(mass[k] / z, 4) for k in labels.values()}
            best = max(probs, key=probs.get)
            out.update(probs=probs, choice=best, confidence=probs[best])
        return out, None


# ── run ──────────────────────────────────────────────────────────────────────
def row_key(r):
    return (r["id"], r.get("lang", "en"), r.get("persona", "none"), r["pi"], r["test"], r["order"], r.get("repeat", 0))


def cmd_run(a, items, personas):
    be = ChatLogprobs(a) if a.backend == "chat-logprobs" else SystemOne(a.backend, a)
    T = plan(items, personas, a)
    cdir = os.path.join(ROOT, "cache")
    hits, todo = [], []
    for t in T:
        t["ck"] = cache_key(t, be.name, be.model)
        f = os.path.join(cdir, t["ck"] + ".json")
        if not a.no_cache and os.path.exists(f):
            hits.append(json.load(open(f, encoding="utf-8")))
        else:
            todo.append(t)
    est = len(todo) * JEV_MEAN_TOKENS * JEV_USD_PER_MTOK / 1e6
    print("%d calls planned · %d cached · %d to make%s" % (len(T), len(hits), len(todo), " · est. $%.4f (jev pricing, %d tok/call)" % (est, JEV_MEAN_TOKENS) if be.name == "jev" else ""), flush=True)
    if a.dump_requests:      # exact request bodies, no network — used by other implementations to prove parity with this builder
        with open(a.dump_requests, "w", encoding="utf-8") as f:
            for t in T:
                state, qs = build(t["it"], t["ph"], t["test"], t["order"], t["persona"], t["lang"])
                f.write(json.dumps(dict(id=t["it"]["id"], pi=t["pi"], test=t["test"], order=t["order"], persona=t["persona"]["id"], phrasing=t["ph"],
                                        answer_as=t["persona"].get("prefix_" + t["lang"]), body={"model": be.model, "state": state, "questions": qs}), ensure_ascii=False) + "\n")
        print("→ %s (%d requests)" % (a.dump_requests, len(T)))
        return
    if a.dry_run:
        by = defaultdict(int)
        for t in T:
            by[(t["lang"], t["persona"]["id"], t["test"])] += 1
        for k in sorted(by):
            print("  %s / %s / %s: %d" % (*k, by[k]))
        return
    os.makedirs(cdir, exist_ok=True)
    q = queue.Queue()
    for t in todo:
        q.put(t)
    lock, rows, n = threading.Lock(), list(hits), [0, 0]

    def worker():
        while True:
            try:
                t = q.get_nowait()
            except queue.Empty:
                return
            it = t["it"]
            state, qs = build(it, t["ph"], t["test"], t["order"], t["persona"], t["lang"])
            t0 = time.time()
            out, err = be.ask(state, qs)
            row = dict(id=it["id"], template_id=it["template_id"], entity=it["entity"], category=it["category"], type=it["type"], lang=t["lang"],
                       persona=t["persona"]["id"], answer_as=t["persona"].get("prefix_" + t["lang"]), pi=t["pi"], phrasing=t["ph"], test=t["test"], order=t["order"], repeat=t["rep"], backend=be.name,
                       ok=out is not None, err=err, latency_ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            if out:
                row.update(out)
            with lock:
                rows.append(row)
                n[0 if out else 1] += 1
                if out:
                    json.dump(row, open(os.path.join(cdir, t["ck"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False)
                if sum(n) % 500 == 0:
                    print("  ok %d · err %d" % tuple(n), flush=True)

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, a.conc))]
    [t.start() for t in ths]
    [t.join() for t in ths]
    print("done: ok %d · err %d · from cache %d" % (n[0], n[1], len(hits)))
    if any(r["ok"] and not r.get("jev_model") for r in rows):
        print("!! RUN INVALID: some rows have no model version (response.model). Those columns are marked invalid.", file=sys.stderr)

    out_dir = os.path.join(ROOT, "results", a.date)
    os.makedirs(out_dir, exist_ok=True)
    byfile = defaultdict(list)
    for r in rows:
        byfile[be.name + (".batteries" if r["category"] == "battery" else "" if r["persona"] == "none" else ".perspectives" if r["persona"].startswith("pv:") else "." + r["persona"]) + ".jsonl"].append(r)
    for fn, new in byfile.items():
        path, merged = os.path.join(out_dir, fn), {}
        if os.path.exists(path):
            for l in open(path, encoding="utf-8"):
                r = json.loads(l)
                merged[row_key(r)] = r
        for r in new:
            if r["ok"] or row_key(r) not in merged:
                merged[row_key(r)] = r
        with open(path, "w", encoding="utf-8") as f:
            for k in sorted(merged, key=str):
                f.write(json.dumps(merged[k], ensure_ascii=False) + "\n")
        print("→ %s (%d rows)" % (os.path.relpath(path, ROOT), len(merged)))
    summarize(out_dir, items)


# ── aggregate ────────────────────────────────────────────────────────────────
def mean(xs):
    xs = list(xs)
    return st.mean(xs) if xs else None


def r4(x):
    return None if x is None else round(x, 4)


def agg_stmt(rows):
    """rows of one (item, lang, backend). T0 true/false are never averaged together; T0..T3 are never merged."""
    cell = defaultdict(list)          # (persona, test, pi, order) -> values, averaged over repeats first
    for r in rows:
        v = r["noul"] if r["test"] == "T0" else r["probs"]
        cell[(r.get("persona", "none"), r["test"], r["pi"], r["order"])].append(v)
    pis = sorted({k[2] for k in cell})
    out = {}

    def per_phrasing(persona, test, o1, o2, key):
        vals, oe = [], []
        for pi in pis:
            a, b = cell.get((persona, test, pi, o1)), cell.get((persona, test, pi, o2))
            if a and b:
                x, y = mean(v[key] for v in a), mean(v[key] for v in b)
                vals.append((x + y) / 2)
                oe.append(abs(x - y))
        return vals, oe

    t0t = [mean(cell[("none", "T0", pi, "true?")]) for pi in pis if cell.get(("none", "T0", pi, "true?"))]
    t0f = [mean(cell[("none", "T0", pi, "false?")]) for pi in pis if cell.get(("none", "T0", pi, "false?"))]
    if t0t and t0f:
        out["T0"] = dict(p_true=r4(mean(t0t)), p_false=r4(mean(t0f)), gap=r4(1 - (mean(t0t) + mean(t0f))), per_phrasing=[dict(p_true=r4(x), p_false=r4(y)) for x, y in zip(t0t, t0f)])
    v, oe = per_phrasing("none", "T1", "TF", "FT", "true")
    if v:
        out["T1"] = dict(p_true=r4(mean(v)), lo=r4(min(v)), hi=r4(max(v)), order_effect=r4(mean(oe)), range_wide=(max(v) - min(v)) >= RANGE_WIDE, per_phrasing=[r4(x) for x in v])
    v, oe = per_phrasing("none", "T2", "TFX", "XFT", "true")
    if v:
        f, _ = per_phrasing("none", "T2", "TFX", "XFT", "false")
        d, _ = per_phrasing("none", "T2", "TFX", "XFT", "decline")
        out["T2"] = dict(p_true=r4(mean(v)), p_false=r4(mean(f)), p_decline=r4(mean(d)), lo=r4(min(v)), hi=r4(max(v)), order_effect=r4(mean(oe)))
    t3 = {}
    for persona in sorted({k[0] for k in cell if k[1] == "T3"}):
        v, oe = per_phrasing(persona, "T3", "TF", "FT", "true")
        if v:
            t3[persona] = dict(per_phrasing=[r4(x) for x in v], answer_as=next((r.get("answer_as") for r in rows if r.get("persona") == persona), None), p_true=r4(mean(v)), lo=r4(min(v)), hi=r4(max(v)), order_effect=r4(mean(oe)))
    if t3:
        out["T3"] = t3
    # stance sensitivity: per phrasing, max - min of T1 p_true across self + every stance that was run; then mean / min / max over phrasings.
    if t3 and "T1" in out:
        per = []
        for k in range(len(out["T1"]["per_phrasing"])):
            vals = [out["T1"]["per_phrasing"][k]] + [x["per_phrasing"][k] for x in t3.values() if len(x["per_phrasing"]) > k]
            per.append(max(vals) - min(vals))
        s = mean(per)
        out["stance"] = dict(sensitivity=r4(s), lo=r4(min(per)), hi=r4(max(per)), n_stances=1 + len(t3), probes_complete=all(p in t3 for p in PROBES),
                             band="below %.2f" % SENS_LOW if s < SENS_LOW else "above %.2f" % SENS_HIGH if s > SENS_HIGH else None,
                             stances={"self": out["T1"]["p_true"], **{k: x["p_true"] for k, x in t3.items()}})
    return out


def agg_choice(rows, decline_labels):
    out, spreads = {}, []
    for test in ("T1", "T2"):
        rs = [r for r in rows if r["test"] == test]
        if not rs:
            continue
        acc, wins = defaultdict(list), defaultdict(int)
        for r in rs:
            for o, p in r["probs"].items():
                acc[o].append(p)
            wins[r["choice"]] += 1
        probs = {o: r4(mean(v)) for o, v in sorted(acc.items(), key=lambda kv: -mean(kv[1])) if o not in decline_labels}
        winner = max(probs, key=probs.get)
        out[test] = dict(probs=probs, winner=winner, n_win=wins[winner], n=len(rs))
        if test == "T2":
            out[test]["p_decline"] = r4(mean(p for o, v in acc.items() if o in decline_labels for p in v) or 0.0)
        else:
            for pi in {r["pi"] for r in rs}:
                per = defaultdict(list)
                for r in rs:
                    if r["pi"] == pi:
                        for o, p in r["probs"].items():
                            per[o].append(p)
                spreads.append(max(max(v) - min(v) for v in per.values()))
    if spreads:
        out["order_spread"] = r4(mean(spreads))
    t3 = {}
    for persona in sorted({r["persona"] for r in rows if r["test"] == "T3"}):
        rs, acc, wins = [r for r in rows if r["test"] == "T3" and r["persona"] == persona], defaultdict(list), defaultdict(int)
        for r in rs:
            for o, p in r["probs"].items():
                acc[o].append(p)
            wins[r["choice"]] += 1
        probs = {o: r4(mean(v)) for o, v in sorted(acc.items(), key=lambda kv: -mean(kv[1]))}
        winner = max(probs, key=probs.get)
        t3[persona] = dict(answer_as=rs[0].get("answer_as"), probs=probs, winner=winner, n_win=wins[winner], n=len(rs))
    if t3:
        out["T3"] = t3
    return out


def calibrate(anchor_aggs, persona):
    """Ruler check for one column. Never drops data; only marks the column."""
    checks = []
    for it, ag in anchor_aggs:
        fails, flags, truth = [], [], it.get("truth")
        hi = truth == 1
        if persona == "none":
            t1, t0, t2 = ag.get("T1"), ag.get("T0"), ag.get("T2")
            if not (t1 and t2):
                fails.append("missing test")
            else:                                    # T1 is the ruler; T2 decline must stay shut on settled facts
                if (t1["p_true"] < 0.95) if hi else (t1["p_true"] > 0.05): fails.append("T1 p_true=%.2f" % t1["p_true"])
                if t2["p_decline"] > 0.02: fails.append("T2 p_decline=%.2f" % t2["p_decline"])
            if t0 and abs(t0["gap"]) > GAP_FLAG:      # T0 gap is an abstention diagnostic, not the ruler: flag, never fail
                flags.append("T0 gap=%.2f" % t0["gap"])
        else:
            t3 = (ag.get("T3") or {}).get(persona)
            if not t3:
                fails.append("missing test")
            elif (t3["p_true"] < 0.95) if hi else (t3["p_true"] > 0.05):
                fails.append("T3 p_true=%.2f" % t3["p_true"])
        checks.append(dict(id=it["id"], truth=truth, ok=not fails, fails=fails, flags=flags))
    n_ok = sum(c["ok"] for c in checks)
    passed = len(checks) == 8 and n_ok == 8
    # Validity is enforced on the no-persona column of each backend x lang. Under a persona the anchors are a REFERENCE row:
    # "a flat-earther" is supposed to flip "The Earth is flat" — that is the persona working, not the ruler breaking.
    return dict(passed=passed, enforced=persona == "none", status="ok" if passed else ("no anchors" if len(checks) < 8 else ("ruler failed" if persona == "none" else "anchors moved")),
                hits="%d/8" % n_ok, anchors=checks)


def summarize(out_dir, items):
    meta = {i["id"]: i for i in items}
    rows = []
    for f in sorted(glob.glob(os.path.join(out_dir, "*.jsonl"))):
        if any(x in os.path.basename(f) for x in (".traits.", ".lang-", ".self-rank.")):      # summarised separately
            continue
        backend = os.path.basename(f).split(".")[0]
        for l in open(f, encoding="utf-8"):
            r = json.loads(l)
            r.setdefault("backend", backend); r.setdefault("lang", "en"); r.setdefault("persona", "none"); r.setdefault("repeat", 0)
            rows.append(r)
    # Only rows whose wording is still in the item bank count: a reworded sentence keeps its id, and its old rows stay in the jsonl but not in the summary.
    current = lambda r: r.get("phrasing") in (meta[r["id"]].get("phrasings_" + r["lang"]) or meta[r["id"]]["phrasings_en"])
    stale = sum(1 for r in rows if r["ok"] and r["id"] in meta and not current(r))
    good = [r for r in rows if r["ok"] and r["id"] in meta and current(r)]
    unknown = sorted({r["id"] for r in rows if r["id"] not in meta})
    groups = defaultdict(list)
    for r in good:
        groups[(r["id"], r["lang"], r["backend"])].append(r)
    decl = {t["decline"] for t in L10N.values()}
    entries = []
    for (iid, lang, backend), rs in sorted(groups.items()):
        it = meta[iid]
        e = dict(id=iid, template_id=it["template_id"], entity=it["entity"], category=it["category"], type=it["type"], pair_strict=it.get("pair_strict", True),
                 lang=lang, backend=backend, jev_model=sorted({r.get("jev_model") or "?" for r in rs}), n_calls=len(rs))
        e.update(agg_stmt(rs) if it["type"] == "stmt" else {"choice": agg_choice(rs, decl)})
        entries.append(e)
    columns = []
    col_of = lambda r: "perspectives" if r["persona"].startswith("pv:") else r["persona"]
    for backend, lang, persona in sorted({(r["backend"], r["lang"], col_of(r)) for r in rows}):
        crows = [r for r in rows if (r["backend"], r["lang"], col_of(r)) == (backend, lang, persona)]
        ok = [r for r in crows if r["ok"]]
        toks = sum(r.get("tokens") or 0 for r in ok)
        anchors = [(meta[e["id"]], e) for e in entries if e["category"] == "anchor" and e["lang"] == lang and e["backend"] == backend]
        cal = (calibrate(anchors, persona) if persona != "perspectives" else
               dict(passed=None, enforced=False, status="n/a: item-specific prefixes; read against the no-persona column of the same backend and language", hits=None, anchors=[]))
        models = sorted({r.get("jev_model") for r in ok if r.get("jev_model")})
        col = dict(column="%s|%s|%s" % (backend, lang, persona), backend=backend, lang=lang, persona=persona, jev_model=models,
                   valid=bool(ok) and all(r.get("jev_model") for r in ok), has_raw=bool(ok) and all("raw" in r for r in ok),
                   n_calls=len(ok), n_err=len(crows) - len(ok), tokens=toks, cost_usd=round(toks * JEV_USD_PER_MTOK / 1e6, 4) if backend == "jev" else None, calibration=cal)
        columns.append(col)
        if cal["passed"] is False:
            print("!! %s for column %s (%s): %s" % ("RULER FAILED" if cal["enforced"] else "anchors moved under persona (reference only)", col["column"], cal["hits"], "; ".join("%s: %s" % (c["id"], ",".join(c["fails"])) for c in cal["anchors"] if not c["ok"]) or cal["status"]), file=sys.stderr)
    # batteries: align every sentence to the judgement (sign -1 => 1 - p), then mean and how many sentences lean its way
    bats = []
    def battery_entry(rowsb, **kw):
        its = [dict(statement=meta[e["id"]]["text_en"], sign=meta[e["id"]]["sign"], p_true=e["T1"]["p_true"], order_effect=e["T1"]["order_effect"],
                    aligned=r4(e["T1"]["p_true"] if meta[e["id"]]["sign"] > 0 else 1 - e["T1"]["p_true"])) for e in rowsb]
        al = [x["aligned"] for x in its]
        return dict(lang=rowsb[0]["lang"], backend=rowsb[0]["backend"], mean_aligned=r4(mean(al)), n_support=sum(x > 0.5 for x in al), n=len(al), range=r4(max(al) - min(al)), headline_p=its[0]["p_true"], items=its, **kw)
    # a sentence's own battery: one entry per name of the sentence
    own = defaultdict(list)
    for e in entries:
        if e["category"] == "battery" and meta[e["id"]].get("of_item") and "T1" in e:
            own[meta[e["id"]]["of_item"]].append(e)
    tmeta = {t["template_id"]: t for t in json.load(open(os.path.join(ROOT, "templates", "templates.json"), encoding="utf-8"))}
    for iid, rowsb in sorted(own.items()):
        rowsb.sort(key=lambda e: meta[e["id"]]["battery_item"])
        tid = meta[iid]["template_id"]
        bats.append(battery_entry(rowsb, battery_id=tid, judgement=tmeta[tid]["battery"]["judgement"], headline_template=tid, entity=meta[rowsb[0]["id"]]["entity_name"], item_id=iid))
    have = {(b["headline_template"], b["entity"].lower()) for b in bats}
    for b in load_batteries():      # a judgement with its own list of names; a sentence's own battery for the same name takes its place
        for ename in b["entities"]:
            rowsb = sorted([e for e in entries if e["template_id"] == "battery." + b["battery_id"] and meta[e["id"]].get("entity_name") == ename and not meta[e["id"]].get("of_item") and "T1" in e], key=lambda e: meta[e["id"]]["battery_item"])
            if rowsb and (b.get("headline_template"), ename.lower()) not in have:
                bats.append(battery_entry(rowsb, battery_id=b["battery_id"], judgement=b["judgement"], headline_template=b.get("headline_template"), entity=ename))
    entries = [e for e in entries if e["category"] != "battery"]      # battery sentences live in `batteries`, not in the map
    # The second check: every battery sentence across the names it was asked about. A sentence that reads the same for every name says something about
    # the construction, not about the name, and it spoils an average. Two ways out, both written in the bank next to the sentence: `uniform_ok` (the
    # sameness is itself the finding; the note says what independent evidence there is) and `gap_with` (the sentence is one half of a recorded pair,
    # e.g. a label and the fact behind it: what is published there is the two readings side by side and their difference, never an average).
    across = defaultdict(list)
    for b in bats:
        for k, x in enumerate(b["items"]):
            across[(b["battery_id"], k)].append(x["p_true"])
    note = {(i["battery_id"], i["battery_item"]): i.get("uniform_ok") for i in meta.values() if i.get("category") == "battery"}
    pair = {(i["battery_id"], i["battery_item"]): i.get("gap_with") for i in meta.values() if i.get("category") == "battery"}
    sentences = defaultdict(list)
    for (bid, k), ps in sorted(across.items()):
        sd = st.pstdev(ps) if len(ps) > 1 else None
        # The spread depends on who was asked about. Few names, or names picked because they share the trait (five people who all stand accused),
        # squeeze a good sentence into a low SD: below BATTERY_MIN_NAMES the number is reported and nothing is concluded from it.
        # Too few names only matters when the spread is low: a sentence that already separates the names it was asked about needs no
        # larger set to prove it. A low spread with few names is unresolved, not a failure, unless the bank settles it (uniform_ok / gap_with).
        low = sd is not None and sd < BATTERY_MIN_SD and not note.get((bid, k)) and pair.get((bid, k)) is None
        sentences[bid].append(dict(k=k, n_names=len(ps), mean=r4(mean(ps)), sd=r4(sd), uniform_ok=note.get((bid, k)), gap_with=pair.get((bid, k)),
                                   flagged=bool(low and len(ps) >= BATTERY_MIN_NAMES), unresolved=bool(low and len(ps) < BATTERY_MIN_NAMES)))
    # The floor of a battery: its average over the names whose forward sentences all read near zero. Reversed sentences can carry a presupposition
    # ("X conducted war within the laws of war" is false of someone who never waged war), which lifts every average; the floor says by how much.
    floors = {}
    for bid in {b["battery_id"] for b in bats}:
        low = [b for b in bats if b["battery_id"] == bid and any(x["sign"] < 0 for x in b["items"]) and mean(x["p_true"] for x in b["items"] if x["sign"] > 0) < 0.10]
        if len(low) >= 3:
            floors[bid] = dict(n_names=len(low), names=[b["entity"] for b in low], mean_of_forward=r4(mean(mean(x["p_true"] for x in b["items"] if x["sign"] > 0) for b in low)), mean_of_battery=r4(mean(b["mean_aligned"] for b in low)))
    for b in bats:
        L = sentences[b["battery_id"]]
        b["clean"] = False if any(x["flagged"] for x in L) else (None if any(x["unresolved"] for x in L) else True)      # None = a flat sentence on too few names to judge
        b["forward_mean"] = r4(mean(x["p_true"] for x in b["items"] if x["sign"] > 0)) if any(x["sign"] > 0 for x in b["items"]) else None
    # The gate. A sentence marked `needs_battery` does not publish its single reading as a number on its own: a name is `publishable` there
    # only with a battery reading next to it. `flipped` = the single sentence and the battery land on different sides of 0.5.
    bat_of = {(b["headline_template"], b["entity"].lower()): b for b in bats}
    report = {}
    for e in entries:
        t = tmeta.get(e["template_id"])
        if e["type"] != "stmt" or not t or not t.get("needs_battery") or "T1" not in e:
            continue
        b = bat_of.get((e["template_id"], (meta[e["id"]].get("slots") or {}).get("a", "").lower()))
        e["publishable"] = b is not None and b["clean"] is not False
        r = report.setdefault(e["template_id"], dict(n_names=0, n_with_battery=0, n_flipped=0, flipped=[]))
        r["n_names"] += 1
        if b:
            r["n_with_battery"] += 1
            if (e["T1"]["p_true"] > 0.5) != (b["mean_aligned"] > 0.5):
                r["n_flipped"] += 1; r["flipped"].append(e["id"])
    fl = [e["stance"]["sensitivity"] for e in entries if e["category"] == "anchor" and e.get("stance")]
    noise = dict(n_anchors=len(fl), mean=r4(mean(fl)), max=r4(max(fl))) if fl else None      # the same stances on settled facts = the floor to read sensitivity against
    summary = dict(languages=summarize_languages(out_dir, entries, meta), traits=summarize_traits(out_dir), self_rank=summarize_self_rank(out_dir, entries, meta), n_items=len(entries), stance_noise_floor=noise, stance_bands=dict(below=SENS_LOW, above=SENS_HIGH), date=os.path.basename(os.path.normpath(out_dir)), generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), range_wide_threshold=RANGE_WIDE,
                   columns=columns, unknown_item_ids=unknown, stale_rows_ignored=stale, battery_gate=report, battery_sentences=dict(min_sd=BATTERY_MIN_SD, min_names=BATTERY_MIN_NAMES, floors=floors, batteries=sentences), batteries=bats, items=entries)
    path = os.path.join(out_dir, "summary.json")
    if os.path.exists(path):      # rebuilding from the same rows changes nothing but the timestamp: keep the file as it is
        prev = json.load(open(path, encoding="utf-8"))
        if {**prev, "generated_at": None} == {**json.loads(json.dumps(summary)), "generated_at": None}:
            summary["generated_at"] = prev["generated_at"]
    json.dump(summary, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("→ %s (%d item rows, %d columns: %s)" % (os.path.relpath(path, ROOT), len(entries), len(columns),
                                                   ", ".join("%s %s" % (c["column"], "ok" if c["calibration"]["passed"] else c["calibration"]["status"].split(":")[0].upper()) for c in columns)))
    return summary


def cmd_stats(items):
    from collections import Counter
    items = [i for i in items if i["category"] != "battery"]
    body = [i for i in items if i["category"] != "anchor"]
    ent = sum(i["category"] == "entertainment" for i in body)
    print("items %d = %d stmt + %d choice + %d anchors · %d stmt templates" % (len(items), sum(i["type"] == "stmt" for i in body), sum(i["type"] == "choice" for i in body),
          len(items) - len(body), len({i["template_id"] for i in body if i["type"] == "stmt"})))
    print("by category:", ", ".join("%s %d" % kv for kv in Counter(i["category"] for i in items).most_common()))
    print("zh coverage: %d/%d" % (sum("phrasings_zh" in i for i in items), len(items)))
    print("entertainment share: %.1f%%%s" % (100 * ent / len(body), "" if ent / len(body) >= 0.20 else "  !! below the 20% dilution gate"))
    errs = lint(items)
    for e in errs:
        print("LINT", e, file=sys.stderr)
    print("lint: %s" % ("ok" if not errs else "%d error(s)" % len(errs)))
    return 1 if errs else 0


def main():
    ap = argparse.ArgumentParser(description="Diode test runner")
    ap.add_argument("--backend", default="jev", choices=["jev", "replica", "chat-logprobs"])
    ap.add_argument("--langs", default="en")
    ap.add_argument("--personas", default="none", help="comma list of persona ids from bank/personas/; `none` is the baseline (T0/T1/T2)")
    ap.add_argument("--perspectives", action="store_true", help="also run each item's party perspectives (T3 with an item-specific answer_as)")
    ap.add_argument("--translated", default="", metavar="LANGS", help="`all` or a comma list from bank/translations.json: the diode test with the statement translated, question and options in English")
    ap.add_argument("--traits", action="store_true", help="also run bank/traits.json: Jev's impression of every person in the item bank (one request per person and question order)")
    ap.add_argument("--batteries", action="store_true", help="also run bank/batteries.json (diode only)")
    ap.add_argument("--self-rank", action="store_true", help="only run bank/self_rank.json: the model scores every statement itself (how contested, how heated, how taboo, how answerable), "
                    "once blind and once shown the numbers measured in results/<date>/summary.json; then rebuilds that summary")
    ap.add_argument("--only", default="", help="comma list of template_id (anchors are always added)")
    ap.add_argument("--categories", default="", help="comma list of categories (anchors cannot be excluded)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--conc", type=int, default=10)
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d", time.gmtime()))
    ap.add_argument("--env-file", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump-requests", metavar="FILE", help="write every planned request body as jsonl and exit; no network")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--summarize", metavar="DIR", help="rebuild DIR/summary.json from its jsonl files; no network")
    a = ap.parse_args()
    items = load_items()
    items, personas = items + battery_items(load_batteries(), items), load_personas()
    if a.stats:
        sys.exit(cmd_stats(items))
    if a.summarize:
        summarize(os.path.abspath(a.summarize), items)
        return
    if a.repeats > 2:
        print("!! --repeats %d: repeats are a waste — repeat SD is 0.005, two orders of magnitude below the phrasing range. Clamped to 2; spend the budget on phrasings." % a.repeats, file=sys.stderr)
        a.repeats = 2
    for l in a.langs.split(","):
        if l not in L10N:
            sys.exit("unknown lang %r" % l)
    a.personas = ",".join(dict.fromkeys(",".join(PROBES) if p == "probes" else p for p in a.personas.split(",")))
    for p in a.personas.split(","):
        if p not in personas:
            sys.exit("unknown persona %r (have: %s)" % (p, ", ".join(sorted(personas))))
    errs = lint(items)
    if errs:
        sys.exit("item lint failed:\n  " + "\n  ".join(errs))
    if a.self_rank:
        out_dir = os.path.join(ROOT, "results", a.date)
        run_self_rank(a, items, SystemOne(a.backend, a), out_dir)
        if not a.dry_run:
            summarize(out_dir, items)
        return
    if a.translated and a.backend != "chat-logprobs":
        run_translated(a, items, SystemOne(a.backend, a), os.path.join(ROOT, "results", a.date))
    if a.traits and a.backend != "chat-logprobs":
        run_traits(a, items, SystemOne(a.backend, a), os.path.join(ROOT, "results", a.date))
    cmd_run(a, items, personas)


if __name__ == "__main__":
    main()
