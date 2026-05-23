# Conversion findings

A running catalog of HWP → HWPX conversion behavior and fidelity loss, built up
as we test against real documents. Populate as evidence accumulates.

## Toolchain (observed)

- `hwp2hwpx` `pom.xml` targets Java **7** source/target. Modern `javac`
  (JDK 20+) refuses `-source 7`, so `bootstrap.sh` overrides the compiler to
  Java 8. Build itself runs fine on JDK 17–21.
- Runtime deps resolved from Maven Central: `kr.dogfoot:hwplib`,
  `kr.dogfoot:hwpxlib`.

## Conversion loss catalog

### `container.xml` declares a `Preview/` part that is never written
- **Source:** any `.hwp` (observed on `tests/fixtures/sample_hwp.hwp`).
- **After convert:** hwp2hwpx writes `META-INF/container.xml` listing
  `Preview/PrvText.txt` as a rootfile, but the package contains no `Preview/`
  directory at all. A genuine Hancom-authored HWPX includes both
  `Preview/PrvText.txt` and `Preview/PrvImage.png`.
- **Symptom:** strict OPC readers reject the file. `python-hwpx` raises
  `HwpxStructureError: Root content 'Preview/PrvText.txt' ... is missing`, so
  the converted document cannot be opened by the editing layer.
- **Severity:** structural (blocks downstream editing); no text/formatting loss.
- **Workaround:** `Hwp2HwpxBackend` normalizes the output by adding an empty
  placeholder for any declared-but-missing rootfile under `Preview/`
  (`_normalize_hwpx`). After this, `python-hwpx` opens the document and reads
  all paragraphs. The synthesized preview is a stub — a real `PrvText.txt`
  would carry the plain-text preview; not reconstructed for now.
- **Note:** opening still logs non-fatal `manifest에서 ... fallback` warnings
  (masterPage / history / version parts located by filename fallback rather
  than via the manifest). Harmless today; revisit if it affects edits.

### Line-spacing PERCENT values inflated 10× (page overflow)
- **Source:** `tests/fixtures/sample_big_hwp.hwp` (2.7 MB, 6 sections). Visible
  worst on the table of contents (목차) but affects body paragraphs too.
- **After convert:** some `<hh:paraPr>` get
  `<hh:lineSpacing type="PERCENT" value="1600" .../>` — i.e. 1600% — where the
  intended value is 160%. The whole document mixes correct values
  (100–230) with a cluster of exactly-10×-too-large ones
  (1500, 1600, 2800, 3000, 3200, 5600 → should be 150, 160, 280, 300, 320, 560).
  14 paragraph styles affected in this doc.
- **Symptom:** the cached `<hp:linesegarray>` still holds the original (correct)
  line positions, so `python-hwpx` text extraction is unaffected and the file
  *looks* fine programmatically. But when Hangul **re-lays out** the document it
  applies the paraPr value (1600%), so each line takes ~10× the vertical space:
  the TOC and body overflow across pages and page 1 looks nearly empty.
  Confirmed visually against the original in Hangul (left=converted, right=hwp).
- **Severity:** structural/layout (no text loss; geometry only).
- **Likely cause:** unit/scale mismatch in hwp2hwpx when mapping a specific
  HWP line-spacing storage variant to HWPX PERCENT (factor-of-10).
- **Fix (shipped):** in `header.xml`, for `type="PERCENT"` lineSpacing with
  `value >= 1000` (implausible as a real percentage), divide by 10. Cleanly
  separates the 14 buggy styles from the legitimate ≤230 ones here. Verified in
  Hangul, then integrated into `_normalize_hwpx` (`linespacing:<n>` tag).
  Caveat: validated on one document so far; revisit the 10× heuristic as more
  samples arrive.
- **Note:** the TOC entries are *not* lost — all 56 are present as well-formed
  `HYPERLINK` fields (begin/end balanced 56/56). A separate cosmetic loss: TOC
  tab leaders are all `leader="0"` (dotted "……" leaders dropped).

### Table `pageBreak` flipped CELL→TABLE → full-page border frames collapse
- **Source:** `tests/fixtures/sample_big_hwp.hwp`. Most visible on the
  "연구요약" full-page border frame (a `<hp:tbl>` page-height box, section2):
  it renders as a thin bar at the top of the page instead of framing the page,
  and the body (Ⅰ.서론 + ◯ bullets, which correctly flows *after* the table)
  appears to spill below it.
- **Root cause (confirmed by reference comparison):** diffing our output against
  a Hancom-authored HWPX of the *same* document
  (`tests/fixtures/sample_big_ref.hwpx`) showed the cell/table sizes are
  **identical** — the only meaningful difference is the table's `pageBreak`
  attribute. hwp2hwpx rewrites table `pageBreak="CELL"` as `pageBreak="TABLE"`
  wholesale:

  | pageBreak (tables) | Hancom ref | hwp2hwpx |
  |---|---|---|
  | CELL  | 52 | 1  |
  | TABLE | 1  | 51 |
  | NONE  | 22 | 22 |

  With `CELL`, a cell taller than the page splits across pages, so a page-height
  border frame fills the page; with `TABLE`, the table won't split and the frame
  collapses. (NONE is preserved correctly; only CELL is corrupted.)
- **Severity:** structural/layout (no text/content loss — text, images, order
  all intact; only frame paging is wrong).
- **Fix (shipped):** replace table `pageBreak="TABLE"` → `pageBreak="CELL"` in
  `Contents/section*.xml`. Safe to do by string replace: paragraphs use
  `pageBreak="0"/"1"`, only tables use the `TABLE`/`CELL`/`NONE` enum. New
  distribution CELL=52/TABLE=0/NONE=22 matches the reference's CELL=52. Verified
  in Hangul, then integrated into `_normalize_hwpx` (`pagebreak:<n>` tag).
- **Note:** the reference has exactly 1 legitimate `pageBreak="TABLE"`; a blanket
  flip also converts that one. Low-risk (a keep-together table becomes
  splittable). Revisit if a future sample shows it matters.
- **Earlier dead end (recorded for posterity):** first suspected collapsed
  `<hp:sz>`/`<hp:cellSz>` heights and set `sz.height = curSz.height` on the
  `<hp:rect>` 목차/표차례/그림차례 boxes (`sample_big_fixed2.hwpx`). That did
  nothing — the cell heights are byte-identical to the (correctly-rendering)
  reference, so height was never the issue; `pageBreak` was.

### Non-BMP (supplementary-plane) characters corrupted to U+FFFD — DATA LOSS
- **Source:** `tests/fixtures/sample_big_hwp.hwp`. E.g. the 겹낫표 around
  "『AI·디지털 대전환 로드맵』" in the conclusion; renders as ◆◆ / boxes.
- **Root cause (confirmed against `sample_big_ref.hwpx`):** Hancom stores those
  brackets as **supplementary-plane PUA** codepoints — `U+F0854` (『) and
  `U+F0855` (』), in plane 15. The Hancom-authored reference keeps them verbatim
  (0 replacement chars). hwp2hwpx instead writes **two `U+FFFD` per character**
  (raw bytes `EF BF BD` ×2). The 2-per-char pattern = a UTF-16 **surrogate-pair**
  mishandling: the writer iterates UTF-16 code units and emits U+FFFD for each
  surrogate half. Confirmed in the raw converter output (not our repackaging):
  6 FFFD bytes across section2 (2) + section3 (4); reference has 0.
- **Scope:** only characters above U+FFFF break. BMP special chars are fine
  (e.g. halfwidth corner brackets ｢｣ U+FF62/63 survive in both files). So this
  hits the Hancom PUA-A punctuation/symbols that live in plane 15.
- **Severity:** **data loss** — unlike the line-spacing and pageBreak bugs, the
  original codepoint is *destroyed*. U+FFFD carries no identity, so the HWPX
  output alone cannot be repaired by a post-hoc mapping table.
- **Localized to `hwplib` (the HWP *reader*), confirmed:** dumping
  `TextExtractor.extract(...)` straight from hwplib (before hwp2hwpx/hwpxlib
  touch anything) already returns `U+FFFD U+FFFD` for these chars. So hwplib
  fails to assemble the UTF-16 **surrogate pair** that encodes the
  supplementary-plane codepoint and substitutes a replacement char per half.
  (BMP variants like ｢｣ U+FF62/63 in the same document read fine.) Therefore the
  byte info is gone at the very first stage — hwp2hwpx and hwpxlib are blameless.
- **Version bump does NOT help:** rebuilt with `hwpxlib 1.0.9` (latest) →
  still 6 × FFFD, 0 × U+F0854. `hwplib` is already at its latest (1.1.10).
- **Exact location:** `HWPCharNormal.intToString(int code)` decodes each char's
  2 bytes on its own via `new String(bytes, UTF_16LE)`. A lone surrogate half
  decoded alone becomes U+FFFD; the two halves live in two separate
  `HWPCharNormal` objects, so they never get a chance to combine.
- **Fix (shipped):** one line — preserve the raw code unit instead of decoding
  per-half: `return String.valueOf((char) code);`. The halves stay intact and
  the StringBuilder that concatenates `getCh()` (in both hwplib's TextExtractor
  and hwp2hwpx's `ForChars`) reunites the surrogate pair into the right code
  point. We don't rebuild all of hwplib (its source needs the JDK-removed
  `javax.xml.bind.DatatypeConverter`); instead `scripts/patches/HWPCharNormal.java`
  is compiled against the resolved hwplib jar and its `.class` is overlaid into
  `vendor/hwp2hwpx.jar` by `scripts/bootstrap.sh`. Verified: patched output has
  FFFD=0 / U+F0854=2, byte-identical char encoding to the Hancom reference.
- **Status:** FIXED via the overlaid patch. Also submitted upstream:
  **neolord0/hwplib#306** (https://github.com/neolord0/hwplib/pull/306). A
  separate Hancom-PUA → standard Unicode map (U+F0854→『 U+300E) remains
  optional, for non-Hancom portability.

Template for further entries:

### <element> — <short description>
- **Source:** what the `.hwp` contains
- **After convert:** what the `.hwpx` shows
- **Severity:** lossless / cosmetic / structural / data-loss
- **Workaround:** if any
