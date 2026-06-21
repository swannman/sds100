"""SDS100 firmware staging.

Firmware updates on the SDS100 are file-based: a single encrypted ``.bin`` is
placed in ``BCDx36HP/firmware/`` on the microSD card, and the scanner flashes
itself when you unmount and hold the power key. Sentinel does exactly this; so
does this module.

Safety rules enforced here (from Uniden's update Readme):
* Only **one** firmware version may be on the card at a time -- any existing
  firmware binary is cleared before staging the new one.
* The ``CityTable_*.dat`` / ``ZipTable_*.dat`` files in ``firmware/`` are
  **not** firmware and must never be removed (the scanner won't boot without
  them); they are always preserved.
* The firmware model must match the connected scanner (an SDS200 image on an
  SDS100 would be harmful) -- staging refuses on mismatch unless forced.

This module never flashes anything; it only stages the file. The scanner
performs the actual update. Always keep the backup it makes.
"""

from __future__ import annotations

import os
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Optional

WIKI_URL = "https://info.uniden.com/twiki/bin/view/UnidenMan4/SDS100FirmwareUpdate"
PUB_BASE = "https://info.uniden.com"
# Firmware binaries carry these extensions; everything else in firmware/ (the
# .dat lookup tables) is left untouched.
FIRMWARE_EXTS = (".bin", ".firm")
USER_AGENT = "Mozilla/5.0 (sds100-cli)"


@dataclass
class Release:
    name: str        # e.g. "SDS100 V1.23.20 Main"
    version: str     # e.g. "1.23.20"
    url: str

    @property
    def is_main(self) -> bool:
        return "main" in self.name.lower()


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# --------------------------------------------------------------- card inspection
def scanner_inf_path(card_root: str) -> str:
    return os.path.join(card_root, "scanner.inf")


def installed_info(card_root: str) -> dict:
    """Parse model + firmware versions from the card's scanner.inf."""
    path = scanner_inf_path(card_root)
    info = {"model": "?", "main": "?", "sub": "?"}
    if not os.path.exists(path):
        return info
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for line in fh.read().split("\r\n"):
            p = line.split("\t")
            if p and p[0] == "Scanner" and len(p) >= 4:
                info["model"] = p[1]
                info["main"] = p[3].strip()
                info["sub"] = p[-1].strip()
    return info


def firmware_dir(card_root: str) -> str:
    return os.path.join(card_root, "firmware")


def staged_firmware(card_root: str) -> list[str]:
    """Firmware binaries currently staged in firmware/ (excludes .dat tables)."""
    d = firmware_dir(card_root)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d)
                  if f.lower().endswith(FIRMWARE_EXTS))


# ------------------------------------------------------------------ wiki listing
def list_available() -> list[Release]:
    """Scrape the Uniden wiki page for downloadable SDS100 firmware zips."""
    html = _get(WIKI_URL).decode("utf-8", "replace")
    rels = []
    for href in re.findall(r'href="(/twiki/pub/[^"]*SDS100[^"]*\.zip)"', html):
        name = urllib.parse.unquote(os.path.splitext(os.path.basename(href))[0])
        if "protocol" in name.lower():
            continue
        m = re.search(r"V?(\d+\.\d+\.\d+)", name)
        ver = m.group(1) if m else "?"
        url = PUB_BASE + urllib.parse.quote(href)
        rels.append(Release(name, ver, url))
    # de-dup by url
    seen, out = set(), []
    for r in rels:
        if r.url not in seen:
            seen.add(r.url); out.append(r)
    return out


def download(url: str, dest: str) -> str:
    data = _get(url)
    with open(dest, "wb") as fh:
        fh.write(data)
    return dest


# --------------------------------------------------------------- firmware source
@dataclass
class FirmwareImage:
    bin_name: str          # filename to write into firmware/
    data: bytes
    model: Optional[str]   # model from the readme, if known
    version: Optional[str]
    readme: Optional[str]


def _model_from_text(text: str) -> Optional[str]:
    m = re.search(r"Model:\s*UNIDEN\s+(\S+)", text, re.I)
    return m.group(1).upper() if m else None


def load_image(path: str) -> FirmwareImage:
    """Load a firmware image from a Uniden ``.zip`` or a bare ``.bin``."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            bins = [n for n in names if n.lower().endswith(".bin")]
            if len(bins) != 1:
                raise ValueError(
                    f"expected exactly one .bin in {os.path.basename(path)}, "
                    f"found {len(bins)}")
            readme = None
            for n in names:
                if n.lower().endswith(".txt"):
                    readme = z.read(n).decode("utf-8", "replace")
                    break
            data = z.read(bins[0])
            model = _model_from_text(readme or "")
            ver = re.search(r"(\d+[._]\d+[._]\d+)", os.path.basename(bins[0]))
            return FirmwareImage(os.path.basename(bins[0]), data, model,
                                 ver.group(1).replace("_", ".") if ver else None,
                                 readme)
    elif path.lower().endswith(".bin"):
        with open(path, "rb") as fh:
            data = fh.read()
        base = os.path.basename(path)
        model = "SDS100" if re.search(r"sds[\-_ ]?100", base, re.I) else None
        ver = re.search(r"(\d+[._]\d+[._]\d+)", base)
        return FirmwareImage(base, data, model,
                             ver.group(1).replace("_", ".") if ver else None, None)
    raise ValueError("firmware source must be a .zip or .bin")


# ------------------------------------------------------------------- staging
def backup_firmware_dir(card_root: str) -> str:
    src = firmware_dir(card_root)
    base = src + ".bak"
    dst, n = base, 0
    while os.path.exists(dst):
        n += 1; dst = f"{base}{n}"
    shutil.copytree(src, dst)
    return dst


@dataclass
class StageResult:
    bin_name: str
    cleared: list[str]
    backup: str


def stage(card_root: str, image: FirmwareImage, *, backup: bool = True) -> StageResult:
    """Write ``image`` into firmware/, clearing any prior firmware binary.

    Caller is responsible for model-match and confirmation checks.
    """
    d = firmware_dir(card_root)
    if not os.path.isdir(d):
        raise ValueError(f"no firmware/ folder on card at {d}")
    bak = backup_firmware_dir(card_root) if backup else ""
    cleared = []
    for f in staged_firmware(card_root):     # enforce single-version rule
        os.remove(os.path.join(d, f))
        cleared.append(f)
    with open(os.path.join(d, image.bin_name), "wb") as fh:
        fh.write(image.data)
    return StageResult(image.bin_name, cleared, bak)
