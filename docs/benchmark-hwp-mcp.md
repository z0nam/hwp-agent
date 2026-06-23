# HWP/HWPX MCP 벤치마크 & 전략 리뷰 (2026-06-13)

**렌즈**: 1차 목표 = *제주연구원 연구원이 HWP 업무를 AI로 무리없이* 한다.
처음(기획 시)엔 "목적에 맞는 MCP가 없다"고 판단해 직접 만들기 시작했다. 이제 구현
경험이 쌓였으니, 그 경험으로 생태계를 다시 보고 **계속 갈지 / 리팩토링할지 / 흡수·기여할지**
를 판단한다.

---

## 1. 연구원의 실제 워크플로 = 평가 기준

| 단계 | 실태 | "무리없이"의 의미 |
|---|---|---|
| 받는 포맷 | 대부분 **.hwp**(바이너리), 일부 .hwpx | .hwp를 손실 없이 다룰 수 있어야 |
| 도구 환경 | **Windows + 한컴오피스** | 설치·실행이 컴맹도 가능해야 |
| 폼 작성 | 평가위원 등록신청서 등 **flat 서식**, 반복 데이터 | 내 정보로 자동 채움 |
| 보고서 | 원내 보고서 서식(**15/16 flat**), 작성자마다 제각각 | 비표준 → AI가 본문 작성 가능한 구조로 |
| 산출물 | **다시 한컴에서 열림**(보안 '높음') | 깨짐·서식파괴 0, 원본 컨테이너 보존 |

→ 핵심 난이도는 *편집*이 아니라 **(a) .hwp 무손실 처리, (b) 비표준 서식의 구조화,
(c) 한컴 재오픈 호환**이다. 표면 텍스트/표 편집은 이미 흔하다.

---

## 2. 생태계 지도 (기능 구성)

세 부류로 갈린다.

### A. .hwpx 표면 편집 MCP (Hancom 불요, 크로스플랫폼)
- **Dayoooun/hwpx-mcp** (TS, **~125툴**, 직접 작성 파서 `HwpxParser.ts` ~170KB): 매우
  세분화된 편집 원시도구(문단/표/이미지/매달린들여쓰기/mermaid/위치인덱스). 구조 인식은
  **본문 텍스트 접두사 regex로 읽기전용 TOC**뿐 — 진짜 아웃라인 스타일 없음, 정규화·폼필·
  .hwp 없음. *표면 편집기.*
- **airmang/hwpx-mcp-server** (Py, **64+툴**, v2.4.1, 활발): **우리와 같은 `python-hwpx` 기반.**
  진짜 "개요 N" 아웃라인 스타일, `validate_structure`/`lint_text_conventions`/
  `inspect_official_document_style`, **문서-플랜 생성 파이프라인**, **template form-fit**,
  **네이티브 폼필**, .hwp 변환(pyhwp, **lossy**), md/PNG export, mail_merge. *구조·품질 인식
  저작 시스템.* **현 시점 가장 앞선 경쟁자.**
- TreeSoop/hwp-mcp (TS+rhwp WASM, 34툴): .hwp **읽기전용** 파싱(쓰기 미완), .hwpx r/w,
  SVG/HTML 렌더. 표면 편집. mjyoo2/hwp-extension 등도 동류.

### B. .hwp 완전 편집 MCP (한컴 COM 자동화, Windows+한컴 필수)
- **jkf87/hwp-mcp** (Py+win32com): 원조 COM MCP. .hwp **완전** 편집(한컴 기능 전부).
- crowwan(확장), m1ns2o/hwp-mcp-go(Go 30+툴) — 동 계열.
- 라이브러리: **pyhwpx**(가장 친절한 COM 래퍼), JunDamin/hwpapi.
- **비용**: Windows 종속 + 한컴 설치 + COM 취약성. **그러나 .hwp를 제자리에서 진짜로 편집하는
  유일한 길.**

### C. 파싱/변환 라이브러리 (MCP 아님)
- neolord0/**hwplib**(.hwp), **hwpxlib**(.hwpx, `hwp2hwpx` 변환기 — **우리 jar의 출처**),
  mete0r/pyhwp, hwplib-py, **python-hwpx**(우리·airmang 공통 기반).

---

## 3. 기능 구성 매트릭스

| 기능 | hwp-agent (우리) | airmang | Dayoooun | TreeSoop | COM류(jkf87…) |
|---|:--:|:--:|:--:|:--:|:--:|
| .hwpx 표면 편집(텍스트/표/이미지) | ◐ 일부 | ✅ 강 | ✅ 매우강 | ✅ | ✅(앱경유) |
| .hwp **읽기**(무한컴) | ✅ jar 무손실 | ◐ lossy | ❌ | ✅ 읽기전용 | ✅ |
| .hwp **쓰기/제자리편집** | ❌ | ❌ | ❌ | ❌ | ✅ COM |
| .hwp→.hwpx 변환 | ✅ **무손실(hwplib)** | ◐ lossy | ❌ | ❌ | (앱) |
| 폼필(라벨셀/체크박스) | ✅ +**프로필 자동매핑** | ✅ 네이티브필드 | ❌ | ◐ template | ◐ |
| **flat 문서 *재구조화***(아웃라인 선언) | ✅ **`normalize`** | ❌(템플릿의존 생성만) | ❌ | ❌ | ❌ |
| 구조 검증/린트 | ◐ `check` | ✅ 강 | ❌ | ❌ | ❌ |
| 템플릿 위 저작(MD→서식) | ✅ `write` | ✅ plan파이프라인 | ❌ | ❌ | ◐ |
| 추출(→Markdown) | ✅ `extract` | ✅ | ◐ text/html | ✅ | ◐ |
| 한컴 재오픈 호환(컨테이너 보존) | ✅ **명시적 노하우** | ◐ byte-patch | ◐ | ? | ✅(앱) |
| 컴맹 배포(웹/설치본) | ✅ serve+setup.exe | ❌(개발자용) | ❌ | ❌(npx) | ❌ |
| MCP 툴 수 | **6**(좁음) | 64+ | 125 | 34 | 30+ |
| 라이선스/활성 | Apache-2.0 | Apache-2.0 활발 | MIT | MIT | 혼재 |

(◐=부분, ✅=강점, ❌=없음)

---

## 4. 우리 현재 surface (정직한 자기점검)

- **CLI(15)**: classify, styles, check, instructions, convert, **normalize**, write, extract,
  **form** analyze/fill(+profile), image list/replace, meta, unmerged, **serve**, **mcp**, setup
- **ops(10모듈/3773줄)**: author, normalize, form, profile, extract, doctor, styles, images,
  metadata, container
- **MCP(6)**: analyze_form_slots, fill_form_slots, fill_form_from_profile,
  convert_hwp_to_hwpx, normalize_template, extract_to_markdown
- **표면(웹/MCP)**: serve(웹업로드+REST+OpenAPI), 로컬 stdio MCP, setup.exe(JRE동봉)

**잘한 것**: normalize(고유), 무손실 변환, 폼 프로필, 컴맹 3경로(웹/exe/GPT), 컨테이너 보존.
**약한 것**: 범용 .hwpx 편집 도구 수(airmang/Dayoooun에 압도), 구조 린트/플랜 생성(airmang 우위),
.hwp 쓰기(아무도 못 함, COM 제외), MCP 표면이 좁음.

---

## 5. 전략적 시사점

1. **범용 .hwpx 편집기 경쟁은 진다.** Dayoooun(125툴)·airmang(64툴)이 이미 더 넓고 활발.
   우리가 솔로로 64툴 MCP를 따라가면 영원히 추격. **하지 말 것.**

2. **MCP는 합성된다 — 경쟁이 아니라 보완이 답.** 연구원이 Claude/Codex에 **airmang(범용 편집)
   + hwp-agent(JI 정규화·폼·변환)** 를 동시에 등록하면 빈칸이 메워진다. 우리는 airmang의
   *못 하는 곳*만 채우면 된다.

3. **우리 해자는 좁지만 진짜이고 JI-고정**:
   - `normalize`(이미 flat인 문서 재구조화) — airmang도 못 함(생성/검증만).
   - 무손실 .hwp 변환(hwplib) — airmang은 lossy.
   - JI 특화: 서식 SSOT(`works-drive-index.yaml`), 실제 번호체계 휴리스틱, 신청서류 프로필.
   - 컴맹 배포(웹링크/exe) — 개발자용 MCP들이 안 건드림.

4. **같은 base(python-hwpx)라는 게 기회.** `normalize`를 **python-hwpx/airmang에 업스트림 기여**
   하면 유지부담↓·가시성↑·생태계 기여. JI-특화 래퍼(프로필·SSOT·serve)만 우리 것으로.

5. **가장 큰 미해결 = .hwp 쓰기/제자리편집.** "연구원이 무리없이"의 끝은 *자기 .hwp가 그대로
   고쳐지는 것*. 무한컴 OSS 라이터는 없다(rhwp 미완). 선택지:
   - (i) **.hwpx를 작업 포맷으로 수용**(변환→편집→hwpx 산출, 한컴서 다시 .hwp 저장) — 현실적, 현 경로.
   - (ii) Windows에서 **COM 도구(pyhwpx/jkf87)와 합성** — .hwp 제자리편집은 그쪽에 위임.
   - (iii) hwpx→hwp 라이터 직접 — 고비용·저효용. 비권장.

---

## 6. 권고 (요약)

**hwp-agent = "범용 편집기"가 아니라 "JI 비표준 서식 → AI 적용가능화 + 컴맹 배포" 특화 도구로
좁히고 깊게.**

- **유지**: normalize, 무손실 변환, 폼 프로필, serve/exe(컴맹), 컨테이너 보존.
- **포기/위임**: 범용 .hwpx 편집 툴 확장(→ airmang MCP 합성 권장), .hwp 제자리편집(→ COM 또는 hwpx 수용).
- **검토**: normalize 업스트림 기여(airmang/python-hwpx), MCP는 6툴 유지(슬림이 강점).
- **다음 빌드 후보**: ① normalize 완성(한글'높음'검수+4서식+수동불릿) ② `conform`(messy→정규화템플릿
  재배치, M6) ③ 정체성/README 재포지셔닝 ④ airmang 합성 가이드(둘 다 등록해 쓰는 법).

**핵심 한 줄**: 우리는 *작은데 아무도 안 하는* 것(비표준 재구조화 + 무손실 변환 + 컴맹 배포)을
JI에 고정해 지키고, 범용 편집은 airmang에 합성해 빌린다.
