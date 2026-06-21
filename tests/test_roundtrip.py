"""Tests for the sds100 codec, model, and edit operations.

The fixtures are the user's real exported lists; if they are not present the
data-dependent tests are skipped so the suite still runs anywhere.
"""

import glob
import os

import pytest

from sds100 import codec, schema, firmware as fw_mod, scanner as scanner_mod
from sds100.format import hz_to_mhz, mhz_to_hz
from sds100.model import FavoritesList

DATA_DIR = os.path.expanduser(
    "~/OneDrive/Documents/Radio/Uniden Sentinel SDS100")
HPE_FILES = sorted(glob.glob(os.path.join(DATA_DIR, "*.hpe")))
needs_data = pytest.mark.skipif(not HPE_FILES, reason="no .hpe fixtures present")


def test_scramble_is_involution():
    blob = bytes(range(256)) * 4
    assert codec._scramble(codec._scramble(blob)) == blob


def test_codec_text_roundtrip():
    text = ("TargetModel\tBCDx36HP\r\nFormatVersion\t1.00\r\n"
            "File\tHomePatrol Export File\r\n")
    assert codec.decode(codec.encode(text)) == text


def test_freq_helpers():
    assert hz_to_mhz("462550000") == "462.55"
    assert hz_to_mhz("26965000") == "26.965"
    assert hz_to_mhz("0") == ""
    assert mhz_to_hz("462.6750") == 462675000
    assert mhz_to_hz("154.43") == 154430000


def test_tone_normalization():
    assert schema.normalize_tone("100.0") == "TONE=C100.0"
    assert schema.normalize_tone("C156.7") == "TONE=C156.7"
    assert schema.normalize_tone("CTCSS 141.3") == "TONE=C141.3"
    assert schema.normalize_tone("D023") == "TONE=D023"
    assert schema.normalize_tone("DCS 23") == "TONE=D023"
    assert schema.normalize_tone("NAC=293") == "NAC=293"
    assert schema.normalize_tone("NAC 293") == "NAC=293"
    assert schema.normalize_tone("CC 1") == "ColorCode=1"
    assert schema.normalize_tone("") == ""
    assert schema.normalize_tone("off") == ""


def test_service_type_resolution():
    assert schema.resolve_service_type("Ham") == "13"
    assert schema.resolve_service_type("other") == "21"
    assert schema.resolve_service_type("16") == "16"
    assert schema.resolve_service_type("") == "21"
    assert schema.service_type_name("3") == "Fire Dispatch"
    with pytest.raises(ValueError):
        schema.resolve_service_type("Nonsense")


@needs_data
@pytest.mark.parametrize("path", HPE_FILES, ids=lambda p: os.path.basename(p))
def test_file_roundtrip_byte_identical(path):
    inner = codec.read(path)
    fav = FavoritesList.parse(inner)
    assert fav.to_text() == inner
    # full codec re-encode must decode back to the same text
    assert codec.decode(codec.encode(fav.to_text())) == inner


@needs_data
def test_add_channel_arity_and_stability(tmp_path):
    src = next(p for p in HPE_FILES if "Amateur.hpe" in p and "DMR" not in p)
    fav = FavoritesList.parse(codec.read(src))
    conv = next(s for s in fav.systems if s.tag == "Conventional")
    group = fav.groups(conv)[0]
    rec = fav.add_channel(group, "Test Ch", mhz_to_hz("146.52"),
                          modulation="NFM", tone="100.0", service_type="Ham")
    assert len(rec.fields) == schema.ARITY["C-Freq"]
    assert rec.get("freq") == "146520000"
    assert rec.get("tone") == "TONE=C100.0"
    assert rec.get("service_type") == "13"
    # reparse stability
    text = fav.to_text()
    assert FavoritesList.parse(text).to_text() == text


@needs_data
def test_remove_and_avoid(tmp_path):
    src = next(p for p in HPE_FILES if "Amateur.hpe" in p and "DMR" not in p)
    fav = FavoritesList.parse(codec.read(src))
    conv = next(s for s in fav.systems if s.tag == "Conventional")
    group = fav.groups(conv)[0]
    before = len(list(group.of("C-Freq")))
    victim = next(group.of("C-Freq"))
    victim.set_avoid(True)
    assert victim.avoided
    assert fav.remove(victim)
    assert len(list(group.of("C-Freq"))) == before - 1


SYS_TEXT = ("TargetModel\tBCDx36HP\r\nFormatVersion\t1.00\r\n"
            "Conventional\t\t\tTest Sys\tOff\t\tConventional\tOff\tOff\t0\t"
            "Off\tOff\t400\tAuto\t8\r\n")


def _fake_card(tmp_path):
    """Build a minimal but realistic SDS100 card layout under tmp_path."""
    fav_dir = tmp_path / "NO NAME" / scanner_mod.FAVORITES_SUBDIR
    fav_dir.mkdir(parents=True)
    flags = "\t".join(["Off"] * scanner_mod.N_FLIST_FLAGS)
    (fav_dir / scanner_mod.INDEX_FILE).write_text(
        "TargetModel\tBCDx36HP\r\nFormatVersion\t1.00\r\n"
        f"F-List\tTest Sys\tf_000001.hpd\t{flags}\r\n", newline="")
    (fav_dir / "f_000001.hpd").write_text(SYS_TEXT, newline="")
    return scanner_mod.Scanner(str(tmp_path / "NO NAME"))


def test_scanner_detect_and_index(tmp_path):
    _fake_card(tmp_path)
    scanners = scanner_mod.detect(str(tmp_path))
    assert len(scanners) == 1
    _, entries = scanners[0].read_index()
    assert [e.name for e in entries] == ["Test Sys"]
    fav = scanner_mod.read_hpd(scanners[0].hpd_files()[0])
    assert fav.systems[0].name == "Test Sys"


def test_push_overwrite_leaves_index(tmp_path):
    s = _fake_card(tmp_path)
    fav = scanner_mod.read_hpd(s.hpd_files()[0])
    _, before = s.read_index()
    res = scanner_mod.push(s, fav, "Test Sys", backup=False)
    assert res.replaced and res.filename == "f_000001.hpd"
    _, after = s.read_index()
    assert len(after) == len(before)  # no new index row
    # written .hpd carries no export signature
    raw = open(s.hpd_files()[0], encoding="utf-8", newline="").read()
    assert schema.named  # sanity import
    assert "HomePatrol Export File" not in raw


def _fake_fw_card(tmp_path):
    root = tmp_path / "BCDx36HP"
    (root / "firmware").mkdir(parents=True)
    (root / "scanner.inf").write_text(
        "TargetModel\tBCDx36HP\r\nFormatVersion\t1.00\r\n"
        "Scanner\tSDS100\t38326-X\t1.21.00\t01\t\t1.00.00\t1.00.00\t0\t1.02.01\r\n",
        newline="")
    (root / "firmware" / "CityTable_V1_00_00.dat").write_text("START_CITY")
    (root / "firmware" / "ZipTable_V1_00_00.dat").write_text("START_ZIP")
    return str(root)


def test_fw_installed_info(tmp_path):
    root = _fake_fw_card(tmp_path)
    info = fw_mod.installed_info(root)
    assert info["model"] == "SDS100"
    assert info["main"] == "1.21.00"
    assert info["sub"] == "1.02.01"


def test_fw_stage_main_and_sub_preserves_dat(tmp_path):
    root = _fake_fw_card(tmp_path)
    fwd = os.path.join(root, "firmware")
    open(os.path.join(fwd, "SDS-100_V1_22_00.bin"), "wb").write(b"old")  # prior main
    images = [
        fw_mod.FirmwareImage("SDS-100_V1_26_01.bin", b"main", "main", "SDS100", "1.26.01"),
        fw_mod.FirmwareImage("SDS-100-SUB_V1_03_15.firm", b"sub", "sub", "SDS100", "1.03.15"),
    ]
    res = fw_mod.stage(root, images, backup=False)
    files = set(os.listdir(fwd))
    assert {"SDS-100_V1_26_01.bin", "SDS-100-SUB_V1_03_15.firm"} <= files  # both staged
    assert "SDS-100_V1_22_00.bin" not in files                            # old cleared
    assert "SDS-100_V1_22_00.bin" in res.cleared
    assert {"CityTable_V1_00_00.dat", "ZipTable_V1_00_00.dat"} <= files    # .dat kept


def test_fw_load_images_combined_zip(tmp_path):
    import zipfile
    z = tmp_path / "SDS100_V1_23_15_Main_Sub.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("SDS100 V1.23.15/Readme.txt", "Model: UNIDEN SDS100\n")
        zf.writestr("SDS100 V1.23.15/SDS-100_V1_23_15.bin", b"\x91\xf0main")
        zf.writestr("SDS100 V1.23.15/SDS-100-SUB_V1_03_06.firm", b"sub")
    images = fw_mod.load_images(str(z))
    by = {i.kind: i for i in images}
    assert by["main"].name == "SDS-100_V1_23_15.bin" and by["main"].version == "1.23.15"
    assert by["sub"].name == "SDS-100-SUB_V1_03_06.firm" and by["sub"].version == "1.03.06"
    assert all(i.model == "SDS100" for i in images)


def test_push_new_appends_index(tmp_path):
    s = _fake_card(tmp_path)
    fav = scanner_mod.read_hpd(s.hpd_files()[0])
    res = scanner_mod.push(s, fav, "Brand New", backup=False)
    assert not res.replaced and res.filename == "f_000002.hpd"
    _, after = s.read_index()
    assert [e.name for e in after] == ["Test Sys", "Brand New"]
    new = next(e for e in after if e.name == "Brand New")
    assert len(new.flags) == scanner_mod.N_FLIST_FLAGS
