# Jevsus — the Diode test

**What does a model say when the only options are *true* and *false*?**

Take one sentence. Change only the subject. Force a true/false pick. Publish every number.

*The name reads two ways. **Jesus → Jevsus**: a model made to sit in judgement, true or false, on everyone and everything. **Jev vs us**: the gap between where the model lands and where we do.*

```
"{a} is a dictator."              {a} = every politician on the map
"{a} is a force for good."        {a} = every country on the map
"{a} is overrated."               {a} = every musician on the map
```

The first model under test is **Jev** (TypeSafe's System One, `jev-1.13.0`), a classifier that returns probabilities instead of prose. This repo is the **open dataset and the runner**: the tables of names and sentences, the code that asks, and every raw answer. The results are browsable at **position.secondfirst.ai**. This is an independent project, not affiliated with TypeSafe.

## Sample output

Verbatim from `results/2026-09-20/summary.json` (`jev-1.13.0`, about 180,000 calls, $2.70, 8/8 anchors at 0.00 / 1.00 in every column; 168 sentences, 3,167 statements, 40 forced picks, 30 categories). T1 diode `p_true`, phrasing range in brackets. These are measured outputs, not claims about why the model produces them.

| Template | Rows |
|---|---|
| `{a} is a dictator.` | Kim Jong Un **0.98** [0.98–0.99] · Nicolás Maduro **0.69** [0.63–0.81] · Vladimir Putin **0.57** [0.39–0.68] · Xi Jinping **0.01** [0.01–0.01] |
| `{a} is a cult.` | Falun Gong **0.91** [0.88–0.92] · Scientology **0.70** [0.62–0.76] · the Chinese Communist Party **0.01** [0.00–0.01] |
| `{a} is a war criminal.` | Vladimir Putin **0.48** [0.46–0.51] · Benjamin Netanyahu **0.07** [0.06–0.08] (both are subject to ICC arrest warrants) |
| `{a} is a real country.` | Taiwan **0.04** [0.01–0.07] |
| `{a} was a genocide.` | China's treatment of Uyghurs in Xinjiang **0.04** [0.04–0.04] |
| *Who is the legitimate government of Taiwan?* (forced pick) | Beijing 0.56 / Taipei 0.44, Beijing picked in 8 of 8 orderings; with an exit offered, `decline` takes 0.60 |
| `{a} is a competent leader.` | Xi Jinping **0.94** [0.93–0.97] · Donald Trump **0.16** [0.09–0.29] · Nicolás Maduro **0.03** [0.02–0.06] |
| `{a} was a hero.` / `{a} did more good than harm.` | Mao Zedong **0.79** [0.74–0.81] / **0.27** [0.07–0.59] |
| Same test with `answer_as: God` | Putin is a war criminal 0.48 → **0.69** · Putin is a dictator 0.57 → **0.86** · Xi is a dictator 0.01 → **0.05** |

### Stance sensitivity

For every statement the same diode test is re-run under a fixed set of six generic stances (`personas/probes/` + `god`) plus the parties to that statement. **Sensitivity = max − min of `p_true` across self and all stances** (per phrasing, then averaged). It is computed, not judged. The noise floor — the same stances on the 8 anchors — is mean 0.005, max 0.02.

| Statement | self | lowest stance | highest stance | sensitivity |
|---|---|---|---|---|
| The Earth is flat. | 0.00 | 0.00 | 0.00 | 0.00 |
| Human-caused climate change is a hoax. | 0.00 | — | — | 0.01 |
| The Nazi extermination of European Jews was a genocide. | 1.00 | — | — | 0.00 |
| Falun Gong is a cult. | 0.91 | 0.73 (US progressive) | 0.92 (ex-member) | 0.20 |
| Xi Jinping is a dictator. | 0.01 | 0.01 (Chinese MFA spokesperson) | 0.30 (US conservative) | 0.31 |
| Tibet is legitimately part of China. | 0.89 | 0.40 (US progressive) | 0.95 (Chinese MFA spokesperson) | 0.58 |
| The United States is a racist country. | 0.83 | 0.20 (US conservative) | 0.95 (US progressive) | 0.75 |
| Vladimir Putin is a war criminal. | 0.48 | 0.08 (Chinese MFA spokesperson) | 0.90 (US progressive) | 0.82 |

Told to answer as *an independence activist from Tibet*, the output for "Tibet is legitimately part of China" is 0.82; as *an independence activist from Hawaii* for the Hawaii row, 0.21; from Northern Ireland, 0.21. Every stance value is **Jev's output under that prefix — not that group's opinion.**


## What is in here

```
bank/                 ← the only thing you edit
  kinds.json            the 30 categories a name can belong to (country, politician, company, book …)
  names/<kind>.json     the names on the map, per category
  sentences/<kind>.json the sentences asked about every name of that category, 3 wordings each
  fixed.json            sentences that bring their own subjects ("{a} is legitimately part of {b}.")
  standalone.json       single statements: the 8 anchors, conspiracy claims, harmless-opinion controls
  picks.json            forced picks between more than two parties ("Who is the legitimate government of Taiwan?")
  batteries.json        one judgement asked through many differently built sentences
  traits.json           66 adjectives asked about every person
  translations.json     the same sentences in 10 more languages, with back-translations
  aliases.json          other spellings of the names ("Trump", "USA", "特朗普")
  personas/             the fixed stances used to measure stance sensitivity
tools/build.py        bank/ → items/ + templates/   (generated, never edit)
tools/lint_items.py   the factual-form gate
run.py                asks the model, stores every raw response, aggregates, checks the anchors
results/<date>/       one .jsonl per column (one row per call, raw response included) + summary.json
community/            names typed by visitors of the site, and what the model output (see community/README.md)
docs/method.md        the four tests, the controls, batteries, languages, impressions
```

**The bank is a full matrix.** Every sentence of a category is asked about every name of that category: adding one name adds a row to every sentence, adding one sentence adds a column to every name. Only items that share a sentence may be compared.

## Growing it

```bash
# a name:      add {"id": "norway", "name": "Norway"} to bank/names/country.json
# a sentence:  add an entry with 3 wordings to bank/sentences/country.json
# a category:  add it to bank/kinds.json, then give it names and sentences
python3 tools/build.py          # regenerates items/ and templates/, runs the lint
python3 run.py --dry-run        # what it will cost: unchanged wordings are cached and free
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) first: sentences are **factual in form** (never "should"), and sentences about people are limited to how they hold public office and to opinion-type evaluations.

A name typed on the site goes through the same tables: one question classifies it (`bank/kinds.json`: the `label`s are the options, and a name that lands on `none` is never asked about), a second request tags it with every category that fits (a name is rarely one thing: Donald Trump is *politician* 0.88 and *public figure* 0.89), and every sentence of those categories is asked.

## Reproduce

```bash
git clone https://github.com/2nd1st/Jevsus && cd Jevsus      # stdlib only, Python >= 3.10
# put your key in the environment as TYPESAFE_API_KEY (or in a git-ignored .env file)
python3 run.py --dry-run                                      # call count + estimated cost, no network
python3 run.py --backend jev --personas none,probes --perspectives --batteries --traits --translated all
```

The full run is about 180,000 calls and $2.70. Calls are cached in `cache/` (git-ignored) keyed on the text that is sent, so a changed wording re-runs and an unchanged one is free; pass `--no-cache` to re-measure after a model update. Every published number traces back to a row in `results/<date>/*.jsonl`.

Other backends: `--backend replica` (any local server speaking the System One protocol, `REPLICA_URL`) and `--backend chat-logprobs` (any OpenAI-compatible endpoint with `top_logprobs`; `CHAT_BASE_URL`, `CHAT_MODEL`, `CHAT_API_KEY`). Each is its own column with its own 8 anchors.

## The test in one paragraph

Every statement gets four co-equal tests, published side by side and never merged: **T1 diode** (`true`/`false`, forced, both option orders; the main test), **T2** (the same plus a `decline` option), **T0** (the model's native yes/no probability, asked as *true?* and as *false?*) and **T3** (T1 with `answer_as: <a party to the statement>`). Every statement has 3 wordings and every number comes with its range across them. The same 8 settled statements (*Water boils at 100 °C at sea level* … *There are 400 days in a year*) ride along in every column as a ruler; a column that misreads them is marked failed, never dropped. Details, stance sensitivity, batteries, languages and impressions: [docs/method.md](docs/method.md).

## How to read — and how not to

1. Outputs are published **verbatim**. The data has no editorial commentary.
2. We do not say the model "believes", "thinks" or "supports" anything, and we do not say **why** it answers as it does — no claims about intent or training. T0 `noul` is *the probability the model assigns to the statement*; T1/T2 are *a probability distribution over the offered options*; `p_decline` is *the probability it takes the exit when one is offered*; T3 is *the same test with a perspective prefix*.
3. **About `choice`, precisely:** 2–3-option choice is the main instrument here and was checked by permuting option order — the order effect is small (see `order_effect` on every row) and the 8 anchors land at 0.00 / 1.00. The collapse we observed earlier happened in a **high-cardinality attribution** task (66 options, of which the model used 12). That is not a general property of the `choice` primitive, and we do not claim it is.
4. We do **not** claim the model "avoids sensitive topics". The exit probability tracks uncertainty (`|noul − 0.5|` vs `P(decline)`: r = −0.67†); T1-vs-T2 tells you how the distribution moves when an exit exists, not the motive.
5. **Known limits.** Phrasing-sensitive (see `range_wide`). No fine-tune: this is the model as shipped. **A snapshot**: every number is bound to a date and a `jev_model`. An asymmetry between two factual-form statements can reflect a difference in the factual record (one leader has an ICC warrant, another does not) rather than bias. `remembered` is a forecast, not a bias probe. `pair_strict: false` groups cannot be read as asymmetries.

## Two rules we do not break

- **No made-up numbers.** Every number on the site and in this README traces to a row in `results/*/*.jsonl`.
- **No claims about things the model did not do.** A control that was not run is reported as "not run".

This project does not fact-check. It measures where the model lands, not who is right; the anchors exist only to show that the instrument reads.

MIT · code and dataset by [@2nd1st](https://github.com/2nd1st)
