# skillmon

**Find unused Claude Code and Codex skills, see what they cost you in context every turn, and remove them.**

Agent skills are cheap to install and invisible to forget. A year in, your skill listing is 160 entries, most of which you have never invoked, and every single one of them is re-sent to the model on every turn — you pay for all of them, on every request, forever.

How much does that actually cost you in routing quality? We measured it rather than guessed: **at 2.5x the listing budget, routing accuracy did not move** (90% vs 90%, zero wrong-skill picks). See [experiments/skill-count-routing](experiments/skill-count-routing/) for the harness, the 80 runs, and the limitations. So this is not a correctness scare — it is a bill you are paying for nothing.

`skillmon` is a single-file Python script (stdlib only, no install) that reads your local agent data and tells you exactly which skills are dead weight.

```
skill                              윈도    누적  토큰 agent   마지막     판정   plugin
korea-weather                         0       0    58 -       -          remove
seoul-bike                            0       0    41 -       -          remove
figma-swiftui                         0       0    52 -       -          ask    figma@claude-plugins-official
insane-search                        28      26   247 claude  2026-09-02 keep   insane-search@gptaku-plugins
imagegen-frontend-mobile             41       0    96 codex   2026-09-06 keep
ponytail-review                       0    5650   118 -       2026-09-07 keep   ponytail@ponytail

스킬 159개 · 최근 180일 윈도우
상시 컨텍스트 비용 8,660 토큰 / 목록 예산 2,000 (433%)  ← 초과분은 설명이 잘린다
미사용분이 먹는 몫 6,574 토큰 (매 턴, 전체의 75%)
판정: keep 28 · remove 107 · ask 24
```

## Install

```bash
curl -O https://raw.githubusercontent.com/bborok1234/skillmon/main/skillmon.py
python3 skillmon.py
```

Python 3.9+. No dependencies, no network calls, no telemetry. Everything it reads is already on your disk.

## Usage

```bash
python3 skillmon.py                    # full report: usage + resident context cost
python3 skillmon.py --days 90          # narrow the window (default 180)
python3 skillmon.py --unused           # only the non-keep verdicts
python3 skillmon.py --off              # disable via settings — reversible, recommended
python3 skillmon.py --prune            # remove from disk (backup move + plugin uninstall)
python3 skillmon.py --json             # machine-readable, for cron
python3 skillmon.py --keep a,b         # never touch these
```

`--off` and `--prune` both print exactly what they will do and ask for confirmation. `--off` backs up `settings.json` first.

## How it decides

| verdict | meaning |
| --- | --- |
| `keep` | used inside the window, or the built-in lifetime counter is non-zero |
| `remove` | zero in both. Safe to disable |
| `ask` | no usable signal — the parent plugin is used but this skill shows nothing, or the skill has no frontmatter. Your call, not the data's |

Signals it reads:

- **`~/.claude.json` → `skillUsage` / `pluginUsage`** — Claude Code's own lifetime counters (`usageCount`, `lastUsedAt`). This is the authoritative source. Hook-driven and always-on skills never emit a `Skill` tool call, so a transcript-only scan reports them as unused and you delete something you use 5,000 times.
- **`~/.claude/projects/**/*.jsonl`** — windowed usage: `Skill` tool calls, `<command-name>` slash invocations, `SKILL.md` reads, and `mcp__<server>__*` calls for MCP server counts.
- **`~/.codex/sessions/**/*.jsonl`** — Codex usage, recovered from `SKILL.md` paths inside exec calls.

Context cost is computed the way Claude Code actually computes it: skill name + description from the `SKILL.md` frontmatter, description capped at 1536 chars, ~4 chars per token, compared against the skill-listing budget of 1% of the context window. Over budget, descriptions get truncated — measurably without hurting routing, so treat the percentage as a bill, not an alarm.

## Why not just use `/plugin stats`

Claude Code ships its own skill usage report — originally `/skill-doctor`, now the Stats tab of `/plugin` and Check 1 of `/doctor`. It is good. `skillmon` covers everything it does, plus:

| | `/plugin stats` | `skillmon` |
| --- | --- | --- |
| Claude Code usage | ✅ built-in counters | ✅ same counters |
| Resident context cost | ✅ | ✅ |
| MCP server usage | ✅ | ✅ |
| **Codex usage** | ❌ | ✅ |
| Reversible disable | ✅ settings toggle | ✅ settings toggle |
| **Actually remove from disk** | ❌ | ✅ backup move + `plugin uninstall` |
| **Scriptable / JSON / cron** | ❌ interactive UI | ✅ |

The Codex gap matters more than it sounds. `~/.claude/skills/*` is very often a symlink into a shared `~/.agents/skills`, so both agents run the same files. A Claude-Code-only report calls those skills unused and you delete something Codex uses daily.

## Safety

- Nothing is deleted or written without an explicit `y`.
- `--off` writes a settings toggle and backs up the original file. Fully reversible.
- `--prune` moves skills to `~/.claude/backups/skillmon-<timestamp>/` rather than deleting them, and writes a `restore.sh` there that puts every one of them back. Symlinked skills (`~/.claude/skills/*` is often a relative symlink into a shared `~/.agents/skills`) are unlinked rather than moved, because moving a relative symlink breaks it — the restore script recreates the link instead.
- A plugin is only uninstalled when *every* skill it ships is unused. Otherwise it is skipped, with the reason printed.
- Bundled and policy-managed skills are never touched — only what lives in your own skill and plugin directories.

## 한국어

코딩 에이전트에 스킬을 100개씩 깔아두면, 쓰지도 않는 스킬의 설명이 **매 턴 컨텍스트에 다시 올라갑니다.** 요청마다, 계속.

그럼 라우팅 품질도 나빠지느냐 — 추측하지 않고 재봤습니다. 목록 예산의 2.5배(스킬 125개)에서도 **정확도가 그대로였습니다**(90% vs 90%, 엉뚱한 스킬 선택 0건). 하네스와 80회 실행 기록, 한계는 [experiments/skill-count-routing](experiments/skill-count-routing/) 에 있습니다. 즉 이건 고장 경고가 아니라, 안 쓰는 것에 매 턴 나가는 청구서입니다.

`skillmon` 은 로컬 데이터만 읽어서 어떤 스킬이 죽어 있는지, 그게 매 턴 몇 토큰을 먹는지 보여주고 정리합니다. 파이썬 표준 라이브러리만 쓰는 단일 파일이라 설치가 없습니다.

Claude Code 내장 `/plugin stats`(구 `/skill-doctor`)가 하는 일은 전부 하고, 거기에 더해 **Codex 사용량**을 함께 봅니다. `~/.claude/skills` 가 `~/.agents/skills` 심링크인 경우가 많아 두 에이전트가 같은 파일을 공유하는데, Claude Code 만 보는 리포트는 Codex 전용 스킬을 "미사용"으로 판정합니다. 그대로 지우면 사고입니다.

`--off` 는 `settings.json` 토글이라 되돌릴 수 있고, `--prune` 은 삭제가 아니라 백업 폴더로 옮기고, 전부 되돌리는 `restore.sh` 를 같이 씁니다. 둘 다 실행 전에 무엇을 할지 전부 출력하고 확인을 받습니다.

## Contributing

Adding another agent is one function. Write a `scan_<agent>(hits, cutoff)` that appends `(agent_name, timestamp)` tuples into `hits[skill_name]`, and call it from `build_rows`. PRs welcome.

Run the self-check before sending one:

```bash
python3 skillmon.py --selftest
```

## License

MIT
