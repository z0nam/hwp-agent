"""Tests for the output overwrite-guard (embedded provenance fingerprint)."""

import zipfile

from hwp_agent.ops.guard import (
    FINGERPRINT_PART,
    current_content_hash,
    is_ours_untouched,
    next_versioned_path,
    plan_output,
    read_stored_fingerprint,
    stamp_fingerprint,
)


def _make_pkg(path, parts=None):
    parts = parts or {"mimetype": b"application/hwp+zip", "Contents/section0.xml": b"<p>hi</p>"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)


def test_new_file_writes_to_path(tmp_path):
    out = tmp_path / "a.hwpx"
    gr = plan_output(out)
    assert gr.target == out and not gr.versioned and gr.reason == "new"


def test_stamp_then_recognized_as_own(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)
    assert stamp_fingerprint(out) is True
    assert read_stored_fingerprint(out) == current_content_hash(out)
    assert is_ours_untouched(out)
    gr = plan_output(out)
    assert gr.target == out and not gr.versioned and gr.reason == "overwrite-own"


def test_external_edit_drift_versions(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)
    stamp_fingerprint(out)
    # simulate a Hangul edit: rewrite a content part (keeps the stale fp)
    _make_pkg(out, {"mimetype": b"application/hwp+zip", "Contents/section0.xml": b"<p>EDITED</p>"})
    assert not is_ours_untouched(out)
    gr = plan_output(out)
    assert gr.versioned and gr.reason == "versioned-foreign"  # rewrite dropped the fp
    assert gr.target == tmp_path / "a_v2.hwpx"


def test_drift_with_kept_fingerprint(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)
    stamp_fingerprint(out)
    # keep the fp part but change another part → stored fp no longer matches content
    with zipfile.ZipFile(out) as zf:
        parts = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    parts["Contents/section0.xml"] = b"<p>changed by hand</p>"
    _make_pkg(out, parts)
    assert read_stored_fingerprint(out) is not None  # fp still present
    assert not is_ours_untouched(out)  # but content drifted
    gr = plan_output(out)
    assert gr.versioned and gr.reason == "versioned-drift"


def test_foreign_file_versions(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)  # never stamped → no provenance
    gr = plan_output(out)
    assert gr.versioned and gr.reason == "versioned-foreign"
    assert gr.target == tmp_path / "a_v2.hwpx"


def test_force_overwrites(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)  # foreign
    gr = plan_output(out, force=True)
    assert gr.target == out and not gr.versioned


def test_next_versioned_skips_existing(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)
    (tmp_path / "a_v2.hwpx").write_bytes(b"x")
    assert next_versioned_path(out) == tmp_path / "a_v3.hwpx"


def test_restamp_keeps_content_hash(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)
    stamp_fingerprint(out)
    h1 = read_stored_fingerprint(out)
    stamp_fingerprint(out)  # re-stamp must not change the content hash
    assert read_stored_fingerprint(out) == h1
    assert is_ours_untouched(out)


def test_non_zip_is_foreign(tmp_path):
    out = tmp_path / "a.hwpx"
    out.write_bytes(b"not a zip")
    assert stamp_fingerprint(out) is False
    assert read_stored_fingerprint(out) is None
    gr = plan_output(out)
    assert gr.versioned and gr.reason == "versioned-foreign"


def test_fingerprint_part_excluded_from_hash(tmp_path):
    out = tmp_path / "a.hwpx"
    _make_pkg(out)
    h_before = current_content_hash(out)
    stamp_fingerprint(out)
    # adding the fp part must not change the content hash
    assert current_content_hash(out) == h_before
    assert FINGERPRINT_PART in {i for i in zipfile.ZipFile(out).namelist()}
