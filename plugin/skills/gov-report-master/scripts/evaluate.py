#!/usr/bin/env python3
"""
evaluate.py — 종합 판정 · 반려 역매핑 (Phase 6)

무엇을 하는가
--------------
게이트 스크립트들의 결과를 하나로 합쳐 **PASS / PASS-WITH-WARNINGS / FAIL** 을 내고,
FAIL이면 각 결함을 **어느 Phase로 되돌려야 하는지**(references/09 §7 역매핑) 지시한다.

  draft_guard.py    Q3~Q5 · Q8 · Q9 · Q17~Q21 + kordoc lint(overrides 적용)
  density_guard.py  Q10~Q13 · Q16                    (HWPX가 있을 때만)
  report-auditor    Q6 · Q7 · S1 · S2 · S6 · S8~S10  (의미 판단 — 사람/에이전트 몫)
  evaluate.py 자신  S3 · S4 · S5 · S7                (기계로 잡히는 정성 4종)

판정 규칙은 점수 평균이 아니라 게이트다 (references/09 §1).

  PASS                CRITICAL 0 · MAJOR 0 · 정성 필수 6개 충족 · 정성 10 중 8 이상
  PASS-WITH-WARNINGS  CRITICAL 0 · MAJOR 0 · MINOR/WARN 만
  FAIL                CRITICAL ≥1 · MAJOR ≥1 · 정성 필수 미충족

**확인하지 않은 것은 통과가 아니다.** 필수 정성 항목(S1·S2·S10)이 auditor 판정 없이
비어 있으면 조용히 넘기지 않고 MAJOR 결함으로 세운다 — 그래야 에이전트가 auditor를 돌린다.

사용법
------
  python evaluate.py --draft .gov-report/04-draft.md \
                     --profile profiles/01-policy-review.profile.json \
                     [--auditor .gov-report/06-qa-auditor.json] \
                     [--density .gov-report/density.json] \
                     [--qa .gov-report/06-qa.json]     # 회차 누적 기록
  * --draft-guard 를 주지 않으면 draft_guard.py 를 직접 돌린다.

출력 계약: exit 0=PASS · 1=FAIL · 2=PASS-WITH-WARNINGS · 3=실행 오류
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SEV_ORDER = {"CRITICAL": 0, "MAJOR": 1, "ERROR": 2, "MINOR": 3, "WARN": 4, "ADVISORY": 5}
FAIL_SEV = {"CRITICAL", "MAJOR"}

# references/09 §6 — 필수 6개. 하나라도 미충족이면 FAIL.
QUAL_REQUIRED = ["S1", "S2", "S3", "S4", "S5", "S10"]
QUAL_ALL = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"]
QUAL_LABEL = {
    "S1": "두괄식 — 첫 문단이 결론·건의인가",
    "S2": "시급성(깨포1) — '왜 지금'이 Why1에 있는가",
    "S3": "원인분석(깨포2) — 문제 나열에 그치지 않았는가",
    "S4": "How·What 분리(깨포3)",
    "S5": "문제↔방안 1:1 연결",
    "S6": "So What — 사실 뒤에 의미가 붙는가",
    "S7": "기대효과 정량 — 추상어 금지",
    "S8": "조치사항 5요소(예산·일정·주체·역할·규정)",
    "S9": "제목 정합 — 남의 다리 긁기 아닌가",
    "S10": "결재 가능성 — 무엇을 결정해야 하는지 분명한가",
}
# 기계로 재는 항목 / auditor가 재는 항목
QUAL_MACHINE = {"S3", "S4", "S5", "S7"}
QUAL_AUDITOR = {"S1", "S2", "S6", "S8", "S9", "S10"}

# references/09 §7 — 결함 → 되돌아갈 Phase
PHASE_BY_ID = {
    "Q6": "Phase 2.5", "Q7": "Phase 2.5",
    "S2": "Phase 3", "S3": "Phase 3",
    "S4": "Phase 4",
    "Q14": "Phase 7",
    "Q15": "Phase 0.5",
}
PHASE_DEFAULT = "Phase 5"

RE_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
RE_TABLE_ROW = re.compile(r"^\s*\|")
RE_DIGIT = re.compile(r"[0-9]")
RE_XREF = re.compile(r"☞\s*\S")

# 장(章) 성격 판별 키워드
KW_WHY2 = ("현황", "실태", "문제", "원인", "진단")
KW_HOW = ("방안", "대안", "개선", "정책수단", "해결", "전략")
KW_WHAT = ("추진 계획", "추진계획", "향후", "실행", "이행", "일정")
KW_EFFECT = ("기대효과", "기대 효과", "효과")

# 표 헤더로 How/What을 가른다 (references/09 §6)
HDR_HOW = ("대안", "장점", "단점", "추진가능성")
HDR_WHAT = ("단계", "기간", "주관", "산출물", "금액", "산출근거", "예산")


def load_json(p: Path) -> dict:
    # utf-8-sig: 에이전트·도구가 BOM 붙은 JSON을 쓰는 경우가 있다
    with io.open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def finding(fid: str, sev: str, loc: str, msg: str, fix: str = "",
            phase: str | None = None) -> dict:
    return {"id": fid, "severity": sev,
            "phase_to_retry": phase or PHASE_BY_ID.get(fid.split(":")[0], PHASE_DEFAULT),
            "location": loc, "message": msg, "fix_hint": fix}


# ── 문서 구조 분해 ────────────────────────────────────────────────────────
def split_sections(lines: list[str]) -> list[dict]:
    """헤딩 기준으로 장(章)을 나눈다. 헤딩이 없으면 통째로 하나."""
    secs: list[dict] = []
    cur = {"title": "(머리)", "line": 1, "body": []}
    in_fence = False
    for i, ln in enumerate(lines, start=1):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m = RE_HEADING.match(ln) if not in_fence else None
        if m and len(m.group(1)) <= 3:
            secs.append(cur)
            cur = {"title": m.group(2).strip(), "line": i, "body": []}
        else:
            cur["body"].append((i, ln))
    secs.append(cur)
    return [s for s in secs if s["body"] or s["title"] != "(머리)"]


def section_kind(title: str) -> str:
    t = title.replace(" ", "")
    if any(k.replace(" ", "") in t for k in KW_EFFECT):
        return "effect"
    if any(k.replace(" ", "") in t for k in KW_WHAT):
        return "what"
    if any(k in t for k in KW_HOW):
        return "how"
    if any(k in t for k in KW_WHY2):
        return "why2"
    return "other"


def table_headers(body: list[tuple[int, str]]) -> list[list[str]]:
    """섹션 안 표들의 헤더 행만 뽑는다."""
    out: list[list[str]] = []
    prev_row = False
    for _, ln in body:
        is_row = bool(RE_TABLE_ROW.match(ln))
        if is_row and not prev_row:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            out.append([c for c in cells if c])
        prev_row = is_row
    return out


# ── 기계로 재는 정성 4종 ──────────────────────────────────────────────────
def check_qualitative(text: str) -> tuple[dict, list[dict]]:
    lines = text.replace("\r\n", "\n").split("\n")
    secs = split_sections(lines)
    kinds = {s["title"]: section_kind(s["title"]) for s in secs}
    res: dict[str, dict] = {}
    fnd: list[dict] = []

    # ── S3 원인분석 — "원인" 헤딩 또는 항목이 실제로 있는가
    cause_at = 0
    for i, ln in enumerate(lines, start=1):
        if "원인" in ln and (RE_HEADING.match(ln) or re.match(r"^\s*[□○\-·]\s", ln)):
            cause_at = i
            break
    if cause_at:
        res["S3"] = {"verdict": "PASS", "by": "machine",
                     "evidence": "L{} 에 원인 항목".format(cause_at)}
    else:
        res["S3"] = {"verdict": "FAIL", "by": "machine",
                     "evidence": "'원인' 항목·헤딩 0건"}
        fnd.append(finding("S3", "MAJOR", "문서 전체",
                           "문제만 나열하고 원인까지 가지 않았다 (깨포2)",
                           "Why2에 '원인' 항목을 세우고 문제 뒤에 왜 그런지를 붙인다"))

    # ── S4 How·What 분리 — How 섹션에 일정·예산표가 새어들었는가
    how_secs = [s for s in secs if section_kind(s["title"]) == "how"]
    what_secs = [s for s in secs if section_kind(s["title"]) == "what"]
    if not how_secs or not what_secs:
        res["S4"] = {"verdict": "UNVERIFIABLE", "by": "machine",
                     "evidence": "How({})·What({}) 장 식별 실패".format(
                         len(how_secs), len(what_secs))}
    else:
        leaked = []
        for s in how_secs:
            for hdr in table_headers(s["body"]):
                hits = [h for h in hdr if any(w in h for w in HDR_WHAT)]
                if len(hits) >= 2 and not any(w in " ".join(hdr) for w in HDR_HOW):
                    leaked.append((s["title"], hdr))
        if leaked:
            res["S4"] = {"verdict": "FAIL", "by": "machine",
                         "evidence": "How 장에 What 표: " + str(leaked[0][1])}
            fnd.append(finding("S4", "MAJOR", leaked[0][0],
                               "How 장에 일정·예산표가 있다 — What이 How로 새어들었다 (깨포3)",
                               "일정·예산·주관 표는 추진계획(What) 장으로 옮긴다"))
        else:
            res["S4"] = {"verdict": "PASS", "by": "machine",
                         "evidence": "How {}장·What {}장 분리 확인".format(
                             len(how_secs), len(what_secs))}

    # ── S5 문제↔방안 1:1 — '☞' 명시 참조
    xref_n = len(RE_XREF.findall(text))
    why2 = [s for s in secs if section_kind(s["title"]) == "why2"]
    prob_items = 0
    for s in why2:
        prob_items += sum(1 for _, ln in s["body"] if re.match(r"^\s*[○\-]\s", ln))
    if not why2:
        res["S5"] = {"verdict": "UNVERIFIABLE", "by": "machine",
                     "evidence": "현황·문제점 장 식별 실패"}
    elif xref_n == 0 and prob_items >= 2:
        res["S5"] = {"verdict": "FAIL", "by": "machine",
                     "evidence": "문제 항목 {}개 · '☞' 참조 0건".format(prob_items)}
        fnd.append(finding("S5", "MAJOR", why2[0]["title"],
                           "문제점과 개선방안이 명시적으로 이어지지 않았다",
                           "문제 항목 끝에 '→ ☞ 개선방안 (N단계)' 를 붙인다"))
    else:
        res["S5"] = {"verdict": "PASS", "by": "machine",
                     "evidence": "'☞' 참조 {}건".format(xref_n)}

    # ── S7 기대효과 정량 — 숫자·단위가 하나도 없으면 추상어 덩어리다
    eff = [s for s in secs if section_kind(s["title"]) == "effect"]
    if not eff:
        res["S7"] = {"verdict": "UNVERIFIABLE", "by": "machine",
                     "evidence": "기대효과 장 없음"}
    else:
        has_num = any(RE_DIGIT.search(ln) for s in eff for _, ln in s["body"])
        if has_num:
            res["S7"] = {"verdict": "PASS", "by": "machine", "evidence": "숫자 포함"}
        else:
            res["S7"] = {"verdict": "FAIL", "by": "machine", "evidence": "숫자 0개"}
            fnd.append(finding("S7", "ERROR", eff[0]["title"],
                               "기대효과에 숫자가 없다 — '제고·강화' 같은 추상어뿐이다",
                               "'민원 처리 12일 → 5일' 처럼 전후 수치로 쓴다"))
    return res, fnd


# ── 게이트 결과 수집 ──────────────────────────────────────────────────────
def run_draft_guard(draft: Path, profile: Path | None, type_id: str | None,
                    final: bool) -> dict:
    cmd = [sys.executable, str(HERE / "draft_guard.py"), str(draft), "--json-only"]
    if profile:
        cmd += ["--profile", str(profile)]
    if type_id:
        cmd += ["--type", type_id]
    if final:
        cmd.append("--final")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa: BLE001
        return {"verdict": "ERROR", "findings": [],
                "message": (p.stderr or p.stdout)[-400:]}


def adopt(src: str, result: dict, out: list[dict]) -> None:
    """게이트 결과의 findings 를 그대로 흡수한다. 등급은 각 게이트가 정본."""
    for f in result.get("findings", []):
        g = dict(f)
        g["source"] = src
        g.setdefault("phase_to_retry",
                     PHASE_BY_ID.get(str(g.get("id", "")).split(":")[0], PHASE_DEFAULT))
        out.append(g)


def merge_auditor(aud: dict, qual: dict, out: list[dict]) -> None:
    """report-auditor 산출을 흡수한다.

    형식: {"qualitative": {"S1": {"verdict": "PASS"|"FAIL"|"UNVERIFIABLE",
                                  "evidence": "..."} …},
           "findings": [ …Q6·Q7 등… ]}
    """
    for k, v in (aud.get("qualitative") or {}).items():
        if k not in QUAL_ALL:
            continue
        qual[k] = {"verdict": str(v.get("verdict", "UNVERIFIABLE")).upper(),
                   "by": "auditor", "evidence": v.get("evidence", "")}
    adopt("auditor", aud, out)


def main() -> int:
    ap = argparse.ArgumentParser(description="종합 판정 · 반려 역매핑")
    ap.add_argument("--draft", required=True)
    ap.add_argument("--profile")
    ap.add_argument("--type", dest="type_id")
    ap.add_argument("--draft-guard", help="draft_guard.py 결과 JSON (없으면 직접 실행)")
    ap.add_argument("--density", help="density_guard.py 결과 JSON")
    ap.add_argument("--auditor", help="report-auditor 산출 JSON")
    ap.add_argument("--sources", help="00-sources.json (출처 레지스트리)")
    ap.add_argument("--final", action="store_true", help="제출용 B 기준으로 검사")
    ap.add_argument("--qa", help="06-qa.json — 회차 누적 기록 경로")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    try:
        draft = Path(args.draft)
        text = io.open(draft, encoding="utf-8").read()
        profile = Path(args.profile) if args.profile else None
        prof = load_json(profile) if profile and profile.exists() else {}
        type_id = args.type_id or prof.get("type_id") or "01"

        findings: list[dict] = []
        gates: dict[str, str] = {}

        dg = (load_json(Path(args.draft_guard)) if args.draft_guard
              else run_draft_guard(draft, profile, type_id, args.final))
        gates["draft_guard"] = dg.get("verdict", "?")
        adopt("draft_guard", dg, findings)

        if args.density:
            den = load_json(Path(args.density))
            gates["density_guard"] = den.get("verdict", "?")
            adopt("density_guard", den, findings)

        qual, qfnd = check_qualitative(text)
        findings += qfnd

        if args.auditor:
            merge_auditor(load_json(Path(args.auditor)), qual, findings)
            gates["auditor"] = "적용"

        # ── 확인하지 않은 것은 통과가 아니다 ─────────────────────────────
        unverifiable: list[dict] = []
        for k in QUAL_ALL:
            if k not in qual:
                qual[k] = {"verdict": "UNVERIFIABLE", "by": "-",
                           "evidence": "auditor 판정 없음"}
        for k in QUAL_ALL:
            if qual[k]["verdict"] != "UNVERIFIABLE":
                continue
            unverifiable.append({"id": k, "label": QUAL_LABEL[k],
                                 "reason": qual[k]["evidence"]})
            if k in QUAL_REQUIRED:
                findings.append(finding(
                    k, "MAJOR", "문서 전체",
                    "{} — 판정되지 않았다".format(QUAL_LABEL[k]),
                    "report-auditor 를 돌려 이 항목을 판정한다",
                    phase="Phase 6"))
            elif k in QUAL_AUDITOR:
                findings.append(finding(
                    k, "ADVISORY", "문서 전체",
                    "{} — 미판정".format(QUAL_LABEL[k]),
                    "report-auditor 판정 권장", phase="Phase 6"))

        for k in QUAL_REQUIRED:
            if qual[k]["verdict"] == "FAIL" and not any(f["id"] == k for f in findings):
                findings.append(finding(k, "MAJOR", "문서 전체",
                                        "{} — 미충족".format(QUAL_LABEL[k])))

        # ── 판정 ────────────────────────────────────────────────────────
        findings.sort(key=lambda x: (SEV_ORDER.get(x.get("severity", "WARN"), 9),
                                     str(x.get("id"))))
        counts: dict[str, int] = {}
        for f in findings:
            s = f.get("severity", "WARN")
            counts[s] = counts.get(s, 0) + 1

        passed_q = sum(1 for k in QUAL_ALL if qual[k]["verdict"] == "PASS")
        req_ok = all(qual[k]["verdict"] == "PASS" for k in QUAL_REQUIRED)

        if any(f.get("severity") in FAIL_SEV for f in findings) or not req_ok:
            verdict, code = "FAIL", 1
        elif passed_q < 8:
            verdict, code = "PASS-WITH-WARNINGS", 2
        elif any(f.get("severity") in ("ERROR", "MINOR", "WARN") for f in findings):
            verdict, code = "PASS-WITH-WARNINGS", 2
        else:
            verdict, code = "PASS", 0

        # ── 반려 계획 (§7 역매핑) ───────────────────────────────────────
        plan: dict[str, dict] = {}
        for f in findings:
            if f.get("severity") not in FAIL_SEV:
                continue
            ph = f.get("phase_to_retry", PHASE_DEFAULT)
            e = plan.setdefault(ph, {"phase": ph, "count": 0, "ids": []})
            e["count"] += 1
            if f["id"] not in e["ids"]:
                e["ids"].append(f["id"])
        retry_plan = sorted(plan.values(), key=lambda x: -x["count"])
        for e in retry_plan:
            e["regate"] = ("G4 재승인 필요" if e["phase"] == "Phase 3"
                           else "G5 재승인 필요" if e["phase"] == "Phase 4" else "")

        # ── 회차 누적 ───────────────────────────────────────────────────
        rounds: list[dict] = []
        escalate = False
        if args.qa:
            qa_path = Path(args.qa)
            if qa_path.exists():
                rounds = (load_json(qa_path) or {}).get("rounds", [])
            entry = {"round": len(rounds) + 1, "verdict": verdict, "counts": counts,
                     "retry": [e["phase"] for e in retry_plan],
                     "qual_pass": passed_q}
            if rounds:
                prev = rounds[-1]
                entry["delta"] = {
                    s: counts.get(s, 0) - prev["counts"].get(s, 0)
                    for s in set(list(counts) + list(prev["counts"]))}
            rounds.append(entry)
            # 같은 Phase를 2회 넘게 되돌렸으면 자가수정을 멈춘다 (§7 루프 제어)
            for ph in {p for r in rounds for p in r.get("retry", [])}:
                if sum(1 for r in rounds if ph in r.get("retry", [])) > 2:
                    escalate = True
            qa_path.parent.mkdir(parents=True, exist_ok=True)
            io.open(qa_path, "w", encoding="utf-8").write(
                json.dumps({"type_id": type_id, "rounds": rounds},
                           ensure_ascii=False, indent=2))

        result = {"verdict": verdict, "type_id": type_id, "gates": gates,
                  "counts": counts, "qualitative": qual,
                  "qual_pass": passed_q, "required_ok": req_ok,
                  "findings": findings, "unverifiable": unverifiable,
                  "retry_plan": retry_plan, "escalate": escalate,
                  "rounds": rounds}
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not args.json_only:
            w = sys.stderr
            print("\n[evaluate] {}  유형 {}  →  {}".format(draft.name, type_id, verdict),
                  file=w)
            print("  게이트 " + " · ".join("{}={}".format(k, v) for k, v in gates.items()),
                  file=w)
            print("  정성 {}/10 통과 · 필수 6개 {}".format(
                passed_q, "충족" if req_ok else "미충족"), file=w)
            for k in QUAL_ALL:
                mark = {"PASS": "○", "FAIL": "✗", "UNVERIFIABLE": "?"}.get(
                    qual[k]["verdict"], "?")
                print("   {} {:4} {:34} {}".format(
                    mark, k, QUAL_LABEL[k][:32], qual[k]["evidence"][:34]), file=w)
            for f in findings[:20]:
                print("   [{:8}] {:8} {:10} {}".format(
                    f.get("severity", ""), str(f.get("id"))[:8],
                    f.get("phase_to_retry", ""), str(f.get("message"))[:74]), file=w)
            if len(findings) > 20:
                print("   … 외 {}건".format(len(findings) - 20), file=w)
            if retry_plan:
                print("  ▶ 반려 계획", file=w)
                for e in retry_plan:
                    print("     {} ← {}건 {} {}".format(
                        e["phase"], e["count"], ",".join(e["ids"][:6]), e["regate"]),
                        file=w)
            if escalate:
                print("  ⚠ 같은 단계를 2회 넘게 되돌렸다 — 자가수정을 멈추고 "
                      "사용자에게 선택지를 제시할 것", file=w)
            if unverifiable:
                print("  ⓘ 확인 불가 {}건 — 통과로 넘기지 않는다: {}".format(
                    len(unverifiable), ", ".join(u["id"] for u in unverifiable)), file=w)
        return code

    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        print("[evaluate] 실행 오류: " + str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
