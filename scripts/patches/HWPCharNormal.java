package kr.dogfoot.hwplib.object.bodytext.paragraph.text;

import java.io.UnsupportedEncodingException;

/**
 * Patched drop-in for hwplib's {@code HWPCharNormal} (kr.dogfoot:hwplib:1.1.10).
 *
 * <p>Upstream {@code intToString} decodes each 2-byte char on its own via
 * UTF-16LE. A supplementary-plane character is stored across two
 * {@code HWPCharNormal} entries (a UTF-16 surrogate pair); decoding each half
 * independently produces U+FFFD, so e.g. the Hancom PUA brackets 『 (U+F0854)
 * and 』 (U+F0855) come out as ◆◆. See docs/findings.md.
 *
 * <p>The one-line fix preserves the raw code unit (incl. lone surrogate halves)
 * so the assembling StringBuilder in hwplib/hwp2hwpx reunites the pair into the
 * correct code point. scripts/bootstrap.sh compiles this against the resolved
 * hwplib jar and overlays the resulting .class into vendor/hwp2hwpx.jar.
 *
 * <p>Identical to upstream except for {@code intToString}; intended for an
 * upstream PR to neolord0/hwplib.
 */
public class HWPCharNormal extends HWPChar {
    public HWPCharNormal() {
    }

    public HWPCharNormal(int code) {
        this.code = code;
    }

    @Override
    public HWPCharType getType() {
        return HWPCharType.Normal;
    }

    public String getCh() throws UnsupportedEncodingException {
        return intToString(code);
    }

    /**
     * Convert a 2-byte char code to a string, preserving lone surrogate halves
     * so surrogate pairs reassemble during text concatenation.
     */
    private String intToString(int code) {
        return String.valueOf((char) code);
    }

    public HWPChar clone() {
        HWPCharNormal cloned = new HWPCharNormal();
        cloned.code = code;
        return cloned;
    }

    @Override
    public int getCharSize() {
        return 1;
    }
}
