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

Template for further entries:

### <element> — <short description>
- **Source:** what the `.hwp` contains
- **After convert:** what the `.hwpx` shows
- **Severity:** lossless / cosmetic / structural / data-loss
- **Workaround:** if any
