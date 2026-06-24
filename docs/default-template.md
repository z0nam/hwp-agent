# 기본 템플릿 (default template) — 양식 미배정 시 사용

`hwp-agent write content.md` 를 **템플릿 없이** 실행하면, 번들된 내용 중심 보고서
템플릿에 마크다운을 채워 넣는다. 연구원이 "양식을 따로 지정하지 않아도" 바로
`.hwpx` 결과를 얻게 하는 것이 목적.

## 해석 순서 (`ops/template.resolve_template_path`)

`--template` 이 없으면 아래 순서로 첫 번째 존재하는 것을 쓴다 (profile 규약과 동일):

1. `--template <path>` (명시) 
2. `$HWP_AGENT_TEMPLATE`
3. `~/.config/hwp-agent/template.hwpx` (사용자가 설치한 하우스 기본값)
4. **번들 기본값** `hwp_agent/assets/default-template.hwpx`

CLI는 기본값으로 떨어질 때 어떤 소스를 썼는지 한 줄 알려준다
(`using bundled default template: …`). `-o` 없이 실행하면 출력은 **마크다운 파일
옆**(`content.hwpx`)에 쓴다 — 공유되는 템플릿을 덮어쓰지 않기 위해서다.

사용자 기본값을 깔려면: 원하는 `.hwpx` 를 `~/.config/hwp-agent/template.hwpx` 로
복사하면 번들값을 가린다.

## 번들 기본 템플릿의 구성

GD `template.hwpx`(JRI 보고서 서식 계열)를 가공해 author 친화적으로 만든 것:

- **헤딩 사다리** (OUTLINE, 자동 번호): `Ⅰ.`(로마자, 20pt) → `1.`(13pt) → `1)`(11pt).
  마크다운 `#`/`##`/`###` 가 여기에 매핑되며, 번호는 스타일이 자동 부여하므로
  마크다운 제목에 번호를 직접 쓰지 않는다.
- **글머리 사다리**: `￭` → `AI:BULLET_1`, `-` → `AI:BULLET_2`
  (`normalize` 로 선언 — 글리프 서열 기준. 원본은 `-` 가 BULLET_3 로 잡혔었음).
- **`{{body}}`** 삽입 마커 — author 가 본문을 여기에 넣고 마커는 제거.
- **`{{table_template}}`** 표 — 생성 표가 본뜨는 하우스 스타일 참조용. 내용은
  중립 placeholder(`구분/항목/내용`)로 비워둠. (이 참조 표 자체는 출력에 남는다.)
- **`AI:INSTRUCTION`** 문단 — AI 작성 지침(번호 자동, 글머리 규약 등). `write`
  실행 시 자동 제거되고, `hwp-agent instructions` 로 읽을 수 있다.

## 갱신

번들 템플릿을 바꾸려면 새 `.hwpx` 를 `src/hwp_agent/assets/default-template.hwpx`
로 교체한다 (wheel 에 포함됨 — `[tool.hatch.build.targets.wheel] packages` 가
패키지 하위 비-파이썬 파일까지 담는다). 가공 절차는 normalize(글머리 선언) +
표 중립화 + `AI:INSTRUCTION` 부여(미사용 스타일의 `engName` 을 byte-치환)이다.
