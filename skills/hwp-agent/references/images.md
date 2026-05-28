# Figure images — replace in place

The goal is narrow and safe: **swap the bytes of an existing figure** (a report's
chart/photo) without disturbing anything else. We do *not* insert new pics from
scratch — a pic carries layout (`<hp:sz>`/`<hp:imgRect>`), an alt-text comment, a
caption paragraph, and a `BinData` part wired through `content.hpf`; synthesizing
all of that correctly is brittle. Replacing the bytes of a slot the template
already has is reliable.

## Anatomy of a figure

A figure is an `<hp:pic>` whose `<hc:img binaryItemIDRef="imageN">` points at a
`BinData/imageN.<ext>` part. `Contents/content.hpf` maps that id to the href (and
thus the extension). The human caption (`[그림 III-NN] 제목`) is the text of the
pic's **own enclosing paragraph**, so a pic and its caption are 1:1. The
list-of-figures section has captions but **no pic**, so it never matches. Each pic
also carries an `<hp:shapeComment>` alt-text with the original filename and pixel
size, which `image list` surfaces.

`hwp-agent image list FILE.hwpx` → one row per slot: `ref`, slot format, original
px size, extent aspect ratio, caption. `--json` for an AI.

## The three rules (verified against a real report)

See the project memory `hwpx-image-replace-mechanism` for the byte-level evidence.

1. **Byte-only swap, container preserved.** Only the target `BinData` entry's bytes
   (plus the section XML / `content.hpf` when geometry or format change) are
   rewritten. The ZIP is re-emitted **entry-by-entry with the original `ZipInfo`,
   order, and compression**, `mimetype` first and `STORED`. A full re-zip (what
   `HwpxDocument.save_to_path` does) trips Hangul's 보안경고 even if the text is
   byte-identical — the difference Hangul flags is the *container*, not the content.

2. **Slot format = extension, and it must match.** The media-type is often
   `image/unknown`, so Hangul keys off the file extension: a `.png` slot needs PNG
   bytes, a `.bmp` slot needs BMP. `image replace` **refuses** a mismatch
   (`format_mismatch`, nothing written). (Changing a slot's format — rewriting the
   `content.hpf` href + extension and renaming the part — is possible but not yet
   exposed; re-encode the source image to the slot's format instead.)

3. **Display extent / aspect.** `<hp:sz>` / `<hp:orgSz>` / `<hp:imgRect>` is the box
   Hangul scales the image into. `--fit aspect` (default) keeps the box **width** and
   recomputes the **height** from the new image's pixel ratio, so it isn't stretched;
   `--fit none` leaves the box as-is. Pixel↔HWPUNIT ≈ ×75 (@96 dpi), used for the
   `imgDim`/`imgClip` native-size fields.

## Targeting & outcomes

Select with exactly one of `--ref imageN` (precise) or `--caption "<text>"` (exact
match first, then substring). A caption matching **more than one** pic returns
`ambiguous` (use `--ref`); matching none returns `unmatched`. A bad/undecodable
image file is `bad_image`; a missing path is `missing_file`. Output goes to `-o OUT`
or edits in place (via a temp file + atomic replace) — and **only when the swap
succeeds**, so a refusal never leaves a half-written file.

## Image size sniffing

`read_image_size` tries Pillow first (covers every format); without Pillow it parses
PNG / JPEG / BMP headers directly, so the common cases need no extra dependency.
`extent` (re-encoding to the slot's aspect) would need Pillow and is not implemented.
