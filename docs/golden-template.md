# 골든 템플릿 — 기관 서식을 기계친화 스켈레톤으로

`examples/정책과제-template.hwpx` 는 제주연구원(JRI) **정책과제 보고서 서식**을
`hwp-agent` 가 바로 쓰도록 가공한 "골든 템플릿"이다. 원본은 사실 빈 양식이 아니라
완성 예시 보고서였고, 그 본문을 걷어내 **재사용 가능한
스켈레톤**으로 만들었다.

출처: NAVERWORKS 공용드라이브 `0.서식(과제 관련)/보고서 서식(유형별)/정책과제_서식.hwpx`.
가공 스크립트(provenance): `scripts/build-golden-template.py`.

## 구성

| 섹션 | 처리 |
|------|------|
| 표지 (section0) | 텍스트 → `{{report_number}}` `{{title}}` `{{subtitle}}` `{{authors}}` |
| 목차 (section1) | 중립 샘플 차례·표 차례·그림 차례(`Ⅰ. 장 제목`…) + 한글 차례 스타일 — `build`·`write`가 본문 제목·캡션으로 다시 쓴다(`scripts/add-golden-toc.py`, #33·#132, docs/toc.md) |
| 요약 양식 (section2) | 빈 양식(구분/주요내용 표·□체크박스) 유지 + 끝에 `{{summary}}` 마커. 표 뒤 견본 골격(`Ⅰ. 장 제목`…)·견본 칸 문구는 제거(`scripts/clean-golden-parts.py`) |
| 들어가며 (section3) | 예시 프로즈·빈 글머리 문단 제거 + `AI:INSTRUCTION` + `{{intro}}` 마커 |
| 본문 (section4) | `AI:INSTRUCTION` + `{{body}}` 마커 + 중립화한 `{{table_template}}` 참조표(3행) |
| 참고문헌 (section5) | 예시 항목·정적 분류 헤더 제거 + `AI:INSTRUCTION` + `{{references}}` 마커. 분류는 md 에 `**해외 문헌**` 줄로 쓰고, 원래 서식은 스타일 `참고문헌 분류`(문단 24·글자 27)·`참고문헌`(문단 29, 내어쓰기)로 남김 |
| 판권지 (section6) | 텍스트 → `{{lead_researcher}}` `{{co_researcher}}` `{{pub_date}}` `{{publisher}}` `{{isbn}}` 등 |

`normalize` 가 선언한 사다리: HEADING `로마자(Ⅰ.)→1.→1)`, BULLET `￭→⦁→-→-`.
새 `AI:INSTRUCTION` 스타일(id 19)이 header 에 추가된다.

`hwp-agent check` → `structured`, `✓ no issues`.

## 쓰는 법 (본문 + 메타데이터)

`write` 는 본문만, 표지·판권 슬롯은 `form fill` 이 채운다. 둘은 독립이라 순서 무관:

```bash
# 1) 마크다운 본문 채우기 (AI:INSTRUCTION 자동 제거, {{body}} 소비)
hwp-agent write content.md --template examples/정책과제-template.hwpx -o draft.hwpx
# 2) 표지/판권 메타데이터 채우기
hwp-agent form fill draft.hwpx --map meta.json -o final.hwpx
```

`meta.json` 예: `{"title":"…","subtitle":": …","authors":"…","report_number":"2026-07", …}`.

제목 번호(Ⅰ./1./1))는 **리터럴**이다(자동번호 아님) — 마크다운에 직접 쓴다
(`# Ⅰ. 서론`, `## 1. 추진 배경`, `### 1) 세부 과제`). 자세한 규약은 이 서식의
`AI:INSTRUCTION` 문단(`hwp-agent instructions`)에 들어 있다.

## 이미지: 무엇이 남고 무엇이 빠지나

기관 서식은 표지 아트·페이지 배경 등 **디자인 이미지**를 품는다. 골든 템플릿은
이것들을 **유지**하고, 예시 본문에 딸렸던 **차트/그림만 제거**한다.

- **유지(서식 디자인):** 표지(section0), 판권(section6), 마스터페이지가 참조하는 이미지.
- **제거(예시 잔재):** 본문 그림처럼 더는 어디서도 참조되지 않는 **고아 이미지**.

확인법 — 모든 섹션·마스터페이지에서 `binaryItemIDRef` 를 모아 참조 집합을 만들고,
`BinData/` 에 있으나 참조되지 않는 파일이 고아(=예시 잔재)다. 눈으로도 가른다:
차트/도표면 예시, 로고·표지 아트면 디자인. (정책과제의 경우 `image9` = IMF WEO
실효관세율 차트 1건이 고아였고 제거됨. 표지/배경 이미지가 무거워 결과 파일은
여전히 ~5MB이며, 이는 서식 고유의 디자인 자산이다.) 이 로직은
`scripts/build-golden-template.py` 가 자동 수행한다.

## 남은 일

- **나머지 3종(기반·센터·전략과제):** 스타일 시스템은 동일하나 예시 내용이 달라
  표지/판권 문자열 매칭을 손봐야 한다. 정책과제로 한글 육안 검증(보안경고·레이아웃)
  후 전개.
- **기능화(중요):** 서식은 개정될 때마다 공용드라이브 최신본으로 갱신되지만, 기계
  친화성은 매번 다시 깨진다. 따라서 "원본 서식 → 기계친화 스켈레톤" 변환을
  `hwp-agent` **명령으로 승격**해야 한다(normalize + 스켈레톤화 + 슬롯/지침/표참조 +
  고아 이미지 정리). 현재 `scripts/build-golden-template.py` 가 그 프로토타입이다.
  `check` 를 진단에서 "수정 체크리스트/기계친화 점수"로 키우는 작업과 함께 간다
  (`docs/author-backlog.md`). 양식 미배정 기본값은 [default-template.md](default-template.md) 참조.

## 요약·들어가며·참고문헌 채우기 (#94, #106-#108)

`hwp-agent build` 의 `parts` 키는 템플릿 마커와 1:1 이다: `summary`→`{{summary}}`,
`intro`→`{{intro}}`, `body`→`{{body}}`, `references`→`{{references}}`. 마커는
`scripts/add-golden-markers.py` 가 골든 템플릿에 멱등으로 넣는다.

`write` 는 원고 한 파일에서 번호 없는 H1 `# 요약` · `# 들어가며` · `# 참고문헌`(정확히 그
제목) 구역을 떼어 각 마커로 보낸다. 그 H1 자체는 버린다(구역에 제목이 있음). 템플릿에
해당 마커가 없거나 정책이슈브리프 템플릿이면 예전처럼 본문 한 곳에 쓴다.

- **요약 양식 표**(#106): 요약 구역의 `| 구분 | 주요내용 |` md 표는 새 표가 되지 않고
  양식 칸을 행 이름(연구 목표·연구 방법·연구결과·예상 정책 활용, 띄어쓰기·괄호 무시)으로
  채운다. 칸 안 줄은 `<br>` 로 나누고 `  - `(들여쓴 글머리)는 2단 모양을 쓴다.
  `정책 활용 분야` 행은 적힌 항목(쉼표 구분)의 □를 ■로 바꾸고 `기타(내용)` 은 괄호에
  채운다. 표 밖의 요약 본문은 마커 자리에 들어간다.
- **참고문헌**(#107, #108): 혼자 굵은 줄(`**해외 문헌**`)이나 소제목은 `참고문헌 분류`
  스타일(앞뒤 빈 줄), 나머지 항목은 `참고문헌` 스타일(paraPr 29, 내어쓰기)을 쓴다.
  두 스타일이 없는 템플릿은 쓴 그대로 둔다.
- **빈 구역**: 원고에 없는 구역은 제목과 빈 양식 표만 남는다(견본 문구 없음).
