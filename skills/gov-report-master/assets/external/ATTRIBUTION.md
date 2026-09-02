# 외부 자산 출처 · 라이선스

이 디렉터리의 파일은 **외부 저장소에서 가져온 원문 그대로**다. 각 하위 폴더에 원 저장소의 `LICENSE`를 함께 둔다. 수정하지 않으며, 우리 규칙과 충돌하는 부분은 이 문서에 적어 둔다.

---

## `k-gov-skills/` — MIT

- 원 저장소: <https://github.com/mouseco/k-gov-skills>
- 라이선스: MIT (Copyright (c) 2026 mouseco) — `k-gov-skills/LICENSE`
- 가져온 파일

  | 파일 | 원 경로 | 우리 쓰임 |
  |---|---|---|
  | `document-types.md` | `skills/official-report-skillset/references/` | 보고서 유형별 권장 구조 — 우리 10종 체계와 대조 참고 |
  | `quality-checklist.md` | `skills/official-report-skillset/references/` | 결재문서 품질 점검표 — `checklists/type-checklists.md` 보완 |
  | `public-report-writing-rules.md` | `skills/hwpx-mouseco/references/` | 문단 위계·개조식 문체·강조 규칙 — `references/07-form-fidelity.md` 근거 |

- **우리 체계와 다른 점 (그대로 따르지 않는다)**
  - 유형을 6종(검토·계획·결과·결합형·회의결과·간단)으로 본다. **우리는 10종이 정본**이며 이 문서는 참고로만 쓴다.
  - "상위 목차를 5개보다 많이 벌리지 않는다"는 이 저장소의 원칙이다. 우리 `templates/01`은 Ⅰ~Ⅴ 5장 구조라 대체로 부합하나, 유형별 뼈대가 우선한다.
  - 밀도 하한("한 장이 비어 보이면 실패")은 원페이퍼 전제 규칙이다. **우리 ④개요정리·⑨말씀자료에는 반대로 상한을 적용**한다 — `references/09-evaluation.md` §유형별 게이트 방향 참조.

---

## `public-doc-to-hwpx/` — MIT

- 원 저장소: <https://github.com/Kminer2053/public-doc-to-hwpx>
- 라이선스: MIT (Copyright (c) 2026 public-doc-to-hwpx contributors) — `public-doc-to-hwpx/LICENSE`
- 가져온 파일

  | 파일 | 우리 쓰임 |
  |---|---|
  | `writing-principles.md` | 업무용 글쓰기 3요소(구성·양식·표현), Fishing→Reasoning→Message, 서술식→개조식 전환 예시 |
  | `format-selection.md` | 양식 자동 추천 결정트리(1p/풀버전/시행문/이메일) — 우리 유형 판정의 분량 축 보완 |
  | `layout-rules.md` | **한 줄 35~45자·46자↑ 분리·1p 실효 37줄/풀버전 38~42줄·페이지 걸침 방지 단위** — `draft_guard.py` Q9와 `density_guard.py` 쪽수 추정의 근거 |

- **주의**: 이 저장소의 양식 4종(1p·풀버전·시행문·이메일)은 우리 10종과 축이 다르다. **분량·레이아웃 수치만 가져다 쓰고 유형 분류는 우리 것을 쓴다.**

---

## 가져오지 않은 것 — 라이선스 없음

아래 저장소는 우수한 자산을 갖고 있으나 **라이선스 파일이 없어(All rights reserved) 파일을 복제할 수 없다.** 아이디어·방법만 참고해 우리가 직접 구현했다.

| 저장소 | 참고한 아이디어 | 우리 구현 |
|---|---|---|
| [jkf87/hwpx-skill](https://github.com/jkf87/hwpx-skill) | 실무 보고서 실측 기반 개조식 문체 검문표, "형식의 세 층"(규정/기관/내용/글쓴이) 모델 | `references/07-form-fidelity.md` 자체 서술 + kordoc `lint --munche`에 위임 |
| [Canine89/hwpxskill](https://github.com/Canine89/hwpxskill) | 레퍼런스 대비 페이지 드리프트 검사, placeholder·원문 잔재 스캔 | `scripts/density_guard.py` · `scripts/draft_guard.py` 자체 구현 |
| [Canine89/gonggong_hwpxskills](https://github.com/Canine89/gonggong_hwpxskills) | 프롬프트+assertion 형식의 스킬 회귀 테스트셋 | `evals/evals.json` 자체 작성 |

> 개조식 문체 통계는 kordoc v4의 `lint --munche` 가 이미 흡수해 제공한다(kordoc README가 jkf87 실측을 출처로 명시). 우리는 **kordoc CLI를 호출**할 뿐 해당 저장소의 파일을 담지 않는다.

---

## 엔진 의존

| 도구 | 라이선스 | 관계 |
|---|---|---|
| [chrisryugj/kordoc](https://github.com/chrisryugj/kordoc) v4 | MIT | HWPX 생성·검증·조판·서식프로필·표기법/문체 검수를 전담. `npx -y kordoc@^4` 로 **호출만** 하며 소스를 담지 않는다 |

## 방법론 출처

박종필 『고수의 보고법』 · 백승권 『한번에 통과되는 기획서·보고서』 · 박종덕 『2025 개정 공문서 작성법』 · 국가공무원인재개발원 교안 · 행정안전부 「행정업무운영 편람」 · 국립국어원 표준.

**상용 도서의 원문은 이 플러그인에 담지 않는다.** 프레임워크·원칙을 실무 적용 관점에서 재구성해 반영했다.
