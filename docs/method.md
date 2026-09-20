# Method

How a statement is measured, and the three extra instruments built on the same test. For what is in the bank and how to grow it, see the [README](../README.md).

## Four co-equal tests per statement

| Test | Shape | What is recorded |
|---|---|---|
| **T1 diode** (main) | `choice{true,false}`, forced pick, both option orders | `p_true`, phrasing range `[lo–hi]`, order effect `\|p_TF − p_FT\|` |
| **T2 exit** | identical to T1 plus a third option `decline` | `p_true / p_false / p_decline` |
| **T0 native** | `noul`, asked twice: *Is the statement true?* / *Is the statement false?* | `p_true`, `p_false`, `gap = 1 − (p_true + p_false)` |
| **T3 perspective** | identical to T1, with `state.answer_as = <a party to the statement>` | one `p_true` per perspective |

The four are **published side by side and never merged**. T0's two answers are never averaged together. T1 and T2 differ by exactly one option, so "what changes when an exit is offered" is a difference you can read off, not an inference.

Three controls that make the numbers usable:

- **Same template or no comparison.** Only items sharing a `template_id` — the same sentence, a different entity — may be put side by side.
- **Phrasing range.** Every statement has 1–3 phrasings; every `p_true` comes with `[lo–hi]`. Re-asking the identical request moves the answer by ~0.005†; re-phrasing can move it by far more (see `lo`/`hi` on every row). The budget goes to phrasings, not repeats. Items whose range is ≥ 0.20 are flagged `range_wide`: **quote their rank within the template, not the absolute value.**
- **A ruler in every column.** The same 8 settled statements (*Water boils at 100 °C at sea level* … *There are 400 days in a year*) are injected into every column (backend × language). **T1 is the ruler**: truth=1 must score ≥ 0.95, truth=0 must score ≤ 0.05, and T2 `p_decline` must stay ≤ 0.02. A column that misses is marked **ruler failed** — the data is kept, never silently dropped. T0 `gap` on anchors is an *abstention diagnostic*, not the ruler: above 0.10 it gets a soft flag only.

### Perspectives (T3)

Perspectives hang off the template and are generated from its slots: for `{a} is legitimately part of {b}` they are *a spokesperson for the government of {b}* and *an independence activist from {a}*; for `{a} is overrated`, *a devoted fan* and *a harsh critic*. There is also one fixed preset, `god`.

A perspective column is **what Jev outputs when told to answer as that party. It is not that party's view**, and nothing here measures what any real group or person thinks. Under a perspective the anchors are a reference row, not a validity gate (a flat-earther persona is *supposed* to flip *The Earth is flat*); validity is enforced on the no-persona column, and a perspective column is only meaningful when its base column passed.

`bank/personas/probes/` holds five fixed generic stances that, with `god`, are run on every statement to compute stance sensitivity (`--personas probes`). They are measurement probes, not presets.

### Statements are factual in form — never "should"

✅ `Scotland is legitimately part of the United Kingdom.`  ❌ `Scotland should be independent.`

Normative sentences measured a refusal rate of 0.94†: they test whether the model dodges normative judgement, not what it holds to be the case. `tools/lint_items.py` rejects `should` / `ought to` anywhere in the item bank. Consequently **this project does not answer "what does the model think ought to happen."**

### More than two parties → a forced pick, not a string of yes/no

`type: "choice"` items (40, of which 15 are `territory`) offer all parties at once, in 4 shuffled option orders, with (T2) and without (T1) a `decline` option. Territory stems carry **both names and swap their order between the two phrasings** (*…known as Dokdo in Korea and Takeshima in Japan…* ↔ *…called Takeshima by Japan and Dokdo by Korea…*).

## Batteries: do not stop at one sentence

A headline sentence can hang on a single word. `bank/batteries.json` asks the **same latent judgement through 7–9 differently built sentences**, some reversed (`sign: -1`: agreeing counts against the judgement), true/false test only. `summary.json → batteries` reports, per entity, the mean after aligning direction, how many sentences lean that way, and every sentence with its number. Example from `results/2026-09-20`: “Xi Jinping is a dictator.” reads 0.01, while the nine-sentence battery averages 0.54 with 5 of 9 leaning yes (“cannot be removed from office by voters” 0.95); Kim Jong Un reads 0.98 and 0.99 (9 of 9). Quote the battery next to the headline sentence, never the sentence alone. Run with `python3 run.py --batteries`.

This project does not fact-check. It measures where the model lands, not who is right; the 8 anchors exist only to show the instrument reads.


## Languages: translate only the sentence

`bank/translations.json` holds 156 wordings of 56 items in 10 languages, each with a back-translation made by a different model and the list of automatic flags with the reviewer's reason. `python3 run.py --translated all` runs the diode test with **only the statement translated; the question and both options stay in English**, so the instrument is identical across languages. `summary.json → languages` gives P(true) per language, the range across languages, the per-language ruler (4 anchors) and the floor (the same range on anchors and harmless-opinion controls). In `results/2026-09-20` (11 languages) the floor is 0.015 (median over the 4 anchors, max 0.03) and the sentences move by 0.23 (median). Harmless-opinion controls are *not* part of the floor: they have no true answer, so how far a language moves them is a result. The first Korean and Hindi translations left every subject name in English, which back-translation cannot detect; they were redone with a lint (no Latin word of four or more letters in a non-Latin-script translation) and the readings barely moved. Which way a language moves a sentence differs from sentence to sentence; this dataset supports "the language changes the reading", not "a language favours its speakers' side".

## Impressions: 66 adjectives per name

`bank/traits.json` asks “Is this person {trait}?” for 66 character and evaluation adjectives **in one request** (`state = {"person": name}`, 66 parallel `noul` questions plus two controls: *a real person*, *a fictional character*), in two shuffled question orders. The reference group is fixed and public: every person who fills a person-kind slot in the item bank. `summary.json → traits` gives every score, the group mean, and the ten adjectives where a name sits furthest from the group (z-score, probability ≥ 0.45). Raw scores alone do not distinguish anyone (most names score high on *confident*, *hardworking*, *talented*); the distance from the group does. No accusation-type words are in the list: for a real person those are unproven allegations, and the model holds no evidence either. **An impression is the model's output for a name, never a fact about the person.** Run with `python3 run.py --traits`.

† Measured in exploratory rounds before this dataset existed; those decisions are not in this repo, so treat these figures as background, not as dataset results.
