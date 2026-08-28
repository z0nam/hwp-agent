# 출력 검증 루프 (output-verification)

hwp-agent가 생성한 HWPX의 **렌더링 품질**을 자동 검증한다. 검증의 source of truth는
**한컴 정식 엔진으로 뽑은 PDF**다. LibreOffice/H2Orestart 경로는 "docx 거쳐 변환한 듯한
번역체" 문제가 있어 정밀 검증에서 제외한다.

### 왜 한컴이어야 하나 (rhwp/LibreOffice 부적합)
hwp-agent **author 산출물은 lineseg(레이아웃 캐시)가 희박**하다 — author는 헤딩에만 lineseg를
복제하고 본문/표셀은 비워둔다(복제하면 틀린 레이아웃이 박히므로 한글 재계산에 맡기는 게 올바름;
실측: JAIX 제안요청서를 rhwp가 LinesegArrayEmpty 7388건으로 경고). 따라서 **lineseg에 의존·추정
하는 렌더러(rhwp 자동보정, LibreOffice 등)는 한글이 실제로 보여주는 화면과 다른 "추측"을 그린다**
→ 검증 SoT로 부적합. 한글(=hwp2pdf COM)의 재계산만이 연구원이 보는 진실이다. (lineseg-희박은
버그가 아니라 정상 — 한글에서 열면 멀쩡하다.)

## 노드 (사실관계 — 추정 금지, 다르면 먼저 확인)
- **변환 노드 namun-ji**: Windows, 상시 대기, 한컴 2022 설치. hwp2pdf 변환기 존재하나
  **dev 브랜치에 있고 아직 pull 안 됨**.
- **hwp-agent**: Python/uv, HWPX를 파일 레벨로 직접 조작(한글 앱 안 거침).
- **노드 간**: Tailscale 메시.

---

## Step 1 — 검증 래퍼 ✅ (한컴 무관, 순수 Python)

`src/hwp_agent/ops/verify.py` + `hwp-agent verify`. hwp2pdf 인터페이스와 **독립** —
PDF만 입력받으므로 Mac Studio에서도 돈다.

**입력 2종 (확장자 디스패치, `verify_document`)**:
- **PDF** (한컴 렌더 = 권위/2차): `verify_pdf` 그대로.
- **`.hwp`/`.hwpx`** (1차·로컬): `verify_hwp` → **rhwp `export-pdf`로 로컬 렌더** 후 동일 검증.
  한컴/ namun-ji 불요. rhwp 조판은 한컴과 페이지네이션이 다르므로(+25% 등) **굵직한 결함
  (그림 누락·빈 페이지·표 깨짐) 1차 탐지용**, 정밀 레이아웃 사인오프는 PDF(한컴) 경로로.
  렌더러는 주입 가능(`render_fn`) — 테스트는 rhwp 바이너리 없이 돈다. rhwp 경로: `--rhwp`
  인자 / `$RHWP_BIN` / PATH.

- 입력: PDF 경로
- PyMuPDF(fitz)로 페이지별 PNG 래스터화(메모리, DPI 기본 150)
- 가드: 없음/0바이트/손상 PDF → `result.error`로 실패(예외 아님)
- 페이지별 비전 판정(claude-opus-4-8, 강제 tool-call로 구조화):
  `missing_image / layout_broken / text_truncated / object_overlap / empty_page / render_failure`
- 출력: 페이지별 판정 + 전체 pass/fail + 문제 페이지 목록 (JSON `--json`)
- **프라이버시**: 페이지 이미지는 메모리에서만 처리, **Anthropic API 외 외부 경로 없음**
  (디스크 기록·업로드 안 함). 기관 문서는 그 한 번의 모델 호출 외엔 로컬에 머묾.
- **테스트 가능**: `verify_pdf(..., vision_fn=...)` 로 비전 함수 주입 → API/네트워크 없이 검증.
  `tests/test_verify.py` 7케이스(정상/문제페이지/max-pages/손상·빈·없는 파일/dpi).

사용:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
hwp-agent verify rendered.pdf            # 사람용 요약
hwp-agent verify rendered.pdf --json     # 구조화(파이프라인용)
hwp-agent verify rendered.pdf --max-pages 5 --dpi 200
```
설치: `pip install "hwp-agent[verify]"` (pymupdf + anthropic). 종료코드 0=pass, 1=fail, 2=설정오류.

---

## Step 0 — 현황 파악 (Step 2 통합 전 필수, 코드 작성 전 확인)

namun-ji에서:
1. **dev 브랜치 pull 전 `git status`** — 로컬 커밋/uncommitted 변경 확인(다른 머신과
   엇갈렸을 수 있음 → pull 충돌 방지).
2. pull 후 hwp2pdf **호출 인터페이스 확정**:
   - [ ] CLI(`hwp2pdf in.hwpx out.pdf`)인가 HTTP 엔드포인트인가?
   - [ ] 한컴 COM 보안모듈 팝업 억제(RegisterModule) 처리돼 있나?
   - [x] **무인/잠금 상태에서도 도는가?** → ❌ **아니오 (2026-06-22 실증).** SSH 셸은
         **세션 0**(비대화)이고, 거기서 `Dispatch("HWPFrame.HwpObject")`가 *"HWPFrame.HwpObject
         시작 중..."* 에서 **행**(90초 타임아웃). COM 한글은 **로그인된 대화형 데스크톱
         세션(세션 1)에서만** 동작. SSH/서비스(세션 0) 직접 호출 불가.
   - [x] 누수 가드: 호출당 `CoInitialize→Dispatch→…→Quit()` 1회 생애주기 + `--kill-hwp`
         + (신규) HancomDialogWatcher로 블로킹 대화상자 자동확인.
   - [x] 호출=CLI: `hwp2pdf <파일|폴더> --pdf [--docx] [-r] [--no-overwrite]
         [--kill-hwp|--allow-running-hwp] [--no-safe-temp] [--no-force-one-page]`. HTTP 없음.
         실행기 `…\.venv\Scripts\hwp2pdf.exe`. 이미 한글 떠 있으면 기본 거부(가드).
   - [x] RegisterModule(`FilePathCheckerModule`) 처리됨 → 보안 팝업 억제.

### 검증 결론 (트리거 구조 결정)
COM이 **세션 1에서만** 도므로, **SSH로 변환을 직접 띄우면 안 된다.** Step 2는 변환을
namun-ji의 **로그인 세션(세션 1)에서 상시 도는 워커**에 위임한다.

---

## Step 2 — 통합 (확정 구조: 세션 1 상시 워커)

COM이 세션 1에서만 도는 게 실증됐으므로, **inbox/outbox 큐 + 세션 1 워커** 패턴으로 간다
(hwp-preview-bot 선례와 일관). SSH/HTTP는 큐에 넣기만 하고, 변환은 워커가 한다.

```
[Studio/hwp-agent]  --(.hwpx/.hwp 투입)-->  [namun-ji inbox 폴더]
                                                  │  (세션 1 워커가 폴링)
                                                  ▼
                                        hwp2pdf <file> --pdf   (세션 1, COM 정상)
                                                  ▼
                                            [namun-ji outbox]  --(PDF 회수)-->  [Studio]
                                                                                   ▼
                                                                   hwp-agent verify (Step 1)
```

전제(namun-ji 운영):
- **자동 로그인 + 화면잠금 해제 유지**(세션 1 상시) — 없으면 COM 행.
- 워커를 **세션 1에서 기동**: 작업 스케줄러 "사용자가 로그온할 때만 실행"(가장 높은 권한 X,
  대화형 O) 또는 시작프로그램. *서비스(세션 0)로 돌리면 안 됨.*
- 워커: inbox 폴링 → `hwp2pdf` 호출 → 성공 시 outbox로, 실패 시 CSV/로그. 한 번에 1개씩,
  변환 후 한글 Quit(누수 가드).
- 큐 전달: Tailscale 파일공유 / scp / 작은 localhost HTTP 중 택1 (네트워크 노출 시 확인받기).

**파괴적/네트워크 노출 단계는 진행 전 확인받을 것.**

### Step 2 실측 (2026-06-23)
- 좀비 한글 PID 13532 → namun-ji 콘솔에서 `taskkill /F /IM Hwp.exe`로 정리 완료.
- `schtasks /it`로 **세션1 트리거는 성공**(runnerSession=1) — SSH(세션0)에서 세션1 변환을 띄우는
  메커니즘 자체는 동작.
- **그러나 변환이 행**: 로그 "HWP 파일 접근 보안 모듈: **꺼짐(모듈 사용 불가)**" → "[1/1] -> PDF"
  에서 멈춤(120s 타임아웃, PDF 0개). 원인 = **namun-ji에 FilePathCheckerModule 미설치**
  (HKCU/HKLM HNC 레지스트리 0건, DLL 없음). 보안 모듈 없으면 한글이 *파일 접근 허용?* 모달을
  띄워 COM 무한 대기(README 경고와 일치). 새로 넣은 HancomDialogWatcher도 이 모달은 못 닫음.
- **결론: Step 2 무인 운영의 진짜 전제 = FilePathCheckerModule 등록(또는 자동화 영구허용).**

### Step 2 해결 (2026-06-23) ✅
- namun-ji에 **FilePathCheckerModule.dll(x86, pyhwpx 번들 = 한글 32bit와 비트 일치)** 배치
  (`C:\Users\user\.hwpautomation\`) + 레지스트리 등록:
  `HKCU\Software\HNC\HwpAutomation\Modules` 값 `FilePathCheckerModule`=DLL경로.
- 재검증: `schtasks /it` 무인 변환이 **45초에 PDF 생성·완료**(등록 전엔 120초 행+PDF 0). 즉
  파일접근 대화상자가 *아예 안 뜸* → 데스크톱 격리/auto-click 레이스 문제 모두 무의미.
- **→ Tier-2(한컴 권위검증) 트리거 확정**: 세션1에서 `schtasks /it`(또는 시작프로그램 워커)로
  `hwp2pdf <file> --pdf` 호출 → PDF 회수 → `verify_pdf`. 자동 로그인+잠금해제 유지는 여전히 필요.
- 부수효과: hwp2pdf 자체 무인 배치 신뢰도도 해결됨. (auto-allow watcher 브랜치는 보안모듈로
  대체되어 불필요 — 대화상자가 안 뜨므로 클릭할 게 없음.)

### Step 2 클라이언트 (구현됨) — `hwp-agent pdf` / `hwp-agent docx`

hwp-agent 쪽 티어드 렌더 축(`src/hwp_agent/render/`)이 붙었다. HWP→HWPX(`convert/`)와
별개로 HWP/HWPX→**PDF/DOCX**를 낸다.

```bash
hwp-agent pdf report.hwpx                 # auto: namun-ji(hwp2pdf) 닿으면 그걸로, 아니면 rhwp
hwp-agent pdf report.hwpx --engine rhwp   # 강제 Tier-1(로컬, 한컴 무관)
hwp-agent pdf report.hwpx --engine hwp2pdf -o out.pdf
hwp-agent docx report.hwpx                # DOCX = Tier-2(hwp2pdf) 전용 (rhwp는 docx 불가)
hwp-agent verify out.pdf                  # 한컴 PDF면 권위 사인오프(Step 1)
```

- **선택(`--engine auto`)**: `docx`는 항상 Tier-2. `pdf`는 Tier-2 사용가능(config 있음 + ssh
  프로브 성공)이면 Tier-2, 아니면 Tier-1(rhwp)로 폴백.
- **Tier-2 왕복(schtasks 온디맨드)**: scp로 파일을 namun-ji inbox에 올림 → SSH로
  `schtasks /run /tn hwp-agent-hwp2pdf`(세션1) → outbox의 `<job>.done` 폴링 → scp로 회수 →
  정리. 워커 `scripts/render-inbox.ps1`이 `hwp2pdf <inbox> --pdf --docx --kill-hwp` 실행 후
  outbox로 옮기고 `.done`/`.err` 마커를 남긴다.
- **config**: `~/.config/hwp-agent/hwp2pdf.json`(예시 `examples/hwp2pdf.example.json`) —
  `host`(ssh 별칭), inbox/outbox 원격경로, task_name, 타임아웃. 필드별 `$HWP2PDF_*` env 오버라이드.
  없으면 Tier-2 미가용 → auto는 rhwp.
- **namun-ji 1회 세팅**: `scripts/install-hwp2pdf-worker.ps1`(세션1 `schtasks /it` 등록 +
  inbox/outbox + 워커 배치). 전제(자동로그인·잠금해제·FilePathCheckerModule)는 위 Step-2 해결
  그대로 — 스크립트가 강제하지 못하므로 타임아웃 메시지가 이 둘을 지목한다.
- 클라이언트/transport는 주입 가능(`tests/test_render.py`) → namun-ji 없이 오프라인 테스트.
