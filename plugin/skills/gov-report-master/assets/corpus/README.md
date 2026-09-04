# assets/corpus — 대표 원문 3건 (공개 저장소에는 없음)

이 폴더에는 유형별 대표 보고서 **본문 전문(정규화본)** 이 들어갑니다. 카드(`assets/samples/`)가
목차 구조만 주는 데 비해, 여기는 **실제 문장·마커 밀도·어투**를 줍니다.

```
대표-기획_*.md    정책검토(①)·계획수립(②) 이 참조
대표-상황_*.md    상황(③) 이 참조
대표-결과_*.md    결과보고(⑩) 이 참조
```

**원문은 이 저장소에 포함하지 않습니다.** 부처 보고서 원문이라 재배포하지 않습니다.
`assets/reference-index.json` 과 `SKILL.md` 의 경로는 그대로 두었으니, 파일을 채우면
바로 참조됩니다. 없으면 스킬은 카드(`assets/samples/`)와 프로파일 실측값만으로 진행합니다.

## 직접 채우는 법

손에 있는 보고서 PDF로 같은 형식의 정규화본을 만들 수 있습니다.

```bash
cd plugin/skills/gov-report-master
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
python scripts/build_corpus.py <보고서>.pdf -o assets/corpus/
```

파일명은 `대표-{기획|상황|결과}_{제목}_{기관}{연도}.md` 규칙을 따르면
`reference-index.json` 을 고치지 않고도 잡힙니다.

소속 기관 문서로 채우는 편이 낫습니다 — 우리 결재선의 어투가 그대로 반영됩니다.
