#!/usr/bin/env python3
"""skillmon - 코딩 에이전트 스킬 사용량·컨텍스트 비용 조사 및 정리.

Claude Code 의 /plugin stats (구 /skill-doctor) 가 하는 일을 전부 하되,
Codex 까지 함께 본다. 셋 다 로컬 데이터만 읽는다.

  python3 skillmon.py                # 리포트 (사용량 + 상시 컨텍스트 비용)
  python3 skillmon.py --days 90      # 윈도우 조정 (기본 180일)
  python3 skillmon.py --unused       # 미사용/판단보류만
  python3 skillmon.py --off          # 되돌릴 수 있게 settings 에서 끄기  (권장)
  python3 skillmon.py --prune        # 디스크에서 치우기 (백업 이동 + plugin uninstall)
  python3 skillmon.py --json         # 기계 판독용

# ponytail: 실시간 기록 데몬은 안 만든다. 두 에이전트가 이미 카운터와
# 트랜스크립트에 남기므로 그걸 읽는다. 데몬은 로그가 사라질 때 추가.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CLAUDE_JSON = HOME / ".claude.json"            # skillUsage / pluginUsage 카운터
USER_SETTINGS = HOME / ".claude/settings.json"
INSTALLED = HOME / ".claude/plugins/installed_plugins.json"
PLUGIN_CACHE = HOME / ".claude/plugins/cache"
INVENTORY = [
    HOME / ".claude/skills",
    PLUGIN_CACHE,   # 실제 설치본. marketplaces/ 는 클론 원본이라 제외
    HOME / ".codex/skills",
]
SKILL_MD_RE = re.compile(r"skills/([A-Za-z0-9._-]+)/SKILL\.md")
MCP_TOOL_RE = re.compile(r"^mcp__([A-Za-z0-9_.-]+)__")
FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)

# Claude Code 의 스킬 목록 예산: 컨텍스트의 1%, 스킬당 설명 1536자 상한.
DESC_CAP = 1536
LISTING_BUDGET = 0.01
CHARS_PER_TOKEN = 4


# ---------- 인벤토리 ----------

def plugin_map():
    """{installPath(버전 제외): "plugin@marketplace"}"""
    try:
        data = json.loads(INSTALLED.read_text())
    except (OSError, ValueError):
        return {}
    # installPath 끝의 버전 세그먼트는 뗀다 - cache 에 구버전 디렉터리가 남아있다
    return {str(Path(inst["installPath"]).parent): pid
            for pid, insts in data.get("plugins", {}).items()
            for inst in insts if inst.get("installPath")}


def frontmatter(md):
    """SKILL.md 의 name/description. 모델 컨텍스트에 상시 올라가는 부분이다."""
    try:
        text = md.read_text(errors="replace")[:8000]
    except OSError:
        return "", ""
    m = FM_RE.match(text)
    if not m:
        return "", ""
    out = {}
    key = None
    for line in m.group(1).splitlines():
        if re.match(r"^\w[\w-]*:", line):
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip().strip("'\"")
        elif key and line.startswith((" ", "\t")):
            out[key] += " " + line.strip()
    return out.get("name", ""), out.get("description", "")


def disabled():
    """이미 꺼둔 것. 컨텍스트에 안 올라가므로 비용 0으로 친다."""
    try:
        cfg = json.loads(USER_SETTINGS.read_text())
    except (OSError, ValueError):
        return set(), set()
    return ({k for k, v in (cfg.get("skillOverrides") or {}).items() if v == "off"},
            {k for k, v in (cfg.get("enabledPlugins") or {}).items() if v is False})


def find_skills():
    """{name: {path, desc_chars}}"""
    out = {}
    for root in INVENTORY:
        if not root.is_dir():
            continue
        # followlinks: ~/.claude/skills/* 는 ~/.agents/skills 로의 심링크다
        for dirpath, dirnames, files in os.walk(root, followlinks=True):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            if "SKILL.md" not in files:
                continue
            d = Path(dirpath)
            dirnames[:] = []  # 스킬 안쪽은 더 안 판다
            if d.name in out:
                continue
            name, desc = frontmatter(d / "SKILL.md")
            out[d.name] = {"path": d,
                           "desc_chars": len(name or d.name) + min(len(desc), DESC_CAP)}
    return out


# ---------- 사용량 ----------

def counters():
    """Claude Code 내장 카운터. 훅/자동 로드 스킬은 여기에만 잡힌다.

    ponytail 처럼 훅으로 상시 켜지는 스킬은 Skill 툴콜을 남기지 않아
    트랜스크립트만 보면 0회로 오판한다. 이쪽이 정본이다.
    """
    try:
        d = json.loads(CLAUDE_JSON.read_text())
    except (OSError, ValueError):
        return {}, {}
    skill = defaultdict(lambda: [0, None])
    for key, v in (d.get("skillUsage") or {}).items():
        s = skill[key.split(":")[-1]]
        s[0] += v.get("usageCount", 0)
        ts = v.get("lastUsedAt")
        if ts:
            s[1] = max(s[1] or 0, ts / 1000)
    return dict(skill), (d.get("pluginUsage") or {})


def iter_jsonl(root, cutoff):
    if not root.is_dir():
        return
    for f in root.rglob("*.jsonl"):
        try:
            if cutoff and f.stat().st_mtime < cutoff:
                continue
            with f.open(errors="replace") as fh:
                for line in fh:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError:
            continue


def ts_of(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def scan_claude(hits, mcp, cutoff):
    """윈도우 내 사용 여부. Skill 툴콜과 /슬래시 호출이 확실한 신호."""
    for rec in iter_jsonl(HOME / ".claude/projects", cutoff):
        t = ts_of(rec.get("timestamp"))
        if cutoff and t and t < cutoff:
            continue
        content = rec.get("message", {}).get("content")
        if isinstance(content, str):
            for m in re.finditer(r"<command-name>/?([\w:-]+)</command-name>", content):
                hits[m.group(1).split(":")[-1]].append(("claude", t))
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if b.get("type") != "tool_use":
                continue
            inp = b.get("input") or {}
            name = b.get("name", "")
            if name == "Skill":
                s = str(inp.get("skill", "")).split(":")[-1]
                if s:
                    hits[s].append(("claude", t))
            elif MCP_TOOL_RE.match(name):
                mcp[MCP_TOOL_RE.match(name).group(1)].append(t)
            elif name in ("Bash", "Read"):
                for m in SKILL_MD_RE.finditer(json.dumps(inp)):
                    hits[m.group(1)].append(("claude", t))


def scan_codex(hits, cutoff):
    """Codex 는 전용 skill 툴콜이 없어 exec 안의 SKILL.md 경로가 최선.

    /plugin stats 가 못 보는 영역이다 - 두 에이전트가 ~/.agents/skills 를
    심링크로 공유하므로, Codex 에서만 쓰는 스킬을 미사용으로 지우면 안 된다.
    """
    for rec in iter_jsonl(HOME / ".codex/sessions", cutoff):
        t = ts_of(rec.get("timestamp"))
        if cutoff and t and t < cutoff:
            continue
        p = rec.get("payload")
        if not isinstance(p, dict) or p.get("type") not in (
                "function_call", "custom_tool_call", "local_shell_call"):
            continue
        blob = json.dumps(p.get("arguments") or p.get("input") or "")
        for m in SKILL_MD_RE.finditer(blob):
            hits[m.group(1)].append(("codex", t))


# ---------- 판정 ----------

def build_rows(a):
    cutoff = time.time() - a.days * 86400 if a.days else None
    skills = find_skills()
    plugins = plugin_map()
    life, plugin_life = counters()
    off_skills, off_plugins = disabled()
    hits, mcp = defaultdict(list), defaultdict(list)
    scan_claude(hits, mcp, cutoff)
    scan_codex(hits, cutoff)

    def owner(path):
        return next((pid for ip, pid in plugins.items()
                     if str(path).startswith(ip + "/")), "")

    rows = []
    for name, meta in sorted(skills.items()):
        h = hits.get(name, [])
        pid = owner(meta["path"])
        lifetime, last = life.get(name, (0, None))
        # 플러그인 카운터는 훅·에이전트 호출로도 올라가므로 이 스킬을 쓴 증거가 아니다.
        # keep 근거로는 못 쓰고, remove 를 ask 로 낮추는 데만 쓴다.
        sibling = plugin_life.get(pid, {}).get("usageCount", 0) if pid else 0
        window = len(h)
        agents = sorted({s for s, _ in h})
        last = max([last or 0] + [t for _, t in h if t]) or None

        orphan = (not pid and str(meta["path"]).startswith(str(PLUGIN_CACHE)))
        if orphan:
            # uninstall 은 등록만 지우고 cache 디렉터리는 남긴다. 컨텍스트에는
            # 안 올라가니 비용 0이지만 디스크에 남아 있으므로 치울 수 있게 표시.
            verdict, meta["desc_chars"] = "orphan", 0
        elif name in off_skills or pid in off_plugins:
            verdict, meta["desc_chars"] = "off", 0
        elif window or lifetime:
            verdict = "keep"
        elif sibling or not meta["desc_chars"]:
            # 플러그인은 쓰는데 이 스킬만 흔적이 없거나, frontmatter 가 없어
            # 목록에도 안 뜨는 경우. 데이터로는 판정 불가 - 사용자에게 묻는다.
            verdict = "ask"
        else:
            verdict = "remove"

        rows.append({
            "skill": name,
            "window": window,
            "lifetime": lifetime,
            "plugin_uses": sibling,
            "agents": ",".join(agents) or "-",
            "last_used": datetime.fromtimestamp(last).strftime("%Y-%m-%d") if last else "-",
            "tokens": meta["desc_chars"] // CHARS_PER_TOKEN,
            "verdict": verdict,
            "plugin": pid,
            "path": str(meta["path"]),
        })
    return rows, skills, plugins, mcp


# ---------- 출력 ----------

def report(rows, mcp, a):
    budget = int(a.context * LISTING_BUDGET)
    total = sum(r["tokens"] for r in rows)
    waste = sum(r["tokens"] for r in rows if r["verdict"] != "keep")

    print(f"{'skill':34} {'윈도':>5} {'누적':>7} {'토큰':>5} {'agent':7} {'마지막':10} {'판정':6} plugin")
    for r in rows:
        print(f"{r['skill']:34.34} {r['window']:5} {r['lifetime']:7} {r['tokens']:5} "
              f"{r['agents']:7.7} {r['last_used']:10} {r['verdict']:6} {r['plugin']}")

    print(f"\n스킬 {len(rows)}개 · 최근 {a.days}일 윈도우")
    print(f"상시 컨텍스트 비용 {total:,} 토큰 / 목록 예산 {budget:,} "
          f"({total * 100 // max(budget, 1)}%)"
          + ("  ← 예산 초과: 설명이 잘려 스킬 라우팅이 나빠진다" if total > budget else ""))
    print(f"미사용분이 먹는 몫 {waste:,} 토큰 (매 턴, 전체의 {waste * 100 // max(total, 1)}%)")
    if mcp:
        print("MCP 서버 호출: " + ", ".join(
            f"{k}={len(v)}" for k, v in sorted(mcp.items(), key=lambda x: -len(x[1]))[:8]))
    print("판정: keep=사용흔적 있음 / off=이미 꺼둠(비용 0) / orphan=uninstall 후 남은 파일 / remove=윈도우·누적 모두 0 / ask=신호 없음(직접 확인)")
    print("'누적'은 ~/.claude.json 내장 카운터(설치 이후 전체, Claude Code 한정), "
          "'윈도'는 트랜스크립트 기준이라 Codex 도 포함")


# ---------- 정리 ----------

def turn_off(rows, a):
    """되돌릴 수 있는 비활성화. 파일은 그대로 두고 settings 만 건드린다."""
    plugs = dead_plugins(rows, a)
    # 플러그인 스킬도 skillOverrides 로 개별로 끌 수 있다. 플러그인 전체가
    # 대상일 때만 플러그인 단위 disable 로 올린다.
    skills = [r["skill"] for r in rows if r["plugin"] not in plugs]
    if not skills and not plugs:
        print("끌 것 없음")
        return
    print(f"\n{USER_SETTINGS} 에 기록:")
    for s in skills:
        print(f'  skillOverrides["{s}"] = "off"')
    for p in sorted(plugs):
        print(f'  enabledPlugins["{p}"] = false')
    if not confirm(a):
        return
    try:
        cfg = json.loads(USER_SETTINGS.read_text())
    except (OSError, ValueError):
        cfg = {}
    shutil.copy(USER_SETTINGS, str(USER_SETTINGS) + ".skillmon.bak")
    cfg.setdefault("skillOverrides", {}).update({s: "off" for s in skills})
    cfg.setdefault("enabledPlugins", {}).update({p: False for p in plugs})
    USER_SETTINGS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print(f"기록 완료. 되돌리기: 해당 항목 삭제 또는 "
          f"mv {USER_SETTINGS}.skillmon.bak {USER_SETTINGS}")


def dead_plugins(rows, a):
    """플러그인은 스킬 단위로 못 지운다 - 스킬이 전부 미사용일 때만."""
    dead = {r["skill"] for r in rows}
    drop, held = set(), {}
    all_skills = a._skills
    for r in rows:
        pid = r["plugin"]
        if not pid:
            continue
        ip = next(p for p, q in a._plugins.items() if q == pid)
        alive = [n for n, m in all_skills.items()
                 if str(m["path"]).startswith(ip + "/") and n not in dead]
        (held.setdefault(pid, alive) if alive else drop.add(pid))
    for pid, alive in sorted(held.items()):
        print(f"skip: {pid} - 같은 플러그인의 {', '.join(sorted(alive))} 사용중")
    return drop


def confirm(a):
    if a.yes:
        return True
    print("계속? [y/N] ", end="")
    if input().strip().lower() == "y":
        return True
    print("취소")
    return False


def prune(rows, a):
    """디스크에서 치운다. --off 로 충분하면 그쪽이 안전하다."""
    loose = [r for r in rows if not r["plugin"]]
    plugs = dead_plugins(rows, a)
    if not loose and not plugs:
        print("정리할 것 없음")
        return
    dest = HOME / ".claude/backups" / f"skillmon-{datetime.now():%Y%m%d-%H%M%S}"
    print(f"\n스킬 {len(loose)}개 -> {dest}")
    for pid in sorted(plugs):
        print(f"플러그인 제거: claude plugin uninstall {pid}")
    if not confirm(a):
        return
    if loose:
        dest.mkdir(parents=True)
        # ~/.claude/skills/* 는 대개 상대 심링크다. 그냥 mv 하면 상대 경로가
        # 깨져 백업이 dangling link 가 되므로, 링크는 지우고 되살리는 법을
        # restore.sh 에 적는다.
        lines = ["#!/bin/sh", "# skillmon restore"]
        for r in loose:
            src = Path(r["path"])
            if src.is_symlink():
                lines.append(f"ln -s {os.readlink(src)!r} {str(src)!r}")
                src.unlink()
                print("unlinked", r["skill"])
            else:
                shutil.move(str(src), dest / r["skill"])
                lines.append(f"mv {str(dest / r['skill'])!r} {str(src)!r}")
                print("moved", r["skill"])
        sh = dest / "restore.sh"
        sh.write_text("\n".join(lines) + "\n")
        sh.chmod(0o755)
        print(f"복구: sh {sh}")
    for pid in sorted(plugs):
        rc = subprocess.run(["claude", "plugin", "uninstall", pid, "-y"]).returncode
        print(("uninstalled " if rc == 0 else "실패(수동 /plugin) ") + pid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--context", type=int, default=200_000, help="컨텍스트 창 토큰 수")
    ap.add_argument("--unused", action="store_true", help="keep 이 아닌 것만")
    ap.add_argument("--off", action="store_true", help="settings 에서 끄기 (되돌릴 수 있음)")
    ap.add_argument("--prune", action="store_true", help="디스크에서 치우기")
    ap.add_argument("--keep", default="", help="쉼표구분, 절대 건드리지 않을 스킬")
    ap.add_argument("--include-ask", action="store_true",
                    help="판정보류(ask)까지 --off/--prune 대상에 포함")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-y", "--yes", action="store_true", help="확인 프롬프트 생략")
    a = ap.parse_args()

    rows, skills, plugins, mcp = build_rows(a)
    a._skills, a._plugins = skills, plugins
    rows.sort(key=lambda r: (r["window"] + r["lifetime"], -r["tokens"], r["skill"]))

    acting = a.off or a.prune
    if a.unused or acting:
        keep = {s.strip() for s in a.keep.split(",") if s.strip()}
        shown = [r for r in rows if r["verdict"] not in ("keep", "off")
                 and r["skill"] not in keep]
    else:
        shown = rows

    if a.json:
        print(json.dumps(shown, ensure_ascii=False, indent=2))
    else:
        report(shown, {} if acting else mcp, a)

    targets = {"remove", "ask", "orphan"} if a.include_ask else {"remove", "orphan"}
    hit = [r for r in shown if r["verdict"] in targets]
    if a.off:
        turn_off(hit, a)
    elif a.prune:
        prune(hit, a)


def _selftest():
    assert SKILL_MD_RE.search("cat ~/.claude/skills/foo-bar/SKILL.md").group(1) == "foo-bar"
    assert MCP_TOOL_RE.match("mcp__claude-in-chrome__navigate").group(1) == "claude-in-chrome"
    assert ts_of("2026-08-04T05:00:43.259Z") > 0
    assert ts_of(None) is None and ts_of("garbage") is None
    s = find_skills()
    assert s, "no skills found"
    assert any(v["desc_chars"] > 20 for v in s.values()), "frontmatter parse broken"
    life, plug = counters()
    assert life, "usage counters not read"
    print("ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
