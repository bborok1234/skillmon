#!/usr/bin/env python3
"""조건(스킬 개수)별로 케이스를 돌려 라우팅 정확도를 측정한다."""
import json, os, random, sys
from pathlib import Path
import harness

EXP = Path(__file__).parent
CASES = json.loads((EXP / "cases.json").read_text())
POOL = sorted(p.name for p in (EXP / "pool").iterdir() if p.is_dir())
TARGETS = [c[0] for c in CASES]
DISTRACT = [n for n in POOL if n not in TARGETS]
CONDITIONS = [40, 125]   # 정답 40개만 vs 전체. sys.argv[2] 로 덮어쓸 수 있다 (예: "20,125")
OUT = EXP / "results.jsonl"

model = sys.argv[1] if len(sys.argv) > 1 else "haiku"
if len(sys.argv) > 2:
    CONDITIONS = [int(x) for x in sys.argv[2].split(",")]
done = set()
if OUT.exists():
    for l in OUT.read_text().splitlines():
        r = json.loads(l)
        done.add((r["model"], r["n"], r["target"]))

random.seed(42)   # distractor 선택 고정 - 조건 간 비교 가능하게
cost = 0.0
with OUT.open("a") as fh:
    for n in CONDITIONS:
        names = TARGETS + DISTRACT[:max(0, n - len(TARGETS))]
        harness.stage(names)
        # 조건당 스킬 목록 토큰 비용 (skillmon 과 같은 규칙)
        listing = sum(len(x) for x in names)
        for target, prompt in CASES:
            if (model, n, target) in done:
                continue
            r = harness.run(prompt, model)
            called = r.get("called", [])
            grade = ("timeout" if r.get("error") == "timeout"
                     else "correct" if target in called
                     else "miss" if not called else "wrong")
            rec = {"model": model, "n": len(names), "target": target,
                   "called": called, "grade": grade,
                   "cost": r.get("cost", 0), "error": r.get("error")}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            cost += rec["cost"]
            print(f"n={len(names):3} {target:26.26} {grade:7} {called} ${cost:.2f}",
                  flush=True)
print(f"total ${cost:.2f}")
