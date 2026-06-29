# 골든 템플릿 — 기관 서식을 기계친화 스켈레톤으로

`examples/정책과제-template.hwpx` 는 제주연구원(JRI) **정책과제 보고서 서식**을
`hwp-agent` 가 바로 쓰도록 가공한 "골든 템플릿"이다. 원본은 사실 빈 양식이 아니라
완성 예시 보고서(「제주 경제전망 2026」)였고, 그 본문을 걷어내 **재사용 가능한
스켈레톤**으로 만들었다.

출처: NAVERWORKS 공용드라이브 `0.서식(과제 관련)/보고서 서식(유형별)/정책과제_서식.hwpx`.
가공 스크립트(provenance): `scripts/build-golden-template.py`.

## 구성

| 섹션 | 처리 |
|------|------|
| 표지 (section0) | 텍스트 → `{{report_number}}` `{{title}}` `{{subtitle}}` `{{authors}}` |
| 목차 (section1) | 예시 TOC 제거 + `AI:INSTRUCTION` (한글에서 차례 자동 생성 안내) |
| 요약 양식 (section2) | **그대로 유지** — 이미 빈 양식(구분/주요내용 표·□체크박스·구조 안내) |
| 들어가며 (section3) | 예시 프로즈 제거 + `AI:INSTRUCTION` |
| 본문 (section4) | `AI:INSTRUCTION` + `{{body}}` 마커 + 중립화한 `{{table_template}}` 참조표(3행) |
| 참고문헌 (section5) | 예시 항목 제거(분류 헤더 유지) + `AI:INSTRUCTION` |
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
