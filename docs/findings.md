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

_None recorded yet._ Template for entries:

### <element> — <short description>
- **Source:** what the `.hwp` contains
- **After convert:** what the `.hwpx` shows
- **Severity:** lossless / cosmetic / structural / data-loss
- **Workaround:** if any
