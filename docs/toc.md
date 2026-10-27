# 목차 — 차례·표 차례·그림 차례 (issue #33)

쪽 번호는 한글이 조판한 뒤에야 정해진다. 그래서 hwp-agent는 쪽 번호를 **계산하지 않는다.**
`build`는 목차 섹션의 항목만 실제 제목과 캡션으로 다시 쓰고, 쪽 번호 칸은 비워 둔 채
한글이 채우게 한다(도구 › 차례/색인 › 차례 새로 고침).

## 조사: 한글이 쓰는 차례 마크업 (2026-10-27)

### 표본

| 출처 | 차례 형태 |
|---|---|
| 저장소 기관 서식 `examples/house-forms/*과제_서식.normalized.hwpx` | 정적 텍스트. 앞선 보고서의 차례(`Ⅰ. 세계경제 전망 ⇥ 2`…)와 `표 차례`·`그림 차례` 표제 아래 그 보고서의 캡션들이 그대로 남아 있다 |
| `~/.config/hwp-agent/template.hwpx`, 정책이슈브리프 | 차례 없음 |
| 연구원 보고서 hwpx 약 120개(`01.JRI_PM/*/90.최종보고서` 등) | `TABLEOFCONTENTS` 필드 없음. 차례는 정적 텍스트이거나 하이퍼링크 문단(`HYPERLINK` 필드는 많이 있음)으로만 남아 있다 |
| `26p16_지속가능지표_최종보고서_master.hwpx`(한글 저장, lastsaveby 조남운) | **`TABLEOFCONTENTS` 필드 1개**. 제목 차례가 필드로 감싸여 있다 — 아래 마크업의 근거 |

원본은 읽기만 했고, 임시 폴더에 복사해서 열어 보았다.

### 제목 차례 = `TABLEOFCONTENTS` 필드 + 개요 하이퍼링크

첫 항목 문단의 첫 run에 필드 시작이 있고, 마지막 항목 **다음 문단**에 필드 끝만 있다.

```xml
<hp:p styleIDRef="14(목차장)"><hp:run charPrIDRef="4">
  <hp:ctrl><hp:fieldBegin id="…" type="TABLEOFCONTENTS" name="" editable="1" dirty="0"
      zorder="-1" fieldid="628387683" metaTag="">
    <hp:parameters cnt="2" name="">
      <hp:integerParam name="Prop">8</hp:integerParam>
      <hp:stringParam name="Command" xml:space="preserve">TableOfContents:set:140:ContentsMake:uint:17 ContentsStyles:wstring:0: ContentsLevel:int:1 ContentsAutoTabRight:int:0 ContentsLeader:int:0 ContentsHyperlink:bool:1  </hp:stringParam>
    </hp:parameters></hp:fieldBegin></hp:ctrl>
  <hp:ctrl><hp:fieldBegin id="…" type="HYPERLINK" … fieldid="627600491">
    <hp:parameters cnt="5" name="">
      <hp:integerParam name="Prop">0</hp:integerParam>
      <hp:stringParam name="Command">?#8184234;0;1;0;</hp:stringParam>
      <hp:stringParam name="Category">HWPHYPERLINK_TYPE_HWP</hp:stringParam>
      <hp:stringParam name="TargetType">HWPHYPERLINK_TARGET_OUTLINE</hp:stringParam>
      <hp:stringParam name="DocOpenType">HWPHYPERLINK_JUMP_CURRENTTAB</hp:stringParam>
    </hp:parameters></hp:fieldBegin></hp:ctrl>
  <hp:t>I. 서론<hp:tab width="26870" leader="0" type="2"/>3</hp:t>
  <hp:ctrl><hp:fieldEnd beginIDRef="(HYPERLINK id)" fieldid="627600491"/></hp:ctrl><hp:t/>
</hp:run></hp:p>
… 항목 문단들 …
<hp:p styleIDRef="15"><hp:run charPrIDRef="6">
  <hp:ctrl><hp:fieldEnd beginIDRef="(TABLEOFCONTENTS id)" fieldid="628387683"/></hp:ctrl><hp:t/>
</hp:run></hp:p>
```

- 항목 하나 = 문단 하나. 텍스트 = 제목 + **오른쪽 탭(`type="2"`)** + 쪽 번호. 하이퍼링크가 쪽 번호까지 감싼다.
- `?#<id>;0;<k>;0;` 의 `<id>`는 **대상 문단의 `hp:p id`**(제목 문단). `<k>`는 대상 종류: 1 개요(OUTLINE), 2 표(TABLE, `hp:tbl id`), 3 그림(FIGURE, `hp:pic id`).
- 그 표본에서 제목 문단은 `heading type="OUTLINE"`(개요 1·2) 문단이고 번호도 개요 자동 번호다. `ContentsMake:uint:17`·`ContentsLevel:int:1`은 "개요 문단으로 모으기, 2수준"으로 보이나 비트 의미는 확인하지 못했다.
- 필드 안에는 항목 문단만 있다. 장 사이 구분선 문단은 필드 밖에 있었다.

### 표 차례·그림 차례 = 필드 없는 하이퍼링크 목록

같은 표본에서 표 차례(19개)·그림 차례(8개)는 **`TABLEOFCONTENTS` 필드 밖**에 있다. 항목마다 `TARGET_TABLE`(`;0;2;0;`, 표 id)·`TARGET_FIGURE`(`;0;3;0;`, 그림 id) 하이퍼링크가 달려 있다. 표·그림 차례용 필드 마크업 표본은 아직 없다.

## 구현 (`hwp_agent.ops.toc.fill_toc`, `build`가 기본으로 호출)

1. **목차 섹션 찾기**: `표 차례`·`그림 차례` 표제가 있거나, 번호 붙은 샘플 항목(`Ⅰ.`·`1.`·`1)` + 오른쪽 탭)이 2개 이상인 첫 섹션.
2. **제목 수집**: 목차 섹션 뒤의 섹션들에서 `HEADING_1‥3` 롤 스타일 문단(`role_map`). 문단 id가 없거나(`0`·`2147483648`) 겹치면 새 id를 준다(하이퍼링크 대상).
3. **캡션 수집**: `<hp:caption>`이 달린 `numberingType="TABLE"`/`"PICTURE"` 개체. 캡션의 `<hp:autoNum>`은 `num` 값으로 읽는다. 그림(1×2 표, #31)은 안의 `hp:pic`을 링크 대상으로 삼는다.
4. **차례 다시 쓰기**: 샘플 항목 중 첫 `Ⅰ.`부터 마지막 번호 항목까지를 걷어 내고, 수준별 샘플 항목 문단(paraPr·charPr·탭)을 복제해 새 항목을 만든다. 장 사이에는 샘플의 장 사이 문단(빈 줄 + 구분선)을 복제해 넣는다. 수준은 서식 샘플이 보여 주는 만큼만 넣는다. 목록 전체를 위 마크업 그대로 `TABLEOFCONTENTS` 필드로 감싼다.
5. **표·그림 차례**: 표제 아래 샘플 항목을 캡션 항목으로 바꾼다(필드 없이 하이퍼링크만, 표본과 같은 형태). 항목이 없으면 표제도 지운다.
6. 번호 없는 항목(`요약`·`참고문헌`·`부록`)은 남기고, 아라비아 숫자 쪽 번호만 비운다(`요약 ⅰ`처럼 서식이 고정한 앞부분 번호는 둔다).

쪽 번호 칸은 모두 비어 있다. manifest `"toc": false` 또는 `--no-toc`면 목차 섹션을 건드리지 않는다.

## 한글 확인

- **hwp2pdf**: 2026-10-27 시도 시 서버 머신에서 한글이 실행 중이어서 설계대로 건너뛰었다. hwp2pdf는 변환만 하므로 필드를 갱신하지 않는다 — 열기 성공 여부만 확인할 수 있다.
- **차례 새로 고침**: 확인 대기. 우리 `build` 결과의 제목 문단은 개요 문단이 아니다(`heading type="NONE"`, 번호는 글자). 그래서 한글이 "개요 문단으로 모으기"로 새로 고치면 항목을 찾지 못할 수 있다. 확인 결과에 따라 필드를 유지할지, 정적 차례(+ 쪽 번호 수동 입력 안내)로 둘지 정한다.
