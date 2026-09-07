#!/usr/bin/env python3
"""스킬 개수 -> 라우팅 정확도 실험 하네스.

조건별로 프로젝트 .claude/skills 에 N개를 심고, 글로벌 스킬은 격리
settings 의 skillOverrides 로 전부 끈 뒤 claude -p 를 돌린다.
어떤 스킬이 호출됐는지는 세션 트랜스크립트에서 읽는다.
"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

EXP = Path(__file__).parent
POOL, RUN = EXP / "pool", EXP / "run"
SKILLS = RUN / ".claude/skills"
ISOLATE = EXP / "isolate.json"


def stage(names):
    """조건 구성: 프로젝트 스킬 디렉터리를 names 로 정확히 맞춘다."""
    if SKILLS.exists():
        shutil.rmtree(SKILLS)
    SKILLS.mkdir(parents=True)
    for n in names:
        shutil.copytree(POOL / n, SKILLS / n)


def transcript(session_id):
    slug = "-" + str(RUN).strip("/").replace("/", "-")
    p = Path.home() / ".claude/projects" / slug / f"{session_id}.jsonl"
    for _ in range(20):          # 트랜스크립트 flush 대기
        if p.exists():
            return p
        time.sleep(0.3)
    return None


def skills_called(session_id):
    p = transcript(session_id)
    if not p:
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        c = d.get("message", {}).get("content")
        if isinstance(c, list):
            for b in c:
                if b.get("type") == "tool_use" and b.get("name") == "Skill":
                    out.append(str((b.get("input") or {}).get("skill", "")).split(":")[-1])
    return out


def run(prompt, model):
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
           "--settings", str(ISOLATE),
           # 스킬 본문이 실제로 네트워크·파일을 건드리지 않게 막는다.
           # Skill 툴콜 자체는 트랜스크립트에 남으므로 라우팅 신호는 얻는다.
           # SendMessage 도 막는다. 파일럿에서 도구가 막힌 실험 세션이 다른
           # 세션에 작업을 대신 시키려 메시지를 보냈다.
           "--disallowed-tools", "Bash", "WebFetch", "WebSearch", "Write", "Edit",
           "SendMessage"]
    try:
        r = subprocess.run(cmd, cwd=RUN, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        # 케이스 하나가 전체 실험을 죽이지 않게 한다. timeout 은 miss 로 센다.
        return {"error": "timeout", "cost": 0, "called": []}
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return {"error": (r.stderr or r.stdout)[:300], "cost": 0, "called": []}
    return {"cost": d.get("total_cost_usd", 0),
            "session": d.get("session_id"),
            "called": skills_called(d.get("session_id"))}


if __name__ == "__main__":
    # 자체 검증: 프로젝트 스킬 1개만 심고 그것이 호출되는지, 글로벌은 죽었는지
    stage(["korea-weather"])
    r = run("서울 날씨 어때?", sys.argv[1] if len(sys.argv) > 1 else "haiku")
    print(json.dumps(r, ensure_ascii=False))
