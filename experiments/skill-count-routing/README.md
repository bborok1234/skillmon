# Does a bloated skill listing degrade skill routing?

**Short answer: no, not measurably — at least not at 2.5x the listing budget on Haiku 4.5.**

Claude Code budgets the skill listing it sends the model at ~1% of the context window and caps each skill description at 1536 characters. Its own internal guidance states that when the listing exceeds that budget, "entries get truncated and skill routing degrades." That claim is the reason a skill-cleanup tool sounds urgent, so we measured it.

## Result

| skills installed | listing tokens | % of budget | routing accuracy | wrong skill | no skill |
| --- | --- | --- | --- | --- | --- |
| 40 | 1,573 | 78% | **90%** (35/39) | 0 | 4 |
| 125 | 5,139 | 256% | **90%** (36/40) | 0 | 4 |

Tripling the number of installed skills, and pushing the listing to 2.5x its budget, changed nothing. Notably `wrong` is zero in both conditions: the model never picked the wrong skill. Every failure was the model declining to use a skill at all, and it was the *same* five skills in both conditions.

## Method

- **Fixed targets.** 40 skills each have one natural-language prompt whose correct answer is that skill (`"내일 부산 비 올까?"` → `korea-weather`). All 40 targets are present in every condition; only the number of distractor skills changes.
- **Total isolation.** Global skills are disabled via a `--settings` file that sets `skillOverrides` to `off` for every one of them, and the condition's skills are staged into a scratch project's `.claude/skills/`. Verified: a prompt that would trigger a global-only skill triggers nothing.
- **Ground truth from transcripts.** Each case runs `claude -p` and the skills actually invoked are read from the session transcript's `Skill` tool_use entries — not from the model's own account of what it did.
- **Graded** `correct` / `wrong` (used a different skill) / `miss` (used none) / `timeout`.

```bash
python3 run_exp.py haiku            # 40 cases x 2 conditions
python3 run_exp.py haiku 40,80,125  # custom conditions
python3 analyze.py
```

`results.jsonl` holds the 80 runs behind the table. Total API cost: $5.30.

## Limitations — read these before citing the result

- **Haiku 4.5 only.** A larger model would plausibly do better, not worse, but this is untested. The interesting untested direction is a *smaller* or older model.
- **Tool calls are blocked** (`--disallowed-tools Bash WebFetch WebSearch Write Edit SendMessage`) so skill bodies cannot cause side effects. This costs ~10% of baseline accuracy: five external-lookup skills (`korean-law-search`, `korean-spell-check`, `library-book-search`, `naver-news-search`, `kr-whois-lookup`) are never invoked because the model can tell the lookup cannot execute, and answers directly instead. Those five fail identically in both conditions, so the comparison holds, but the absolute 90% is a floor set by the harness, not by routing.
- **Korean-language skills**, mostly domain lookups with sharply distinct triggers. A pool of 125 skills with *overlapping* purposes is the case most likely to break, and it is not tested here.
- **125 skills is not the ceiling.** 2.5x budget showed nothing; 10x might.
- **One prompt per skill.** Phrasing sensitivity is not measured.

## What this means for skillmon

We removed the "your routing is degrading" claim from the tool. It was inferred from the truncation mechanism, not measured, and our measurement does not support it. What survives is the part that arithmetic already proves: unused skills sit in context on every single turn, and you pay for them every time. That is a smaller claim, and it is true.

A side observation worth recording: with its tools blocked, one experiment session messaged *another* Claude session on the same machine asking it to run the blocked `curl` on its behalf. Sandboxing an agent's tools does not sandbox its ability to ask a peer.
