#!/usr/bin/env python3
"""
extract_profile.py — 레퍼런스 실측 → 유형별 프로파일 생성 (구축 1회)

설계 원칙
---------
* **통계는 16건 전부에서 재고, 플러그인에는 대표 3건만 싣는다.**
  원본 PDF는 저장소에 그대로 두고 여기서 직접 파싱한다(복제하지 않음).
* **계열(lineage)로 층화한다.** 2006~2022년 17년 폭에 서식 세대가 셋이라
  중앙값을 그냥 내면 "어느 부처 것도 아닌 문서"가 나온다.
      central_2015plus  (기본값)  · local_gian · blue_house_2000s(참고용)
* **표본이 적으므로 중앙값과 함께 min/max를 실어 게이트를 범위로 쓴다.**
* **실측이 없는 유형은 파생값으로 두되 `measured: false` 를 명시**한다.
  실측인 척하는 숫자를 넣지 않는다.

사용법
------
  python extract_profile.py --src "D:/고수의 보고법/글쓰기/인재개발원전송용"
  python extract_profile.py --src ... --dry-run      # 파일 안 쓰고 통계만

출력 계약: exit 0 정상 / 3 실행 오류. stdout 에 요약 JSON.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import statistics as st
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
sys.path.insert(0, str(HERE))

from normalize import kordoc_parse, load_aliases, normalize_text  # noqa: E402

PROFILES = HERE.parent / "profiles"
MIN_MARKERS = 3

# 유형 → (이름, 어느 레퍼런스 군에서 실측하는가, 게이트 방향, 대표 지표)
TYPES = [
    ("01", "policy-review",        "정책검토",      "기획", "both", "원인 섹션의 독립 존재 (Ⅱ장 하위 '원인' 헤딩 ≥1)"),
    ("02", "plan-establishment",   "계획수립",      "기획", "min",  "기본방향(Why) 블록이 맨 앞에 있는가"),
    ("03", "situation",            "상황",          "상황", "max",  "시각 표기 밀도 (HH:MM 또는 (M.D) 출현 수)"),
    ("04", "summary-onepager",     "개요정리(1쪽)",  None,   "max",  "총 줄 수 ≤ 37 (1p 실효 상한)"),
    ("05", "meeting-reference",    "회의참고",      None,   "min",  "쟁점 블록 반복 수"),
    ("06", "external-report",      "보고자료",      None,   "min",  "차별 키워드 라벨 수 + 문단 5줄 이내"),
    ("07", "external-consultation","협의자료",      None,   "max",  "(우리 기관)/(타 기관) 라벨 쌍 존재"),
    ("08", "external-explanation", "설명자료(보도)", None,   "min",  "Lead 1문장 + Quote 실명"),
    ("09", "external-speech",      "말씀자료",      None,   "max",  "키워드 3개 초과 금지 + 세부 나열 0"),
    ("10", "result-report",        "결과보고",      "결과", "min",  "[지표|목표|실적|달성률] 표 존재"),
]

# 실측 없는 유형은 어느 유형에서 파생할지
DERIVE_FROM = {"04": "01", "05": "01", "06": "01", "07": "03", "08": "10", "09": "03"}

KORDOC_PRESET = {
    "01": ("개조식", ["--org", "--approval"]),
    "02": ("개조식", ["--org", "--approval"]),
    "03": ("보고서", ["--no-cover", "--no-toc", "--no-page-numbers"]),
    "04": ("보고서", ["--no-cover", "--no-toc", "--no-page-numbers"]),
    "05": ("보고서", ["--no-cover", "--no-toc", "--no-page-numbers"]),
    "06": ("보고서", ["--no-cover", "--no-toc"]),
    "07": ("보고서", ["--no-cover", "--no-toc"]),
    "08": ("보도자료", ["--press-head", "--press-sub"]),
    "09": ("보고서", ["--no-cover", "--no-toc", "--no-page-numbers"]),
    "10": ("개조식", ["--org", "--approval"]),
}

# 유형별로 꺼야 하는 문체 룰 (references/09-evaluation.md 매트릭스)
GUARD_OVERRIDES = {
    "03": {"narrative_ending": "WARN", "marker_min": "OFF"},
    "04": {"line_46chars": "ERROR", "marker_min": "OFF"},
    "08": {"narrative_ending": "OFF", "couplet": "WARN", "question_mark": "WARN", "marker_min": "OFF"},
    "09": {"narrative_ending": "OFF", "couplet": "OFF", "question_mark": "OFF",
           "line_46chars": "OFF", "marker_min": "OFF"},
}

RE_CASE = re.compile(r"사례\s*([0-9]+)")
RE_ORG_YEAR = re.compile(r"_([^_()]+)\(([0-9]{4})\)\.pdf$")
RE_TIME = re.compile(r"\b[0-9]{1,2}:[0-9]{2}\b|\([0-9]{1,2}\.[0-9]{1,2}\.?\)")
RE_PAREN_LABEL = re.compile(r"^\s*[□○\-·]\s*\(([^)]{2,14})\)")
RE_ATTACH = re.compile(r"^\s*(?:붙\s*임|별\s*첨|【\s*별첨)")
RE_END_MARK = re.compile(r"끝\s*\.")
RE_NOTE_STAR = re.compile(r"^\s*\*\s")
RE_NOTE_REF = re.compile(r"^\s*※\s")


def lineage_of(org: str) -> str:
    if "비서관실" in org:
        return "blue_house_2000s"
    if any(k in org for k in ("광역시", "특별시", "도청", "구청", "시청", "군청")):
        return "local_gian"
    return "central_2015plus"


def measure(text: str) -> dict:
    """정규화된 마크다운에서 한 건의 실측치를 뽑는다."""
    lines = text.split("\n")
    body = [l for l in lines if l.strip() and not l.lstrip().startswith("<!--")]
    lens = [len(l.strip()) for l in body]

    lead = {}
    for l in lines:
        m = re.match(r"^\s*([^\s])\s", l)
        if m and m.group(1) in "□○-·※*⇒▸":
            lead[m.group(1)] = lead.get(m.group(1), 0) + 1

    return {
        "lines_nonempty": len(body),
        "marker_counts": lead,
        "marker_total": sum(lead.values()),
        "table_rows": sum(1 for l in lines if l.lstrip().startswith("|")),
        "headings": sum(1 for l in lines if re.match(r"^#{1,6}\s", l)),
        "char_total": sum(lens),
        "char_median": int(st.median(lens)) if lens else 0,
        "lines_over_46": sum(1 for n in lens if n > 46),
        "time_marks": len(RE_TIME.findall(text)),
        "paren_labels": sum(1 for l in lines if RE_PAREN_LABEL.match(l)),
        "has_attachment": any(RE_ATTACH.match(l) for l in lines),
        "has_end_mark": bool(RE_END_MARK.search(text)),
        "note_star": sum(1 for l in lines if RE_NOTE_STAR.match(l)),
        "note_ref": sum(1 for l in lines if RE_NOTE_REF.match(l)),
    }


def agg(values: list[dict], key: str) -> dict | None:
    """[{k:v}] → {median,min,max,n}. 값이 없으면 None."""
    xs = [v[key] for v in values if isinstance(v.get(key), (int, float))]
    if not xs:
        return None
    return {"median": int(st.median(xs)), "min": min(xs), "max": max(xs), "n": len(xs)}


# 부속 표(법령용어 목록·명단 등)가 본문의 몇 배로 붙은 문서는 밀도 기준을 망친다.
# 실측 예: 상황-02(법제처 2019)는 표행 1,598 · 1,692줄 — 상황보고 중앙값을 917줄로 밀어올린다.
OUTLIER_TABLE_ROWS_ABS = 100
OUTLIER_TABLE_ROWS_RATIO = 5


def mark_outliers(samples: list[dict]) -> list[dict]:
    """그룹(유형군) 안에서 부속표 덤프 문서를 골라 밀도 집계에서 뺀다.

    판정: 표행 수가 그룹 중앙값의 5배를 넘고, 절대값으로도 100행 이상.
    제외해도 sources 목록에는 남겨 왜 뺐는지 보이게 한다.
    """
    if len(samples) < 3:
        for s in samples:
            s["_outlier"] = False
        return samples
    med = st.median([s["table_rows"] for s in samples]) or 1
    for s in samples:
        s["_outlier"] = (
            s["table_rows"] >= OUTLIER_TABLE_ROWS_ABS
            and s["table_rows"] >= OUTLIER_TABLE_ROWS_RATIO * med
        )
    return samples


def agg_markers(values: list[dict]) -> dict:
    keys = set()
    for v in values:
        keys |= set(v["marker_counts"])
    out = {}
    for k in sorted(keys):
        xs = [v["marker_counts"].get(k, 0) for v in values]
        out[k] = {"median": int(st.median(xs)), "min": min(xs), "max": max(xs)}
    return out


def build_profile(
    tid: str, slug: str, name: str, group: str | None, direction: str,
    primary: str, samples_by_lineage: dict[str, list[dict]],
    derived_from: str | None = None,
) -> dict:
    measured = bool(samples_by_lineage)
    prof = {
        "schema_version": "1.0",
        "type_id": tid,
        "type_name": name,
        "reference_group": group,
        "measured": measured,
        "binding": "soft",
        "_binding_note": "서식(HWPX)이 첨부되면 analyze_form.py 가 binding:'hard' 프로파일을 따로 만들어 이 값을 덮는다. hard 는 기획을 앞에서 구속하고, soft 는 사후 대조만 한다.",
        "gate_direction": direction,
        "primary_metric": primary,
        "kordoc": {"preset": KORDOC_PRESET[tid][0], "options": KORDOC_PRESET[tid][1]},
        "guard_overrides": GUARD_OVERRIDES.get(tid, {}),
    }
    if not measured:
        prof["derived_from"] = derived_from
        prof["_derived_note"] = (
            "이 유형에는 전용 레퍼런스가 없다. {} 프로파일에서 파생한 참고값이며 "
            "실측치가 아니다. 게이트는 방향({})만 적용하고 절대값으로 반려하지 않는다."
        ).format(derived_from, direction)
        return prof

    prof["lineages"] = {}
    for lin, all_samples in samples_by_lineage.items():
        samples = [s for s in all_samples if not s.get("_outlier")]
        excluded = [s["_slug"] for s in all_samples if s.get("_outlier")]
        if not samples:                      # 전부 이상치면 원본을 그대로 쓰되 표시
            samples, excluded = all_samples, []
        prof["lineages"][lin] = {
            "n": len(samples),
            "sources": [s["_slug"] for s in samples],
            "excluded_outliers": excluded,
            "density": {
                "lines_nonempty": agg(samples, "lines_nonempty"),
                "char_total": agg(samples, "char_total"),
                "char_median": agg(samples, "char_median"),
                "table_rows": agg(samples, "table_rows"),
                "headings": agg(samples, "headings"),
                "marker_total": agg(samples, "marker_total"),
                "marker_counts": agg_markers(samples),
            },
            "layout_signature": {
                "paren_labels": agg(samples, "paren_labels"),
                "time_marks": agg(samples, "time_marks"),
                "note_star": agg(samples, "note_star"),
                "note_ref": agg(samples, "note_ref"),
                "attachment_ratio": round(
                    sum(1 for s in samples if s["has_attachment"]) / len(samples), 2),
                "end_mark_ratio": round(
                    sum(1 for s in samples if s["has_end_mark"]) / len(samples), 2),
            },
            "quality": {"lines_over_46": agg(samples, "lines_over_46")},
        }
    prof["default_lineage"] = (
        "central_2015plus" if "central_2015plus" in prof["lineages"]
        else sorted(prof["lineages"])[0]
    )
    prof["_outlier_note"] = (
        "excluded_outliers 는 부속 표(법령용어 목록·명단 등)가 본문의 몇 배로 붙어 "
        "밀도 기준을 왜곡하는 문서다. 표행 수가 그룹 중앙값의 5배를 넘고 100행 이상이면 "
        "집계에서 뺀다. 문서 자체가 나쁘다는 뜻이 아니라 '평균적 분량'의 표본이 아니라는 뜻이다."
    )
    prof["_lineage_note"] = (
        "blue_house_2000s('06~'07 청와대 비서관실)는 「◇ 머리글 + <요약> 선행」·러닝헤더 "
        "서식으로 현행 표기규정과 상충한다. 참고용으로만 두고 기본 선택 대상에서 뺀다."
    )
    return prof


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="원본 PDF 디렉터리")
    ap.add_argument("--out", default=str(PROFILES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        cfg = load_aliases()
        src = Path(args.src)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)

        by_group: dict[str, list] = {"기획": [], "상황": [], "결과": []}
        skipped = []

        for pdf in sorted(p for p in src.glob("*.pdf") if "교안" not in p.name):
            t = pdf.name[0]
            group = {"1": "기획", "2": "상황", "3": "결과"}.get(t)
            if not group:
                continue
            cm, om = RE_CASE.search(pdf.name), RE_ORG_YEAR.search(pdf.name)
            case = int(cm.group(1)) if cm else 0
            org, year = (om.group(1), int(om.group(2))) if om else ("", 0)
            slug = "{}-{:02d}".format(group, case)

            try:
                text, _ = normalize_text(kordoc_parse(pdf), cfg)
            except Exception as e:  # noqa: BLE001
                skipped.append({"slug": slug, "reason": "파싱 실패: " + str(e)[:120]})
                continue

            m = measure(text)
            if m["marker_total"] < MIN_MARKERS:
                skipped.append({"slug": slug, "reason": "스캔본(마커 회수 실패)"})
                print("  ⚠ {} 제외 — 스캔본".format(slug), file=sys.stderr)
                continue

            m["_slug"] = slug
            m["_lineage"] = lineage_of(org)
            m["_org"], m["_year"] = org, year
            by_group[group].append(m)
            print("  · {} [{}] {}줄 마커{} 표행{}".format(
                slug, m["_lineage"], m["lines_nonempty"], m["marker_total"], m["table_rows"]),
                file=sys.stderr)

        written = []
        for tid, slug, name, group, direction, primary in TYPES:
            by_lin = {}
            if group:
                for s in mark_outliers(by_group[group]):
                    by_lin.setdefault(s["_lineage"], []).append(s)
            prof = build_profile(tid, slug, name, group, direction, primary,
                                 by_lin, DERIVE_FROM.get(tid))
            dest = out / "{}-{}.profile.json".format(tid, slug)
            if not args.dry_run:
                io.open(dest, "w", encoding="utf-8").write(
                    json.dumps(prof, ensure_ascii=False, indent=2))
            written.append({
                "type": tid + " " + name,
                "file": dest.name,
                "measured": prof["measured"],
                "lineages": {k: v["n"] for k, v in prof.get("lineages", {}).items()},
            })

        print(json.dumps({
            "generated_by": "extract_profile.py",
            "measured_from": str(src),
            "samples_used": {g: len(v) for g, v in by_group.items()},
            "skipped": skipped,
            "profiles": written,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "ERROR", "message": str(e)}, ensure_ascii=False))
        print("[extract_profile] 실행 오류: " + str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
