#!/usr/bin/env python3
"""
draft_guard.py — 원고(마크다운) 게이트

kordoc 이 하는 것은 kordoc 에 위임하고, 우리 불변식만 자체 구현한다.

  kordoc 이 한다 : 표기법 13룰 · 개조식 문체 12룰 · AI_* 슬롭
  우리가 한다   : 환각방지 3종([AI]·{{ }}·검증표) · 유형별 룰 on/off ·
                  references/02-writing.md 자체 규칙 · 깨포 기계검사

kordoc 결과는 `profiles/lint-overrides.json` 으로 심각도를 재정의한 뒤 합친다.
그대로 흘리면 에이전트가 무해한 경고에 반응해 본문을 망친다.

사용법
------
  python draft_guard.py <원고.md> --profile profiles/01-policy-review.profile.json
  python draft_guard.py <원고.md> --type 01 [--final] [--baseline] [--json-only]

  --final    제출용 B 검사 (플레이스홀더 0건 강제)
  --baseline 골든 캘리브레이션 모드 — 판정을 내지 않고 현황만 기록

출력 계약 (references/09-evaluation.md §8)
  exit 0=PASS · 1=FAIL · 2=PASS-WITH-WARNINGS · 3=실행 오류
  stdout {"verdict","findings":[…],"metrics":{},"unverifiable":[]}
  stderr 사람이 읽을 리포트
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 실행 환경이 cp949 일 수 있다(확인됨: stdout=cp949, locale=cp949).
# 한글 경로·본문을 출력하는 순간 UnicodeEncodeError 가 나므로 첫머리에서 강제한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

HERE = Path(__file__).resolve().parent
PROFILES = HERE.parent / "profiles"
OVERRIDES_PATH = PROFILES / "lint-overrides.json"

SEV_ORDER = {"CRITICAL": 0, "MAJOR": 1, "ERROR": 2, "MINOR": 3, "WARN": 4, "ADVISORY": 5}
FAIL_SEV = {"CRITICAL", "MAJOR", "ERROR"}

# ── references/02-writing.md 자체 규칙 사전 ──────────────────────────────
HARD_WORDS = {
    "경감시키는": "줄이는", "계출": "신고", "토괴": "흙덩이", "가내시": "임시통보",
    "갱구": "터널 출입구", "밀식되어": "빽빽하게 심어져", "도선장": "나루터",
    "재차": "다시", "과년도": "지난해", "식재적기": "심기 좋은 때",
}
VAGUE_WORDS = ["의미 있는", "적합한", "일리 있는", "바람직한", "적정한"]
NARRATIVE_END = re.compile(r"(?:이다|한다|있다|없다|였다|했다|된다|것이다)\s*[.]?\s*$")
NOMINAL_END = re.compile(
    r"(?:함|임|음|됨|있음|없음|필요|예정|추진|검토|가능|곤란|우려|완료|실시|확보|"
    r"제고|마련|지원|운영|구축|개선|강화|등)\s*$"
)


# 공문서 관용 기호 — 이모지가 아니다. CORE RULE 10 의 "본문 이모지 금지"에 걸리면 안 된다.
# ☞ 는 S5(문제↔방안 명시 연결)에서 우리가 권장하는 기호이므로 반드시 여기 있어야 한다.
# ❘❙❚ 는 표준서식이 문제·방향 박스 머리에 쓰는 세로 막대다 — 이모지가 아니다.
DOC_SYMBOLS = set(
    "☞→⇒⇨➡▸▶►‣※◈◇▣□■○●◦★☆▲△▼▽◀◁◎❘❙❚"
    "＊*✚✕✗✓✔↑↓←↔∼~─━│┃┌┐└┘├┤┬┴┼"
)
# 이모지로 볼 코드포인트 블록
EMOJI_BLOCKS = (
    (0x1F000, 0x1FAFF),   # 그림문자·이모티콘 전반
    (0x2600, 0x27BF),     # 기타 기호·딩벳 (관용 기호는 DOC_SYMBOLS 로 제외)
    (0x2B00, 0x2BFF),     # 화살표·기하 도형 확장
)


def has_emoji(line: str) -> bool:
    """관용 기호를 뺀 진짜 이모지가 있는가."""
    for ch in line:
        if ch in DOC_SYMBOLS:
            continue
        cp = ord(ch)
        if cp == 0xFE0F:                       # 이모지 표현 선택자
            return True
        if any(a <= cp <= b for a, b in EMOJI_BLOCKS):
            return True
    return False


def load_json(p: Path) -> dict:
    # utf-8-sig: 에이전트·도구가 BOM 붙은 JSON을 쓰는 경우가 있다
    with io.open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def finding(fid: str, sev: str, phase: str, loc: str, msg: str, fix: str = "") -> dict:
    return {"id": fid, "severity": sev, "phase_to_retry": phase,
            "location": loc, "message": msg, "fix_hint": fix}


# ── 마스킹 ───────────────────────────────────────────────────────────────
def build_masks(overrides: dict) -> dict[str, re.Pattern]:
    out = {}
    for k, v in overrides["masked_regions"].items():
        if isinstance(v, str) and not v.startswith("_note"):
            out[k] = re.compile(v)
    return out


def mask_lines(lines: list[str], masks: dict[str, re.Pattern]) -> list[bool]:
    """문체 검사에서 제외할 줄에 True. 코드펜스는 구간 전체를 덮는다."""
    masked = [False] * len(lines)
    in_fence = False
    for i, ln in enumerate(lines):
        if masks["code_fence"].match(ln):
            in_fence = not in_fence
            masked[i] = True
            continue
        if in_fence:
            masked[i] = True
            continue
        for name, pat in masks.items():
            if name == "code_fence":
                continue
            if pat.search(ln) if name == "signature_block" else pat.match(ln):
                masked[i] = True
                break
    return masked


# ── kordoc lint 연동 ─────────────────────────────────────────────────────
RE_LINT = re.compile(r"L(\d+)\s+\[(error|warning)\]\s+([A-Z_]+):")


def run_kordoc_lint(src: Path, munche: bool = True) -> list[dict]:
    cmd = ["npx", "-y", "kordoc@^4", "lint", str(src)] + (["--munche"] if munche else [])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", shell=(os.name == "nt"), timeout=180)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for line in (proc.stdout + proc.stderr).split("\n"):
        m = RE_LINT.search(line)
        if m:
            out.append({"line": int(m.group(1)), "kordoc_sev": m.group(2),
                        "rule": m.group(3), "raw": line.strip()})
    return out


def apply_overrides(items: list[dict], overrides: dict, masked: list[bool],
                    type_id: str, guard_ov: dict,
                    lines: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """kordoc finding 에 우리 심각도를 입힌다. (채택, 폐기) 를 돌려준다."""
    smap = overrides["severity_map"]
    errch = set(overrides["error_channel"]["rules"])
    excl = {k: re.compile(v["pattern"])
            for k, v in overrides.get("rule_line_exclusions", {}).items()
            if isinstance(v, dict)}
    kept, dropped = [], []
    for it in items:
        idx = it["line"] - 1
        rule = it["rule"]
        if lines and rule in excl and 0 <= idx < len(lines) and excl[rule].match(lines[idx]):
            dropped.append({**it, "why": "룰 예외 — 괄호 소제목 줄"})
            continue
        rec = smap.get(rule)
        ours = None
        if rec:
            ours = rec["ours"]
        if ours == "DROP":
            dropped.append({**it, "why": rec["reason"]})
            continue
        if 0 <= idx < len(masked) and masked[idx]:
            dropped.append({**it, "why": "마스킹 구간(표·인용·서명란·코드펜스)"})
            continue
        if ours == "TYPE_DEPENDENT":
            key = {"COUPLET": "couplet"}.get(rule, rule.lower())
            ours = guard_ov.get(key, "ERROR")
            if ours == "OFF":
                dropped.append({**it, "why": "유형 {} 에서 해제된 룰".format(type_id)})
                continue
        if ours in (None, "CONTEXT"):
            ours = "ERROR" if (rule in errch and it["kordoc_sev"] == "error") else "WARN"
        if ours == "ADVISORY":
            ours = "ADVISORY"
        kept.append({**it, "severity": ours})
    return kept, dropped


# ── 자체 규칙 ────────────────────────────────────────────────────────────
def check_own_rules(lines: list[str], masked: list[bool], guard_ov: dict,
                    final: bool, density: dict | None = None) -> tuple[list[dict], dict]:
    f: list[dict] = []
    m: dict = {}
    density = density or {}

    def on(rule: str, default: str) -> str:
        return guard_ov.get(rule, default)

    # Q3 서술형 종결
    sev = on("narrative_ending", "CRITICAL")
    narrative = []
    if sev != "OFF":
        for i, ln in enumerate(lines):
            if masked[i] or not ln.strip():
                continue
            s = re.sub(r"\*\*|`|\[AI\]", "", ln).strip()
            if re.match(r"^\s*[#|]", s):
                continue
            if NARRATIVE_END.search(s) and not NOMINAL_END.search(s):
                narrative.append(i + 1)
        for n in narrative[:20]:
            f.append(finding("Q3", sev, "Phase 5", "L{}".format(n),
                             "서술형 종결 — 개조식은 명사형으로 끝낸다",
                             "'~이다/~한다/~있다' → '~임/~함/~있음' 또는 명사 종결"))
    m["narrative_endings"] = len(narrative)

    # Q4 플레이스홀더
    ph = [(i + 1, x.group()) for i, ln in enumerate(lines)
          for x in re.finditer(r"\{\{[^}]*\}\}|○○○+|\bTODO\b|\bTBD\b", ln)]
    m["placeholders"] = len(ph)
    if final and ph:
        for n, tok in ph[:20]:
            f.append(finding("Q4", "CRITICAL", "Phase 5", "L{}".format(n),
                             "제출용(B)에 플레이스홀더 잔존: {}".format(tok),
                             "실제 데이터로 교체하거나 해당 항목을 삭제한다"))

    # Q5 [AI] 밀도 — 한 **항목**에 2회 이상이면 위반
    #
    # CORE RULE 3 의 단위는 "문단/항목"이다. 개조식 나열에서는 글머리표로 시작하는
    # 줄 하나하나가 항목이므로, 빈 줄로만 끊으면 항목 5개짜리 나열이 "한 문단 5회"로
    # 오탐된다(실측 확인). 글머리표가 나오면 거기서 항목을 끊는다.
    RE_ITEM_LEAD = re.compile(r"^\s*(?:>\s*)?(?:[□○◦❍\-·※*▸⇒]|\d+[.)]|[가-힣][.)])\s")
    cur, cur_start, ai_over = [], 1, []

    def _flush() -> None:
        if not cur:
            return
        cnt = sum(x.count("[AI]") for x in cur)
        if cnt >= 2:
            ai_over.append((cur_start, cnt))

    for i, ln in enumerate(lines + [""]):
        if not ln.strip():
            _flush()
            cur, cur_start = [], i + 2
            continue
        if RE_ITEM_LEAD.match(ln) and cur:
            _flush()
            cur, cur_start = [], i + 1
        if not cur:
            cur_start = i + 1
        cur.append(ln)
    _flush()
    for n, c in ai_over[:20]:
        f.append(finding("Q5", "MAJOR", "Phase 5", "L{}".format(n),
                         "[AI] 태그가 한 문단에 {}회 — 문단/항목 끝에 1회만".format(c),
                         "문장마다 붙이면 읽을 수 없다. 문단 끝 1회로 줄인다"))
    m["ai_tag_overuse_paragraphs"] = len(ai_over)

    # Q8 같은 depth 마커 혼용
    depth_marks: dict[int, set] = {}
    for i, ln in enumerate(lines):
        if masked[i]:
            continue
        mm = re.match(r"^(\s*)([□○\-·※])\s", ln)
        if mm:
            depth_marks.setdefault(len(mm.group(1)) // 2, set()).add(mm.group(2))
    mixed = {d: s for d, s in depth_marks.items() if len({x for x in s if x not in "※"}) > 1}
    for d, s in list(mixed.items())[:10]:
        f.append(finding("Q8", "MAJOR", "Phase 5", "depth {}".format(d),
                         "같은 깊이에 마커 2종 이상: {}".format(" ".join(sorted(s))),
                         "한 깊이에는 한 부호만. 장(章) 계열과 본문 계열의 병용은 정상이다"))
    m["mixed_depth_markers"] = len(mixed)

    # Q9 문장 길이 — 위계별로 상한이 다르다(references/14-content-density.md §2)
    #   ○ 29~67자 · - 30~62자 · ※ 35~63자 → 논리 단락 기준이므로 46자는 ○ 에만 건다.
    #   `-` 를 46자로 재면 규정대로 쓴 설명이 전부 위반이 된다(실측 중앙 40자, p75 62자).
    LIMIT = {"○": 46, "-": 75, "·": 46, "※": 75, "*": 75, "□": 46}
    sev46 = on("line_46chars", "MINOR")
    long46, long4line = [], []
    counted = 0
    for i, ln in enumerate(lines):
        if masked[i] or not ln.strip():
            continue
        mk = re.match(r"^\s*([□○\-·※*])\s", ln)
        limit = LIMIT.get(mk.group(1), 46) if mk else 46
        n = len(re.sub(r"^\s*[□○\-·※*]\s*", "", ln).strip())
        counted += 1
        if n > limit:
            long46.append(i + 1)
        if n > 46 * 4:
            long4line.append(i + 1)
    m["lines_over_46"] = len(long46)
    body = counted or 1
    ratio = len(long46) / body
    if sev46 != "OFF" and ratio > 0.10:
        f.append(finding("Q9", sev46, "Phase 5", "-",
                         "위계별 길이 상한 초과 {}/{} ({:.0%}) — 기준 10% (○·□ 46자 / - ※ * 75자)".format(
                             len(long46), body, ratio),
                         "'및'·쉼표에서 끊거나 근거·예시를 세부(-)로 내린다"))
    for n in long4line[:10]:
        f.append(finding("Q9b", "ERROR", "Phase 5", "L{}".format(n),
                         "한 문장 4줄 초과 — '예선 탈락' 기준", "반드시 쪼갠다"))

    # Q10 본문 이모지
    # ⚠️ ☞ ⇒ → ▸ ※ ◈ ◇ ▣ 등은 공문서 관용 기호이지 이모지가 아니다.
    #    특히 ☞ 는 S5(문제↔방안 명시 연결)에서 우리가 권장하는 기호다.
    emo = [i + 1 for i, ln in enumerate(lines)
           if not masked[i] and has_emoji(ln) and not ln.lstrip().startswith("|")]
    for n in emo[:10]:
        f.append(finding("Q10", "MAJOR", "Phase 5", "L{}".format(n),
                         "본문에 이모지 — 표 안 신호등만 허용", "제거한다"))
    m["body_emoji"] = len(emo)

    # Q17 * 와 ※ 병용
    # ⚠️ 예전에는 병용 자체를 ERROR 로 잡았으나 실측과 어긋난다.
    # 기획보고서 6건 실측(references/11-measured-patterns.md §5)에서 사례5는
    # ※ 10건·* 2건을 역할을 나눠 함께 쓴다 — * 는 정의·출처·산식, ※ 는 예외·전제·추정치.
    # 역할 구분은 기계가 판정할 수 없으므로 금지가 아니라 환기로 낮춘다.
    star = sum(1 for i, l in enumerate(lines) if not masked[i] and re.match(r"^\s*\*\s", l))
    ref = sum(1 for i, l in enumerate(lines) if not masked[i] and re.match(r"^\s*※\s", l))
    m["note_star"], m["note_ref"] = star, ref
    if star and ref:
        f.append(finding("Q17", "ADVISORY", "Phase 5", "-",
                         "각주 부호 병용 — '*' {}건, '※' {}건".format(star, ref),
                         "병용은 실측 관행이다. 다만 역할을 갈라 쓸 것 — "
                         "'*'=정의·출처·산식 / '※'=예외·전제·추정치·국제비교"))

    # Q18 같은 서술어 3회 이상 (문단 단위)
    para, dup = [], 0
    for ln in lines + [""]:
        if ln.strip():
            para.append(ln)
        elif para:
            tails = [t.group() for x in para
                     for t in [NOMINAL_END.search(x.strip())] if t]
            for t in set(tails):
                if tails.count(t) >= 3:
                    dup += 1
                    break
            para = []
    m["repeated_predicates"] = dup
    if dup:
        f.append(finding("Q18", "WARN", "Phase 5", "-",
                         "같은 서술어가 3회 이상 반복된 문단 {}개".format(dup),
                         "references/02-writing.md 서술어 사전에서 바꿔 쓴다"))

    # Q19 볼드론 — 조사·서술어가 볼드에 묶였는가
    bad_bold = []
    for i, ln in enumerate(lines):
        if masked[i]:
            continue
        for b in re.findall(r"\*\*([^*]{1,60})\*\*", ln):
            if NARRATIVE_END.search(b) or NOMINAL_END.search(b.strip()) or len(b) > 30:
                bad_bold.append(i + 1)
                break
    m["bold_violations"] = len(bad_bold)
    for n in bad_bold[:10]:
        f.append(finding("Q19", "WARN", "Phase 5", "L{}".format(n),
                         "볼드에 서술어·조사가 묶임 — 강조는 명사 키워드에만",
                         "핵심 명사만 남기고 볼드 범위를 줄인다"))

    # Q20 꼬리 줄 (줄 끝 1~3자) — 수평선·구분선·부호만 있는 줄은 제외
    # '끝.' 은 공문서 규정이 요구하는 표시이지 꼬리가 아니다(2025 개정 공문서 작성법)
    RE_RULE_LINE = re.compile(
        r"^\s*(?:-{3,}|\*{3,}|_{3,}|={3,}|[□○\-·※*⇒▸]\s*|끝\s*\.)$")
    tail = [i + 1 for i, ln in enumerate(lines)
            if not masked[i] and 0 < len(ln.strip()) <= 3
            and not re.match(r"^\s*[#|>]", ln) and not RE_RULE_LINE.match(ln)]
    m["orphan_tails"] = len(tail)
    if tail:
        f.append(finding("Q20", "WARN", "Phase 5", "L{}".format(tail[0]),
                         "0.2~0.3줄 꼬리 {}건".format(len(tail)), "앞 줄에 붙여 꼬리를 자른다"))

    # Q21 추상어·어려운 한자어
    vague = [(i + 1, w) for i, ln in enumerate(lines) if not masked[i]
             for w in VAGUE_WORDS if w in ln]
    hard = [(i + 1, w, HARD_WORDS[w]) for i, ln in enumerate(lines) if not masked[i]
            for w in HARD_WORDS if w in ln]
    m["vague_words"], m["hard_words"] = len(vague), len(hard)
    for n, w in vague[:8]:
        f.append(finding("Q21a", "WARN", "Phase 5", "L{}".format(n),
                         "추상어 '{}' — 구체 수치·사례로 내린다".format(w),
                         "'예를 들면?' 을 스스로 물어 치환한다"))
    for n, w, alt in hard[:8]:
        f.append(finding("Q21b", "WARN", "Phase 5", "L{}".format(n),
                         "어려운 한자어 '{}'".format(w), "'{}' 로 바꾼다".format(alt)))

    # ── Q22~Q24 내용 밀도 (references/14-content-density.md)
    # 이 세 가지가 없던 탓에 "○ 만 늘어선 라벨 나열" 원고가 게이트를 그대로 통과했다.
    # 압축을 재는 검사(Q9·Q18~Q21)는 다섯인데 깊이를 재는 검사가 0개였다.
    marker_lines: list[tuple[str, int]] = []          # (마커, 본문 글자수)
    for i, ln in enumerate(lines):
        if masked[i] or not ln.strip():
            continue
        mk = re.match(r"^\s*([□○\-·※*])\s", ln)
        if mk:
            body_txt = re.sub(r"^\s*[□○\-·※*]\s*", "", ln).strip()
            marker_lines.append((mk.group(1), len(body_txt)))

    n_circle = sum(1 for k, _ in marker_lines if k == "○")
    n_dash = sum(1 for k, _ in marker_lines if k == "-")
    m["circle_count"], m["dash_count"] = n_circle, n_dash

    # ⚠️ 완결서술 모드 판별 — 12-doc-type-matrix.md 의 문서군 분기.
    #    업무보고형은 `○` 자체가 50~90자 완결 서술이고 `-` 가 거의 없다. 이 경우 `-` 하한을
    #    그대로 걸면 정상 문서가 FAIL한다. 라벨 나열(짧은 ○ + `-` 없음)만 잡아야 한다.
    _cl = sorted(n for k, n in marker_lines if k == "○")
    circle_med = _cl[len(_cl) // 2] if _cl else 0
    narrative_mode = circle_med >= 50

    # Q22 `-` : `○` 비율 — 실측 풀드 0.88, 해결방안 장 1.0~1.55
    min_ratio = float(density.get("dash_per_circle_min", 0.5))
    tgt = density.get("dash_per_circle_target") or []
    tgt_txt = " · 목표 {:.1f}~{:.1f}".format(*tgt[:2]) if len(tgt) == 2 else ""
    if n_circle >= 8:
        dash_ratio = n_dash / n_circle
        m["dash_per_circle"] = round(dash_ratio, 2)
        m["narrative_mode"] = narrative_mode
        if narrative_mode and dash_ratio < min_ratio:
            f.append(finding(
                "Q22", "WARN", "Phase 5", "-",
                "`-` {}개 / `○` {}개 = {:.2f} (기준 {:.1f}) — 다만 `○` 중앙값 {}자로 "
                "완결서술형이라 경고로 낮춤".format(n_dash, n_circle, dash_ratio, min_ratio, circle_med),
                "업무보고형이면 이대로 두고, 기획·검토형이면 - 층을 채운다 (references/14 §1)"))
        elif dash_ratio < min_ratio:
            f.append(finding(
                "Q22", on("hierarchy_depth", "MAJOR"), "Phase 5", "-",
                "설명 층(-)이 얇다 — `-` {}개 / `○` {}개 = {:.2f} (기준 {:.1f}{})".format(
                    n_dash, n_circle, dash_ratio, min_ratio, tgt_txt),
                "각 ○ 아래 '왜 그런가(진단)' 또는 '어떻게 한다(방안)'를 - 로 편다. "
                "※ 에 든 설명 중 '없으면 이해 못 하는' 문장을 - 로 올린다 (references/14 §1·§5)"))

    # Q23 `○` 실질 길이 — 라벨 나열이면 중앙값이 급격히 짧아진다
    circle_lens = _cl
    if len(circle_lens) >= 8:
        med = circle_med
        m["circle_median_chars"] = med
        lo, hi = (density.get("circle_median_chars") or [29, 67])[:2]
        if med < 20:
            f.append(finding(
                "Q23", "MAJOR", "Phase 5", "-",
                "○ 중앙값 {}자 — 라벨 나열로 보임 (실측 정상 {}~{}자)".format(med, lo, hi),
                "○ 를 술어로 닫는 한 문장으로 쓴다. 조사를 지우고 명사만 남기지 않는다"))
        elif not lo <= med <= hi:
            f.append(finding(
                "Q23", "ADVISORY", "Phase 5", "-",
                "○ 중앙값 {}자 — 실측 밴드 {}~{}자 밖".format(med, lo, hi),
                "짧으면 술어를 붙이고, 길면 뒷부분을 - 로 내린다 (references/14 §2)"))

    # Q25 `-` 실질 길이 — 설명 층이 한 줄 부연으로 쪼그라들거나 반대로 문단이 되면 잡는다
    _dl = sorted(n for k, n in marker_lines if k == "-")
    if len(_dl) >= 8:
        dmed = _dl[len(_dl) // 2]
        m["dash_median_chars"] = dmed
        dlo, dhi = (density.get("dash_median_chars") or [30, 62])[:2]
        if not dlo <= dmed <= dhi:
            f.append(finding(
                "Q25", "ADVISORY", "Phase 5", "-",
                "- 중앙값 {}자 — 실측 밴드 {}~{}자 밖".format(dmed, dlo, dhi),
                "짧으면 인과를 마저 쓰고, 길면 한 - 에 두 가지를 담지 않았는지 본다 "
                "(references/14 §2·§3)"))

    # Q24 자식 없는 `○` 비율 — `-`·`*`·`※` 중 무엇도 딸리지 않은 ○
    if n_circle >= 8:
        childless, seen = 0, False
        idx = [i for i, ln in enumerate(lines) if not masked[i] and ln.strip()]
        for pos, i in enumerate(idx):
            if not re.match(r"^\s*○\s", lines[i]):
                continue
            seen = True
            nxt = lines[idx[pos + 1]] if pos + 1 < len(idx) else ""
            if not re.match(r"^\s*[\-·※*]\s", nxt):
                childless += 1
        if seen:
            rate = childless / n_circle
            m["childless_circle_rate"] = round(rate, 2)
            if rate > 0.60 and not narrative_mode:
                f.append(finding(
                    "Q24", on("hierarchy_depth", "MAJOR"), "Phase 5", "-",
                    "근거 없는 ○ 가 {}/{} ({:.0%}) — 기준 60%".format(
                        childless, n_circle, rate),
                    "실측상 ○ 의 절반 이상이 - 를 1~2개 거느린다. "
                    "각 ○ 에 'Why so?' 를 두 번 물어 답을 지면에 올린다"))

    return f, m


def check_gaepo(text: str, type_id: str) -> tuple[list[dict], list[str]]:
    """3대 깨포 중 기계로 잡히는 것만. 나머지는 report-auditor 로 넘긴다."""
    f, unver = [], []
    if type_id in ("01", "02", "10"):
        if not re.search(r"원\s*인", text):
            f.append(finding("S3", "MAJOR", "Phase 3", "Ⅱ장",
                             "원인분석 섹션이 없다 (깨포2)",
                             "문제 나열에 그치지 말고 '왜 생겼나'를 별도 항목으로 세운다"))
        if not re.search(r"→\s*☞|☞\s*(개선|방안)", text):
            f.append(finding("S5", "WARN", "Phase 5", "-",
                             "문제↔방안 명시 연결(→ ☞)이 없다",
                             "문제점 항목 끝에 '→ ☞ 개선방안 □-N' 을 붙인다"))
        eff = re.search(r"기대\s*효과(.{0,1200})", text, re.S)
        if eff and not re.search(r"[0-9]", eff.group(1)):
            f.append(finding("S7", "WARN", "Phase 5", "기대효과",
                             "기대효과에 숫자가 없다 — '제고' 같은 추상어 금지",
                             "절감액·건수·달성률 등 정량으로 쓴다"))
    if type_id == "10" and not re.search(r"\|[^|\n]*달성률", text):
        f.append(finding("S-10", "WARN", "Phase 5", "Ⅳ장",
                         "[지표|목표|실적|달성률] 표가 없다 — gap-closing 증명 실패",
                         "계획 대비 실적표를 넣는다"))
    unver.append("S2 시급성(깨포1) · S1 두괄식 · S10 결재 가능성 — 의미 판단이라 "
                 "report-auditor 서브에이전트가 확인해야 한다")
    return f, unver


def main() -> int:
    ap = argparse.ArgumentParser(description="원고 게이트")
    ap.add_argument("draft")
    ap.add_argument("--profile")
    ap.add_argument("--type", dest="type_id")
    ap.add_argument("--final", action="store_true", help="제출용 B 검사")
    ap.add_argument("--baseline", action="store_true", help="판정 없이 현황만")
    ap.add_argument("--no-kordoc", action="store_true")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    try:
        src = Path(args.draft)
        text = io.open(src, encoding="utf-8").read()
        lines = text.split("\n")

        prof = {}
        if args.profile:
            prof = load_json(Path(args.profile))
        type_id = args.type_id or prof.get("type_id") or "01"
        guard_ov = prof.get("guard_overrides", {})
        overrides = load_json(OVERRIDES_PATH)

        masks = build_masks(overrides)
        masked = mask_lines(lines, masks)

        findings, metrics = check_own_rules(lines, masked, guard_ov, args.final,
                                            prof.get("density", {}))
        gf, unver = check_gaepo(text, type_id)
        findings += gf

        kordoc_kept, kordoc_dropped = [], []
        if not args.no_kordoc:
            raw = run_kordoc_lint(src)
            kordoc_kept, kordoc_dropped = apply_overrides(
                    raw, overrides, masked, type_id, guard_ov, lines)
            for it in kordoc_kept:
                findings.append(finding(
                    "K:" + it["rule"], it["severity"], "Phase 5",
                    "L{}".format(it["line"]), it["raw"][:180], ""))
            metrics["kordoc_raw"] = len(raw)
            metrics["kordoc_kept"] = len(kordoc_kept)
            metrics["kordoc_dropped"] = len(kordoc_dropped)

        findings.sort(key=lambda x: (SEV_ORDER.get(x["severity"], 9), x["id"]))
        counts: dict[str, int] = {}
        for x in findings:
            counts[x["severity"]] = counts.get(x["severity"], 0) + 1

        if args.baseline:
            verdict, code = "BASELINE", 0
        elif any(x["severity"] in FAIL_SEV for x in findings):
            verdict, code = "FAIL", 1
        elif findings:
            verdict, code = "PASS-WITH-WARNINGS", 2
        else:
            verdict, code = "PASS", 0

        result = {"verdict": verdict, "type_id": type_id, "final": args.final,
                  "counts": counts, "findings": findings,
                  "metrics": metrics, "unverifiable": unver}
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if not args.json_only:
            w = sys.stderr
            print("\n[draft_guard] {}  유형 {}  →  {}".format(src.name, type_id, verdict), file=w)
            print("  등급별: " + (", ".join("{} {}".format(k, v) for k, v in counts.items())
                                or "없음"), file=w)
            if kordoc_dropped:
                print("  kordoc 오탐 제거 {}건 (마스킹·유형해제·구조오인)".format(
                    len(kordoc_dropped)), file=w)
            for x in findings[:25]:
                print("   [{:8}] {:6} {:10} {}".format(
                    x["severity"], x["id"], x["location"], x["message"][:88]), file=w)
            if len(findings) > 25:
                print("   … 외 {}건".format(len(findings) - 25), file=w)
            for u in unver:
                print("  ⓘ 확인 불가: " + u, file=w)
        return code
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        print("[draft_guard] 실행 오류: " + str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
