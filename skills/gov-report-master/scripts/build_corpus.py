#!/usr/bin/env python3
"""
build_corpus.py — 레퍼런스 18건 PDF를 정규화 텍스트 코퍼스로 변환 (구축 1회)

`인재개발원전송용/` 의 실제 부처 보고서 18건을 kordoc으로 파싱하고
normalize.py 규칙으로 정리해 `assets/corpus/` 에 쌓는다.
동시에 파싱 품질(마커 회수 여부)을 판정해 scan-only 문서를 골라낸다.

사용법
------
  python build_corpus.py --src "D:/고수의 보고법/글쓰기/인재개발원전송용"
  python build_corpus.py --src ... --only 결과      # 유형 한 종만

출력 계약: exit 0 정상 / 3 실행 오류.  stdout 에 요약 JSON.
"""
from __future__ import annotations

import argparse
import io
import json
import re
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

CORPUS = HERE.parent / "assets" / "corpus"

TYPE_MAP = {"1": ("기획", "planning"), "2": ("상황", "situation"), "3": ("결과", "result")}
RE_CASE = re.compile(r"사례\s*([0-9]+)")
RE_ORG_YEAR = re.compile(r"_([^_()]+)\(([0-9]{4})\)\.pdf$")

# 파싱이 성공해도 마커가 하나도 안 잡히면 스캔본(텍스트층 없음)으로 본다.
MIN_MARKERS = 3


def classify(name: str) -> dict | None:
    t = name[0]
    if t not in TYPE_MAP:
        return None
    ko, en = TYPE_MAP[t]
    m = RE_CASE.search(name)
    if not m:
        return None
    case = int(m.group(1))
    om = RE_ORG_YEAR.search(name)
    org, year = (om.group(1), int(om.group(2))) if om else ("", 0)
    return {"type_ko": ko, "type_en": en, "case": case, "org": org, "year": year}


def lineage_of(org: str, year: int) -> str:
    if "비서관실" in org:
        return "blue_house_2000s"
    if any(k in org for k in ("광역시", "특별시", "도청", "구청", "시청", "군청")):
        return "local_gian"
    return "central_2015plus"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--only", help="기획|상황|결과 중 하나만")
    ap.add_argument("--out", default=str(CORPUS))
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_aliases()

    entries, failures = [], []
    pdfs = sorted(p for p in src.glob("*.pdf") if "교안" not in p.name)

    for pdf in pdfs:
        meta = classify(pdf.name)
        if not meta:
            failures.append({"file": pdf.name, "reason": "파일명 규칙 불일치"})
            continue
        if args.only and meta["type_ko"] != args.only:
            continue

        slug = "{}-{:02d}".format(meta["type_ko"], meta["case"])
        try:
            raw = kordoc_parse(pdf)
            text, report = normalize_text(raw, cfg)
        except Exception as e:  # noqa: BLE001
            failures.append({"file": pdf.name, "slug": slug, "reason": str(e)[:200]})
            print("  ✗ {}  {}".format(slug, str(e)[:110]), file=sys.stderr)
            continue

        after = report["after"]
        markers = sum(after["marker_counts"].values())
        scan_only = markers < MIN_MARKERS

        header = (
            "<!-- 출처: {}\n"
            "     유형: {} / 사례 {} / {} ({})\n"
            "     계열: {}\n"
            "     생성: build_corpus.py — kordoc 파싱 + normalize.py 정규화\n"
            "     ⚠️ 저장소 내부 참조용. 원문 재배포 금지. -->\n\n"
        ).format(pdf.name, meta["type_ko"], meta["case"], meta["org"], meta["year"],
                 lineage_of(meta["org"], meta["year"]))

        dest = out / (slug + ".md")
        io.open(dest, "w", encoding="utf-8").write(header + text)

        entries.append({
            "slug": slug,
            "source_pdf": pdf.name,
            "type_ko": meta["type_ko"], "type_en": meta["type_en"],
            "case": meta["case"], "org": meta["org"], "year": meta["year"],
            "lineage": lineage_of(meta["org"], meta["year"]),
            "corpus_file": "assets/corpus/" + slug + ".md",
            "scan_only": scan_only,
            "usable_for_profile": not scan_only,
            "stats": {
                "lines_nonempty": after["lines_nonempty"],
                "marker_counts": after["marker_counts"],
                "marker_total": markers,
                "tables": after["tables"],
                "headings": after["headings"],
                "char_total": after["char_total"],
                "char_median": after["char_median"],
            },
            "normalize_ops": report["operations"],
        })
        flag = "  ⚠ 스캔본(프로파일 제외)" if scan_only else ""
        print("  ✓ {}  {:>4}줄  마커 {}{}".format(
            slug, after["lines_nonempty"], after["marker_counts"], flag), file=sys.stderr)

    summary = {
        "generated_by": "build_corpus.py",
        "source_dir": str(src),
        "total": len(entries),
        "usable": sum(1 for e in entries if e["usable_for_profile"]),
        "scan_only": [e["slug"] for e in entries if e["scan_only"]],
        "failures": failures,
        "entries": entries,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 0


if __name__ == "__main__":
    sys.exit(main())
