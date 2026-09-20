# Community runs

`<date>.jsonl` holds runs made by visitors of position.secondfirst.ai: the name they typed, the categories the model gave it, and the model's output on every sentence of those categories (true/false test only, 3 wordings × 2 option orders, with that run's 8 anchors). `reasks.jsonl` holds "ask again now" re-measurements of published items.

- Visitors choose the names. They are told before submitting that a run may be published here, and can untick it, in which case nothing is stored.
- **The gate is the model itself**: a name is only asked about, and only stored, when the model classifies it as something (any category in `bank/kinds.json` but `none`).
- Rows carry no IP, session or account.
- New names are reviewed at every sync. `blocklist.txt` removes a name from the export and, retroactively, from every file already published.

A row means "someone asked the model this, and this is what it output". It is never a claim about the person or organisation named. **If you are named here and want an entry removed, open an issue.**
