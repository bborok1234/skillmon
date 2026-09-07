#!/usr/bin/env python3
"""조건별 라우팅 정확도 + 스킬 목록 토큰 비용."""
import json, re, collections
from pathlib import Path

EXP = Path(__file__).parent
POOL = EXP / "pool"
FM = re.compile(r"^---\n(.*?)\n---", re.S)
CASES = json.loads((EXP / "cases.json").read_text())
TARGETS = [c[0] for c in CASES]
DISTRACT = sorted(p.name for p in POOL.iterdir() if p.is_dir() and p.name not in TARGETS)


def desc_chars(name):
    """skillmon 과 동일한 규칙: name + description(1536자 상한)."""
    t = (POOL / name / "SKILL.md").read_text(errors="replace")[:8000]
    m = FM.match(t)
    if not m:
        return len(name)
    d = {}
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^\w[\w-]*:", line):
            key, _, v = line.partition(":")
            d[key.strip()] = v.strip().strip("'\"")
        elif key and line.startswith((" ", "\t")):
            d[key] += " " + line.strip()
    return len(d.get("name", name)) + min(len(d.get("description", "")), 1536)


rows = [json.loads(l) for l in (EXP / "results.jsonl").read_text().splitlines()]
by = collections.defaultdict(list)
for r in rows:
    by[(r["model"], r["n"])].append(r)

BUDGET = 2000   # 컨텍스트 200k 의 1%
print(f"{'model':7} {'skills':>6} {'listing tok':>11} {'예산%':>6} "
      f"{'correct':>8} {'wrong':>6} {'miss':>5} {'정확도':>7}")
for (model, n), rs in sorted(by.items(), key=lambda x: (x[0][0], x[0][1])):
    names = TARGETS + DISTRACT[:max(0, n - len(TARGETS))]
    tok = sum(desc_chars(x) for x in names) // 4
    g = collections.Counter(r["grade"] for r in rs)
    acc = g["correct"] / len(rs) * 100
    print(f"{model:7} {n:6} {tok:11,} {tok*100//BUDGET:5}% "
          f"{g['correct']:8} {g['wrong']:6} {g['miss']:5} {acc:6.0f}%")

print("\n케이스별 (correct=✓ wrong=✗ miss=·)")
ns = sorted({n for _, n in by})
print(f"{'case':26} " + " ".join(f"{n:>4}" for n in ns))
for t in TARGETS:
    cells = []
    for n in ns:
        r = next((x for x in by[(rows[0]['model'], n)] if x["target"] == t), None)
        cells.append({"correct": "✓", "wrong": "✗", "miss": "·"}.get(r and r["grade"], "?"))
    print(f"{t:26.26} " + " ".join(f"{c:>4}" for c in cells))

wrong = collections.Counter(
    (r["target"], tuple(r["called"])) for r in rows if r["grade"] == "wrong")
if wrong:
    print("\n오라우팅 상위")
    for (t, c), k in wrong.most_common(8):
        print(f"  {t} -> {list(c)}  x{k}")
print(f"\n총 비용 ${sum(r['cost'] for r in rows):.2f}")
