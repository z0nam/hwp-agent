# hwpx-report-kit 결합 검토 (boys8man/hwpx-report-kit, 2026-08-19)

이중화 박사가 공유한 [`boys8man/hwpx-report-kit`](https://github.com/boys8man/hwpx-report-kit)
(c4cf7c7 기준)을 읽고, **검사기를 hwp-agent 산출물에 실제로 돌려** 결합 가능성을 판정한 기록.
상대측 검토 메모는 그 저장소의 `docs/COMBINE.md`.

## 결론

**녹여넣을 수 있다. 단 한 덩어리로 머지하지 말고 세 트랙으로 나눈다.**
겹치는 코드가 사실상 없고(그쪽은 생성 기능 0, 우리는 조판·문체 검사 0), 검사기가
완성된 hwpx 를 입력받는 순수 판정기라 파이프라인을 건드리지 않고 붙는다.

| 트랙 | 대상 | 판정 |
|---|---|---|
| **A. 규칙집** | `docs/hwpx-rules.md` 15종 | **즉시 흡수** — 우리 SKILL.md 에 "직접 편집 금기" 절로. 최대 자산 |
| **B. 검사기** | `hwpx_validate` · `report_style` · `pdf_linebreak` | **흡수** — `hwp-agent lint` 계열로 재배선 |
| **C. 원자료 감사** | `hwpx_reconcile` · `report_audit` · `excel_*` · `stale_scope_scan` | **흡수하지 않음** — 별개 제품(보고서 수치 감사). 별도 CLI 로 두고 상호 참조 |

선결 조건 하나: **그쪽 저장소에 LICENSE 가 없다.** 우리는 Apache-2.0 이라
코드를 벤더링하려면 라이선스를 먼저 정해 달라고 요청해야 한다(문서만 인용하는
트랙 A 는 출처 표기로 진행 가능).

## 1. 실측 — 우리 산출물에 그쪽 검사기를 돌린 결과

hwp-agent 가 만든 4종에 `hwpx_validate.py` 를 돌렸다.

| 입력 | 출처 | 결과 |
|---|---|---|
| `examples/정책과제-template.hwpx` | 한컴 저장 템플릿 | C1~C8 전부 OK. 단 **5,487,901 → 777,982 bytes** (7.05배) 로 파일이 바뀜 |
| `tests/tmp/authored_demo.hwpx` | `hwp-agent write` 산출물 | C1~C8 전부 OK, 교정 0건 |
| `tests/tmp/form_demo.hwpx` | `hwp-agent form fill` 산출물 | C1~C8 전부 OK, 교정 0건 |
| `tests/tmp/sample_big_converted.hwpx` | `hwp-agent convert` 산출물 | **크래시** (아래 2-①) |

→ **우리 생성 경로(write/form fill)는 패키지 규격상 깨끗하다**는 것이 외부 검사기로 확인됐다.
이것만으로도 "성과를 맞춰 보는 공통 테스트 스위트" 라는 그쪽 제안의 값은 이미 나왔다.

`report_style.py` 도 우리 산출물에서 정상 동작하며 유의미한 지적을 낸다(구어투 2건,
문장 미완 3건, 조사 앞 공백·en-dash, 줄 끝 숫자 4건, 줄 밀도 1건). 오탐도 있다(2-③).

## 2. 돌려줄 이슈 6건

① **`convert` 산출물에서 크래시** — `META-INF/container.rdf` 가 없으면
`FileNotFoundError` 로 죽는다(`hwpx_validate.py:116`, 무조건 열기).
hwp2hwpx 변환본은 이 파트를 아예 만들지 않는다. 한컴 네이티브 저장본(우리 템플릿·픽스처)에는
항상 있으므로 **"있으면 일치 검사, 없으면 skip"** 으로 고치면 된다.
동시에 우리 쪽 과제이기도 하다 — 변환본에 `container.rdf` 가 없어도 한컴이 여는지는
확인됐지만, 넣어 주는 편이 규격에 가깝다(별건으로 볼 것).

② **`hwpx_validate.py` 는 판정기가 아니라 교정기다** — README·COMBINE 은
"검사기는 판정만 하고 교정하지 않는다"고 하지만, 이 도구는 C1·C4b·C5·C6 을 고치고
**C9 에서 항상 입력 파일을 제자리 재압축해 덮어쓴다**(`os.replace(tmp, TARGET)`).
백업도 없다. 우리 템플릿 5.4MB 가 778KB 로 바뀐 것을 파일 크기를 보고서야 알았다.
내용은 무손실이었지만(40/40 파트 sha256 동일) **`--fix` 옵트인 + 백업**이 맞다.
`hwpx_normalize.py`·`repack_hwpx.py`·`excel_report_nav.py` 는 백업을 만든다 — 이 도구만 예외.

③ **캡션 자동번호에서 ⑥ 줄 끝 숫자 오탐** — `report_style.py` 의 `plain()` 이 태그를 걷어내
`<hp:autoNum>` 이 사라지므로 캡션이 `[표 A-]` 로 읽힌다. 같은 캡션 14건이 전부 지적됐다.
`<hp:autoNum>` 을 `0` 같은 자리표시 문자로 치환하면 사라진다.
같은 이유로 그림 대체텍스트(`사진 찍은 날짜:` → 구어투 `찍은`)도 오탐이다 —
`<hp:shapeComment>` 는 본문이 아니므로 제외 대상.

④ **규칙 2(linesegarray 비우기)가 우리 실측과 충돌한다.** 우리는
**개요 수준 제목 문단에서 `<hp:linesegarray>` 를 지우면 한컴이 그 제목을 본문으로 강등**시키는
것을 확인해, `author.py` 가 같은 스타일의 실제 제목에서 캐시를 복제해 넣는다
(`_lineseg_index`, `docs/author-backlog.md` 항목 C).
`hwpx_normalize.py` 를 우리 산출물에 돌려 보니 **1,955건을 전부 비웠다** — 그대로 규칙 9
("작업 끝나면 반드시 실행")를 따르면 제목 개요가 무너진다.
→ 규칙을 **"본문 문단은 비우고, 개요 제목 문단은 동일 스타일에서 복제해 유지"** 로 좁힐 것을 제안.

⑤ **규칙 3(`id="2147483648"` 오버플로)의 근거를 다시 볼 것.** 이 값은 사고의 흔적이 아니라
**한컴이 스스로 쓰는 값**으로 보인다. 한컴 네이티브 저장본에서 실측:

| 파일 | 오버플로 id 문단 |
|---|---|
| `examples/정책과제-template.hwpx` (한컴 저장) | 262 / 331 |
| `tests/fixtures/sample_hwpx.hwpx` (한컴 저장) | 1,591 / 1,955 |

`hwpx_normalize.py` 는 이것을 전부 `id="0"` 으로 바꾼다(한 파일에서 1,591건).
글자 겹침과의 인과가 실측으로 확인된 것인지, 아니면 linesegarray 정리 효과와
섞여 관찰된 것인지 물어볼 것.

⑥ **JI 하드코딩은 거의 없다** — 남은 것은 `hwpx_validate.py:34` 의 기본 경로
(`03.집필/[초안]원도심…`)와 `excel_report_kornames.py` 의 한글 변수명 사전 정도.
전자는 지우고 인자 필수로, 후자는 사전 파일로 빼면 된다.

## 3. 우리가 새로 얻는 것 / 이미 가진 것

**새로 얻는 것 (우리에게 전무한 영역)**
- 조판 품질 — 낱말 갈라짐(`breakNonLatinWord`), 자간 판단 기준(±3·15% 밀도), 줄 끝 숫자·단위 분리
- 서식 목록 조작 금기 — `charPrIDRef` 를 **id 가 아니라 목록 위치**로 해석하므로 **맨 뒤에만 추가**
  (우리 `author.py` 의 `_emphasis_char` 가 charPr 을 새로 만든다 → `python-hwpx` 의
  `ensure_char_property` 를 확인한 결과 **`append` + `max(id)+1` + `itemCnt` 갱신**이라 이미 안전.
  다만 이 규칙이 성립하는 이상 우리가 raw XML 로 서식을 건드리는 경로가 생기면 즉시 위험해진다 —
  규칙집에 명시해 둘 값어치가 있다)
- `<hp:tbl>` 문단 밖 배치 → 한컴 저장 시 표·그림 소실 (열기만으로는 안 잡히는 유형)
- 문단 통째 교체 시 메모(`<hp:fieldBegin>`) 소실
- 셀 안 여백이 `<hp:inMargin>`(표)·`<hp:cellMargin>`(셀) 두 군데라는 점
- 문체 검사(정책보고서 구어투 대응표) — 기관 프로파일로 빼면 범용

**이미 가진 것 (중복 흡수 금지)**
- 클린 재압축(규칙 10): `guard.py:141` 의 지문 스탬프가 이미 `mimetype` STORED 선두 +
  나머지 DEFLATE 로 재압축한다. 실측상 `write`·`form fill` 산출물은 STORED 엔트리가
  mimetype 하나뿐이다. **비대한 것은 우리가 저장소에 커밋해 둔 템플릿 자체**(5.4MB)이므로,
  `repack_hwpx.py` 를 흡수할 게 아니라 **템플릿 에셋을 한 번 재압축해 커밋**하면 된다(→ 778KB).
- 렌더 검증: `verify`(rhwp + 비전 모델)가 레이아웃 붕괴·이미지 누락을 크로스플랫폼으로 잡는다.
  COM 이 잡는 "저장해야 드러나는 소실"과는 **다른 층**이다(4-② 참조).
- 표 id·개체 id 관리, 컨테이너 보존 편집(`container.py`), 덮어쓰기 가드(`guard.py`)

**메모**: `docs/images.md` 는 "전체 재압축은 한컴 보안경고를 유발한다"고 적어 두었는데,
`guard.py` 는 실제로 전체 재압축을 하고 문제없이 쓰이고 있다. 보안경고의 진짜 트리거가
"재압축" 자체가 아니라 **엔트리 누락·mimetype 위치/압축 위반**일 가능성이 크다.
그쪽이 매번 재압축하고도 사고가 없었다는 점이 방증이다. 우리 문서 문구를 좁힐 것(별건).

## 4. 그쪽 4가지 질문에 대한 답

**① 편집 계층 일원화** — 동의. 생성은 `python-hwpx`, 검사기는 raw 유지.
근거를 하나 보태면, 우리는 세 번째 방식도 쓴다: **원본 ZIP 을 열어 해당 파트 바이트만
치환하고 나머지 `ZipInfo` 를 그대로 재발행**하는 방식(`ops/container.py`).
`hwpx_validate.py` 의 교정 경로(C1·C4b·C5·C6)를 이 방식으로 바꾸면 입력 파일의
컨테이너를 건드리지 않고 고칠 수 있어 ②의 위험도 같이 사라진다.

**② COM 검증의 위치** — 옵트인 격리에 동의. 다만 층을 나눠 부르자.
- `verify render` (크로스플랫폼, 이미 있음): rhwp 로 SVG/PDF 를 뽑아 비전 모델이 판정. 겹침·누락·잘림.
- `verify roundtrip` (윈도우 전용, 신설 후보): COM `Open`→`SaveAs`→ 표·그림 **개수 대조**.
  규칙 11-2 의 "저장하면 사라지는 표"는 이 층에서만 잡힌다.
후자는 우리 크로스플랫폼 원칙을 깨지 않는다 — 없으면 skip 하고 그 사실을 보고하면 된다.
`pdf_linebreak.py` 는 입력이 PDF 이므로 rhwp 로 뽑은 PDF 를 물리면 **맥에서도 돈다**(윈도우 전용 아님).

**③ 기관 서식의 일반화** — 우리 쪽에 이미 두 개의 축이 있다. 그대로 쓰면 된다.
- 스타일 역할 선언: `docs/template-convention.md` 의 `AI:HEADING_n` / `AI:BULLET_n` 규약과
  `hwp-agent normalize`(선언 자동 주입) — 기관별 서식샘플을 코드가 아니라 **문서 자체**로 흡수한다.
- 값 프로파일: `ops/profile.py` + `examples/profile.example.json`.
→ 제안: 문체·조판 상수(자간 기본 -5, 셀 여백 142, 구어투 대응표, 표두 색 `#2E5A88`)를
`profiles/ji.json` 같은 **기관 프로파일 파일**로 빼고, 검사기는 프로파일을 인자로 받게 한다.
추상화 수준은 "코드에 기관명이 남지 않는 선"이면 충분하다.

**④ 이미지 삽입 의존** — 지금 우리 `hwp-agent image` 는 **교체(replace)만** 한다.
기존 `<hp:pic>` 슬롯의 BinData 바이트를 바꾸고 `content.hpf`·표시 크기를 맞추는 방식이라,
`insert_image_in_cell` 처럼 **없던 그림을 새로 넣는 것은 아직 안 된다.**
다만 필요한 조각은 다 있다 — 표 셀 조작(`author.py` 의 zone/cell 처리), 컨테이너 보존 삽입,
크기 계산(px↔HWPUNIT ×75). 그림 셀을 가진 표를 템플릿에서 복제해 슬롯을 만든 뒤
`image replace` 로 채우는 경로가 현실적이고, 이러면 외부 MCP 의존을 끊을 수 있다.
**우선순위를 주면 넣을 수 있다** 정도가 정직한 답.

## 5. 흡수 방식 (배선안)

```
hwp-agent lint <file.hwpx>              # 신설. 기본은 판정만, --fix 로 교정
  --package     hwpx_validate  (C1~C8, container.rdf 없으면 skip)
  --style       report_style   (--profile ji.json)
  --typeset     pdf_linebreak  (rhwp 로 PDF 를 뽑아 자동 연결)
hwp-agent verify roundtrip <file>       # 윈도우+한컴에서만. 없으면 skip 보고
```

- `check` 는 이미 **템플릿 스타일 체계 진단**이라 이름이 겹친다 → 새 동사는 `lint`.
- 규칙집은 `docs/hwpx-editing-rules.md` 로 들여오고 `skills/hwp-agent/SKILL.md` 에서 링크.
  우리 실측과 충돌하는 2건(④⑤)은 **우리 주석을 달아** 반영한다.
- 원자료 감사(트랙 C)는 hwp-agent 범위 밖. 그쪽 저장소를 그대로 두고
  README 에서 상호 링크하는 편이 두 제품 다 깨끗하다.

## 6. 재현

```bash
gh repo clone boys8man/hwpx-report-kit
cp examples/정책과제-template.hwpx /tmp/probe.hwpx        # 제자리 수정하므로 반드시 복사본
python3 tools/hwpx_validate.py /tmp/probe.hwpx           # → 5,487,901 → 777,982 bytes
python3 tools/report_style.py  tests/tmp/authored_demo.hwpx
python3 tools/hwpx_normalize.py /tmp/probe2.hwpx         # → linesegarray 1,955건 소거 확인
```
