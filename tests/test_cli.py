"""Integration tests for Photo Healer CLI (photo_healer.cli.main)."""

import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
import pytest

from photo_healer.cli.main import main, build_parser, format_size, render_table
from photo_healer.cli.i18n import set_language
from photo_healer.core.validator import JpegValidator
from tests.helpers import JPEGTestKit


@pytest.fixture(autouse=True)
def enforce_test_language(monkeypatch: pytest.MonkeyPatch):
    """Enforce English interface language for test assertions expecting English output."""
    monkeypatch.setenv("PHOTO_HEALER_LANG", "en")
    set_language("en")
    yield
    set_language(None)


# ── Synthetic Helpers ─────────────────────────────────────────────────────────
def create_healthy_jpeg(width: int = 320, height: int = 240) -> bytes:
    """Builds a minimal healthy JPEG."""
    entropy_payload = bytes((i * 137 + 23) % 256 for i in range(1024)) + JPEGTestKit.EOI
    return JPEGTestKit.minimal_donor_header(width=width, height=height) + entropy_payload


def create_candidate_jpeg(width: int = 320, height: int = 240, zero_prefix_len: int = 1024) -> bytes:
    """Builds a simulated TRIM-damaged JPEG candidate (zero prefix followed by entropy)."""
    entropy_payload = bytes((i * 137 + 23) % 256 for i in range(4096)) + JPEGTestKit.EOI
    return (b"\x00" * zero_prefix_len) + entropy_payload


def create_jpeg_with_exif_thumb(thumb_bytes: bytes, width: int = 320, height: int = 240) -> bytes:
    """Constructs a valid JPEG with an embedded EXIF thumbnail in APP1 IFD1."""
    endian = "<"
    tiff_header = b"II" + struct.pack(endian + "HI", 42, 8)
    num_entries_ifd0 = 1
    ifd0_entry1 = struct.pack(endian + "HHII", 0x010E, 2, 4, 0x41424300)
    ifd1_offset = 8 + 2 + 12 + 4
    next_ifd_ptr = struct.pack(endian + "I", ifd1_offset)

    num_entries_ifd1 = 2
    thumb_offset = ifd1_offset + 30
    thumb_len = len(thumb_bytes)
    tag_0201 = struct.pack(endian + "HHII", 0x0201, 4, 1, thumb_offset)
    tag_0202 = struct.pack(endian + "HHII", 0x0202, 4, 1, thumb_len)
    ifd1_next = struct.pack(endian + "I", 0)

    tiff_body = (
        tiff_header
        + struct.pack(endian + "H", num_entries_ifd0)
        + ifd0_entry1
        + next_ifd_ptr
        + struct.pack(endian + "H", num_entries_ifd1)
        + tag_0201
        + tag_0202
        + ifd1_next
        + thumb_bytes
    )
    exif_payload = b"Exif\x00\x00" + tiff_body
    seg_len = len(exif_payload) + 2
    app1 = b"\xff\xe1" + struct.pack(">H", seg_len) + exif_payload

    # Minimal donor with APP1 injected
    return (
        JPEGTestKit.SOI
        + app1
        + JPEGTestKit.dqt(table_id=0)
        + JPEGTestKit.sof0(width=width, height=height)
        + JPEGTestKit.dht(table_class=0, table_id=0)
        + JPEGTestKit.sos()
        + b"\x00" * 32
        + JPEGTestKit.EOI
    )


# ── Test Suite ────────────────────────────────────────────────────────────────
class TestCliGeneral:
    """Tests for CLI arguments, parser construction, and utilities."""

    def test_no_args_prints_help_and_exits_code_1(self, capsys):
        code = main([])
        assert code == 1
        captured = capsys.readouterr()
        assert "usage: photo-healer" in captured.out or "usage: photo-healer" in captured.err

    def test_version_flag_raises_system_exit_0(self):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0

    def test_format_size_utility(self):
        assert format_size(500) == "500 B"
        assert format_size(2048) == "2.00 KB"
        assert format_size(1048576) == "1.00 MB"
        assert format_size(1073741824) == "1.00 GB"

    def test_render_table_utility(self):
        headers = ["Col A", "Col B"]
        rows = [["Val 1", "Val 2"], ["Long Value", "123"]]
        table = render_table("TEST SUMMARY", headers, rows)
        assert "TEST SUMMARY" in table
        assert "Col A" in table
        assert "Val 1" in table


class TestCliTriage:
    """Tests for photo-healer triage subcommand."""

    @pytest.fixture
    def triage_dir(self, tmp_path):
        scan_dir = tmp_path / "archive"
        scan_dir.mkdir()

        # 1. Valid healthy photo
        (scan_dir / "valid_01.jpg").write_bytes(create_healthy_jpeg())

        # 2. TRIM zero file (100% 0x00)
        (scan_dir / "trim_zero_01.jpg").write_bytes(b"\x00" * 65536)

        # 3. Healed candidate (1024 zero bytes prefix + healthy tail)
        (scan_dir / "candidate_01.jpg").write_bytes(create_candidate_jpeg(zero_prefix_len=1024))

        # 4. Empty file
        (scan_dir / "empty_01.jpg").write_bytes(b"")

        # 5. Non-image other file
        (scan_dir / "random.jpg").write_bytes(b"NON_JPEG_DATA_HERE_12345")

        return scan_dir

    def test_triage_basic_scan_and_report(self, triage_dir, tmp_path):
        report_file = tmp_path / "out_report.json"
        code = main(["triage", str(triage_dir), "--report", str(report_file)])
        assert code == 0
        assert report_file.exists()

        records = json.loads(report_file.read_text(encoding="utf-8"))
        assert len(records) == 5

        statuses = {r["name"]: r["status"] for r in records}
        assert statuses["valid_01.jpg"] == "valid"
        assert statuses["trim_zero_01.jpg"] == "trim_zero"
        assert statuses["candidate_01.jpg"] == "healed_candidate"
        assert statuses["empty_01.jpg"] == "empty"
        assert statuses["random.jpg"] == "other"

        # Shortlist check
        cand_shortlist = tmp_path / "heal_candidates.json"
        assert cand_shortlist.exists()
        cands = json.loads(cand_shortlist.read_text(encoding="utf-8"))
        assert len(cands) == 1
        assert cands[0]["name"] == "candidate_01.jpg"

    def test_triage_quarantine_dry_run(self, triage_dir, tmp_path):
        q_dir = tmp_path / "quarantine"
        code = main(["triage", str(triage_dir), "--quarantine", str(q_dir), "--dry-run"])
        assert code == 0
        # Under dry-run, files must not be moved
        assert (triage_dir / "trim_zero_01.jpg").exists()
        assert not (q_dir / "trim_zero_01.jpg").exists()

    def test_triage_quarantine_execution(self, triage_dir, tmp_path):
        q_dir = tmp_path / "quarantine"
        code = main(["triage", str(triage_dir), "--quarantine", str(q_dir)])
        assert code == 0
        # TRIM-zero file was moved to quarantine
        assert not (triage_dir / "trim_zero_01.jpg").exists()
        assert (q_dir / "trim_zero_01.jpg").exists()
        # Candidate and valid files remain intact
        assert (triage_dir / "valid_01.jpg").exists()
        assert (triage_dir / "candidate_01.jpg").exists()

    def test_triage_invalid_dir_fails(self, tmp_path):
        code = main(["triage", str(tmp_path / "non_existent")])
        assert code == 1

    def test_triage_quiet_mode(self, triage_dir, capsys):
        code = main(["triage", str(triage_dir), "--quiet"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Auditing files" not in captured.out
        assert "PHOTO HEALER" not in captured.out


class TestCliHeal:
    """Tests for photo-healer heal subcommand."""

    @pytest.fixture
    def heal_files(self, tmp_path):
        donor = tmp_path / "donor.jpg"
        donor.write_bytes(create_healthy_jpeg(width=640, height=480))

        broken = tmp_path / "broken.jpg"
        broken.write_bytes(create_candidate_jpeg(width=640, height=480, zero_prefix_len=1024))

        zero_file = tmp_path / "pure_zero.jpg"
        zero_file.write_bytes(b"\x00" * 4096)

        return donor, broken, zero_file

    def test_heal_single_file_default_output(self, heal_files):
        donor, broken, _ = heal_files
        code = main(["heal", str(broken), "--donor", str(donor)])
        assert code == 0

        healed_file = broken.parent / f"{broken.stem}_HEALED.jpg"
        assert healed_file.exists()
        val = JpegValidator.validate(healed_file)
        assert val.is_valid
        assert val.width == 640
        assert val.height == 480

    def test_heal_custom_output(self, heal_files, tmp_path):
        donor, broken, _ = heal_files
        dest = tmp_path / "custom_out" / "repaired.jpg"
        code = main(["heal", str(broken), "--donor", str(donor), "--output", str(dest)])
        assert code == 0
        assert dest.exists()
        val = JpegValidator.validate(dest)
        assert val.is_valid

    def test_heal_dry_run(self, heal_files):
        donor, broken, _ = heal_files
        code = main(["heal", str(broken), "--donor", str(donor), "--dry-run"])
        assert code == 0
        healed_file = broken.parent / f"{broken.stem}_HEALED.jpg"
        assert not healed_file.exists()

    def test_heal_careful_overwrite_guard(self, heal_files, capsys):
        donor, broken, _ = heal_files
        healed_file = broken.parent / f"{broken.stem}_HEALED.jpg"
        healed_file.write_bytes(b"OLD_CONTENT")

        # Without --force: must fail and refuse to overwrite
        code = main(["heal", str(broken), "--donor", str(donor)])
        assert code == 1
        assert healed_file.read_bytes() == b"OLD_CONTENT"
        captured = capsys.readouterr()
        assert "already exists" in captured.err

        # With --force: overwrites successfully
        code_force = main(["heal", str(broken), "--donor", str(donor), "--force"])
        assert code_force == 0
        val = JpegValidator.validate(healed_file)
        assert val.is_valid

    def test_heal_inplace_with_backup(self, heal_files):
        donor, broken, _ = heal_files
        orig_content = broken.read_bytes()

        code = main(["heal", str(broken), "--donor", str(donor), "--inplace"])
        assert code == 0

        bak_file = broken.with_suffix(".jpg.bak")
        assert bak_file.exists()
        assert bak_file.read_bytes() == orig_content

        # Original is now healed
        val = JpegValidator.validate(broken)
        assert val.is_valid

    def test_heal_trim_zero_file_fails(self, heal_files, capsys):
        donor, _, zero_file = heal_files
        code = main(["heal", str(zero_file), "--donor", str(donor)])
        assert code == 1
        captured = capsys.readouterr()
        assert "No live entropy data" in captured.err

    def test_heal_missing_files_fail(self, heal_files):
        donor, broken, _ = heal_files
        missing = donor.parent / "non_existent.jpg"
        assert main(["heal", str(broken), "--donor", str(missing)]) == 1
        assert main(["heal", str(missing), "--donor", str(donor)]) == 1


class TestCliBatchHeal:
    """Tests for photo-healer batch-heal subcommand."""

    @pytest.fixture
    def batch_dir(self, tmp_path):
        folder = tmp_path / "batch_photos"
        folder.mkdir()

        # Donor photo
        (folder / "healthy_donor.jpg").write_bytes(create_healthy_jpeg(width=800, height=600))

        # Two candidates
        (folder / "cand_01.jpg").write_bytes(create_candidate_jpeg(width=800, height=600, zero_prefix_len=512))
        (folder / "cand_02.jpg").write_bytes(create_candidate_jpeg(width=800, height=600, zero_prefix_len=1024))

        return folder

    def test_batch_heal_auto_donor(self, batch_dir):
        code = main(["batch-heal", str(batch_dir), "--auto-donor"])
        assert code == 0

        healed1 = batch_dir / "cand_01_HEALED.jpg"
        healed2 = batch_dir / "cand_02_HEALED.jpg"
        assert healed1.exists()
        assert healed2.exists()
        assert JpegValidator.validate(healed1).is_valid
        assert JpegValidator.validate(healed2).is_valid

    def test_batch_heal_explicit_donor(self, batch_dir, tmp_path):
        ext_donor = tmp_path / "external_donor.jpg"
        ext_donor.write_bytes(create_healthy_jpeg(width=1024, height=768))

        code = main(["batch-heal", str(batch_dir), "--donor", str(ext_donor)])
        assert code == 0
        healed1 = batch_dir / "cand_01_HEALED.jpg"
        assert healed1.exists()
        assert JpegValidator.validate(healed1).is_valid

    def test_batch_heal_dry_run(self, batch_dir):
        code = main(["batch-heal", str(batch_dir), "--auto-donor", "--dry-run"])
        assert code == 0
        assert not (batch_dir / "cand_01_HEALED.jpg").exists()
        assert not (batch_dir / "cand_02_HEALED.jpg").exists()

    def test_batch_heal_output_dir(self, batch_dir, tmp_path):
        out_dir = tmp_path / "restored"
        code = main(["batch-heal", str(batch_dir), "--auto-donor", "--output", str(out_dir)])
        assert code == 0
        assert (out_dir / "cand_01_HEALED.jpg").exists()
        assert (out_dir / "cand_02_HEALED.jpg").exists()

    def test_batch_heal_careful_guard(self, batch_dir):
        healed1 = batch_dir / "cand_01_HEALED.jpg"
        healed1.write_bytes(b"EXISTING_HEALED")

        # Without --force: should skip existing without failure
        code = main(["batch-heal", str(batch_dir), "--auto-donor"])
        assert code == 0
        assert healed1.read_bytes() == b"EXISTING_HEALED"

        # With --force: overwrites
        code_force = main(["batch-heal", str(batch_dir), "--auto-donor", "--force"])
        assert code_force == 0
        assert JpegValidator.validate(healed1).is_valid

    def test_batch_heal_no_candidates(self, tmp_path):
        empty_dir = tmp_path / "no_candidates"
        empty_dir.mkdir()
        code = main(["batch-heal", str(empty_dir)])
        assert code == 0


class TestCliQuarantine:
    """Tests for photo-healer quarantine subcommand."""

    @pytest.fixture
    def quarantine_setup(self, tmp_path):
        archive = tmp_path / "archive"
        archive.mkdir()

        f1 = archive / "zero1.jpg"
        f1.write_bytes(b"\x00" * 4096)
        f2 = archive / "sub" / "zero2.jpg"
        f2.parent.mkdir()
        f2.write_bytes(b"\x00" * 8192)

        report_file = tmp_path / "triage_report.json"
        report_data = [
            {"path": str(f1), "name": f1.name, "status": "trim_zero", "size": 4096},
            {"path": str(f2), "name": f2.name, "status": "trim_zero", "size": 8192},
            {"path": str(archive / "intact.jpg"), "name": "intact.jpg", "status": "valid", "size": 2048},
        ]
        report_file.write_text(json.dumps(report_data), encoding="utf-8")
        return report_file, f1, f2

    def test_quarantine_relocation(self, quarantine_setup, tmp_path):
        report_file, f1, f2 = quarantine_setup
        dest_dir = tmp_path / "quarantined"

        code = main(["quarantine", "--report", str(report_file), "--dest", str(dest_dir)])
        assert code == 0

        assert not f1.exists()
        assert not f2.exists()
        assert (dest_dir / f1.name).exists() or any(dest_dir.rglob(f1.name))

    def test_quarantine_dry_run(self, quarantine_setup, tmp_path):
        report_file, f1, f2 = quarantine_setup
        dest_dir = tmp_path / "quarantined_dry"

        code = main(["quarantine", "--report", str(report_file), "--dest", str(dest_dir), "--dry-run"])
        assert code == 0
        assert f1.exists()
        assert f2.exists()

    def test_quarantine_missing_report(self, tmp_path):
        missing = tmp_path / "non_existent_report.json"
        dest = tmp_path / "q"
        assert main(["quarantine", "--report", str(missing), "--dest", str(dest)]) == 1


class TestCliCarve:
    """Tests for photo-healer carve subcommand."""

    @pytest.fixture
    def carve_setup(self, tmp_path):
        thumb_bytes = create_healthy_jpeg(width=160, height=120)
        img_with_thumb = tmp_path / "photo_with_thumb.jpg"
        img_with_thumb.write_bytes(create_jpeg_with_exif_thumb(thumb_bytes, width=640, height=480))

        img_plain = tmp_path / "plain_photo.jpg"
        img_plain.write_bytes(create_healthy_jpeg(width=320, height=240))

        return img_with_thumb, img_plain

    def test_carve_single_file_exif_thumb(self, carve_setup):
        img_with_thumb, _ = carve_setup
        code = main(["carve", str(img_with_thumb)])
        assert code == 0

        previews_dir = img_with_thumb.parent / "_Previews"
        assert previews_dir.exists()
        carved_files = list(previews_dir.glob("*.jpg"))
        assert len(carved_files) >= 1
        assert JpegValidator.validate(carved_files[0]).is_valid

    def test_carve_dry_run(self, carve_setup):
        img_with_thumb, _ = carve_setup
        code = main(["carve", str(img_with_thumb), "--dry-run"])
        assert code == 0
        previews_dir = img_with_thumb.parent / "_Previews"
        assert not previews_dir.exists()

    def test_carve_custom_dest_and_careful_guard(self, carve_setup, tmp_path):
        img_with_thumb, _ = carve_setup
        out_dir = tmp_path / "extracted_thumbs"

        code = main(["carve", str(img_with_thumb), "--dest", str(out_dir)])
        assert code == 0
        extracted = list(out_dir.glob("*.jpg"))
        assert len(extracted) == 1

        # Overwrite without --force: must fail
        code_fail = main(["carve", str(img_with_thumb), "--dest", str(out_dir)])
        assert code_fail == 1

        # Overwrite with --force: succeeds
        code_force = main(["carve", str(img_with_thumb), "--dest", str(out_dir), "--force"])
        assert code_force == 0

    def test_carve_directory(self, carve_setup, tmp_path):
        img_with_thumb, img_plain = carve_setup
        target_dir = tmp_path / "carve_dir"
        target_dir.mkdir()
        shutil.copy2(img_with_thumb, target_dir / "pic1.jpg")
        shutil.copy2(img_plain, target_dir / "pic2.jpg")

        code = main(["carve", str(target_dir)])
        assert code == 0
        previews = list((target_dir / "_Previews").glob("*.jpg"))
        assert len(previews) >= 1

    def test_carve_missing_file_fails(self, tmp_path):
        missing = tmp_path / "does_not_exist.jpg"
        assert main(["carve", str(missing)]) == 1


class TestCliSubprocessExecutable:
    """Verifies installed console script executable in child process."""

    def test_executable_help(self):
        script_path = Path(sys.executable).parent / "Scripts" / "photo-healer.exe"
        if not script_path.exists():
            pytest.skip("photo-healer.exe not found in Scripts")

        proc = subprocess.run(
            [str(script_path), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PHOTO_HEALER_LANG": "en"},
            stdin=subprocess.DEVNULL,
        )
        assert proc.returncode == 0
        assert "Photo Healer" in proc.stdout
        assert "Forensic repair tool" in proc.stdout or "Инструмент" in proc.stdout
        assert "triage" in proc.stdout
        assert "heal" in proc.stdout
        assert "batch-heal" in proc.stdout
        assert "quarantine" in proc.stdout
        assert "carve" in proc.stdout
