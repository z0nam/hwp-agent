import kr.dogfoot.hwp2hwpx.Hwp2Hwpx;
import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.reader.HWPReader;
import kr.dogfoot.hwpxlib.object.HWPXFile;
import kr.dogfoot.hwpxlib.writer.HWPXWriter;

/**
 * Thin command-line entry point around neolord0/hwp2hwpx, which ships as a
 * library with no main method. Bundled into vendor/hwp2hwpx.jar by
 * scripts/bootstrap.sh and invoked from the Python Hwp2HwpxBackend as:
 *
 *     java -jar vendor/hwp2hwpx.jar <input.hwp> <output.hwpx>
 */
public final class Hwp2HwpxCli {

    private Hwp2HwpxCli() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: Hwp2HwpxCli <input.hwp> <output.hwpx>");
            System.exit(2);
        }

        String inputPath = args[0];
        String outputPath = args[1];

        HWPFile fromFile = HWPReader.fromFile(inputPath);
        if (fromFile == null) {
            System.err.println("error: could not read HWP file: " + inputPath);
            System.exit(1);
        }

        HWPXFile toFile = Hwp2Hwpx.toHWPX(fromFile);
        HWPXWriter.toFilepath(toFile, outputPath);
    }
}
