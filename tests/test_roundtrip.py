"""Tests for the sds100 codec, model, and edit operations.

The fixtures are the user's real exported lists; if they are not present the
data-dependent tests are skipped so the suite still runs anywhere.
"""

import glob
import os

import pytest

from sds100 import codec, schema, scanner as scanner_mod
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


def test_scanner_detect_roundtrip(tmp_path):
    # Build a fake scanner volume and confirm detect + read_hpd work.
    vol = tmp_path / "SDS100"
    fav_dir = vol / scanner_mod.FAVORITES_SUBDIR
    fav_dir.mkdir(parents=True)
    (fav_dir / scanner_mod.INDEX_FILE).write_text("")
    text = ("TargetModel\tBCDx36HP\r\nFormatVersion\t1.00\r\n"
            "Conventional\t\t\tTest Sys\tOff\t\tConventional\tOff\tOff\t0\t"
            "Off\tOff\t400\tAuto\t8\r\n")
    (fav_dir / "1_Test.hpd").write_text(text)
    scanners = scanner_mod.detect(str(tmp_path))
    assert len(scanners) == 1
    fav = scanner_mod.read_hpd(scanners[0].hpd_files()[0])
    assert fav.systems[0].name == "Test Sys"
