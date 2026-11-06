// Writes a blank .hwp with hwplib (bundled in hwp2hwpx.jar), so tests can run a real
// conversion without a committed sample. Run as a single-file source program (JDK 11+):
//   java -cp hwp2hwpx.jar tests/data/MakeBlankHwp.java out.hwp
import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.tool.blankfilemaker.BlankFileMaker;
import kr.dogfoot.hwplib.writer.HWPWriter;

public class MakeBlankHwp {
    public static void main(String[] args) throws Exception {
        HWPFile hwp = BlankFileMaker.make();
        HWPWriter.toFile(hwp, args[0]);
    }
}
