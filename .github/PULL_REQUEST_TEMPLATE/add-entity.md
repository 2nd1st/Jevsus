## Add a name / a sentence / a category

Edit the tables in `bank/` (never the generated `items/`), run `python3 tools/build.py`, and fill this in.

- **What**: name / sentence / category
- **Where**: `bank/names/<kind>.json` · `bank/sentences/<kind>.json` · `bank/kinds.json`
- **For a sentence**: does it read naturally for *every* name of the category? It will be asked about all of them.
- **Why is it sharp?** (one line)

### Review policy ([CONTRIBUTING.md](../../CONTRIBUTING.md)) — PRs that miss any of these are closed

- [ ] Public figures and public entities only. No private individuals.
- [ ] Sentences about people: public office or opinion-type evaluation only. Nothing about private life, no allegation of a specific act.
- [ ] Factual form. The lint rejects `should` / `ought to`.
- [ ] More than two parties → `bank/picks.json`.
- [ ] No result numbers added by hand. Results only come from `run.py`.
