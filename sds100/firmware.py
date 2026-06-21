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

import ftplib
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

# Uniden's official firmware/database distribution server. It carries the full
# version history (the public wiki page lags behind it).
FTP_HOST = "ftp.homepatrol.com"
FTP_USER = "homepatrolftp"
FTP_PASS = "green7Corn"
FTP_DIR = "/BCDx36HP"

# Per-model firmware naming on the server. The leading anchor keeps SDS100 from
# matching SDS100E / USDS100 / SDS200 / SDS150, which are different radios.
MODEL_PATTERNS = {
    "SDS100": (r"^SDS-100_V([\d_]+)\.bin$", r"^SDS-100-SUB_V([\d_]+)\.firm$"),
    "SDS200": (r"^SDS200_V([\d_]+)\.bin$", r"^SDS200-SUB_V([\d_]+)\.firm$"),
    "BCD436HP": (r"^BCD436HP_V([\d_]+)\.bin$", None),
    "BCD536HP": (r"^BCD536HP_V([\d_]+)\.bin$", None),
}


def _ver_key(v: str):
    return tuple(int(x) for x in v.replace("_", ".").split("."))


@dataclass
class FtpFirmware:
    filename: str
    kind: str        # 'main' or 'sub'
    version: str     # dotted


def ftp_list_firmware(model: str) -> dict[str, list[FtpFirmware]]:
    """List main/sub firmware available on Uniden's server for ``model``."""
    pats = MODEL_PATTERNS.get(model.upper())
    if not pats:
        raise ValueError(f"unknown model {model!r} for firmware lookup")
    main_re = re.compile(pats[0])
    sub_re = re.compile(pats[1]) if pats[1] else None
    with ftplib.FTP(FTP_HOST, timeout=30) as f:
        f.login(FTP_USER, FTP_PASS)
        f.cwd(FTP_DIR)
        names = f.nlst()
    out = {"main": [], "sub": []}
    for n in names:
        base = os.path.basename(n)
        m = main_re.match(base)
        if m:
            out["main"].append(FtpFirmware(base, "main", m.group(1).replace("_", ".")))
            continue
        if sub_re:
            m = sub_re.match(base)
            if m:
                out["sub"].append(FtpFirmware(base, "sub", m.group(1).replace("_", ".")))
    out["main"].sort(key=lambda x: _ver_key(x.version))
    out["sub"].sort(key=lambda x: _ver_key(x.version))
    return out


def ftp_latest(model: str) -> dict[str, Optional[FtpFirmware]]:
    avail = ftp_list_firmware(model)
    return {"main": avail["main"][-1] if avail["main"] else None,
            "sub": avail["sub"][-1] if avail["sub"] else None}


def ftp_download(filename: str, dest: str) -> str:
    with ftplib.FTP(FTP_HOST, timeout=60) as f:
        f.login(FTP_USER, FTP_PASS)
        f.cwd(FTP_DIR)
        with open(dest, "wb") as fh:
            f.retrbinary(f"RETR {filename}", fh.write)
    return dest


def ftp_load_image(filename: str, kind: str, model: str,
                   version: str) -> "FirmwareImage":
    """Download a single firmware file from the server into a FirmwareImage."""
    with ftplib.FTP(FTP_HOST, timeout=60) as f:
        f.login(FTP_USER, FTP_PASS)
        f.cwd(FTP_DIR)
        chunks = []
        f.retrbinary(f"RETR {filename}", chunks.append)
    return FirmwareImage(filename, b"".join(chunks), kind, model, version)
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
# The SDS100 has two processors with separate firmware:
#   main CPU -> SDS-100_V<ver>.bin
#   sub/DSP  -> SDS-100-SUB_V<ver>.firm
# A combined zip carries one of each; the firmware/ folder may hold one main
# and one sub at a time (never two of either).
def _kind(filename: str) -> Optional[str]:
    low = filename.lower()
    if low.endswith(".firm"):
        return "sub"
    if low.endswith(".bin"):
        return "sub" if "sub" in low else "main"
    return None


@dataclass
class FirmwareImage:
    name: str              # filename to write into firmware/
    data: bytes
    kind: str              # 'main' or 'sub'
    model: Optional[str]   # model from the readme, if known
    version: Optional[str]


def _model_from_text(text: str) -> Optional[str]:
    m = re.search(r"Model:\s*UNIDEN\s+(\S+)", text, re.I)
    return m.group(1).upper() if m else None


def _version_of(name: str) -> Optional[str]:
    m = re.search(r"(\d+[._]\d+[._]\d+)", os.path.basename(name))
    return m.group(1).replace("_", ".") if m else None


def load_images(path: str) -> list[FirmwareImage]:
    """Load firmware image(s) from a Uniden ``.zip`` (main and/or sub) or a
    bare ``.bin``/``.firm``. Returns at most one main and one sub image."""
    images: list[FirmwareImage] = []
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            model = None
            for n in names:
                if n.lower().endswith(".txt"):
                    model = _model_from_text(z.read(n).decode("utf-8", "replace"))
                    if model:
                        break
            for n in names:
                k = _kind(n)
                if k:
                    images.append(FirmwareImage(os.path.basename(n), z.read(n),
                                                k, model, _version_of(n)))
    elif path.lower().endswith((".bin", ".firm")):
        with open(path, "rb") as fh:
            data = fh.read()
        base = os.path.basename(path)
        k = _kind(base) or "main"
        model = "SDS100" if re.search(r"sds[\-_ ]?100", base, re.I) else None
        images.append(FirmwareImage(base, data, k, model, _version_of(base)))
    else:
        raise ValueError("firmware source must be a .zip, .bin, or .firm")

    if not images:
        raise ValueError(f"no firmware (.bin/.firm) found in {os.path.basename(path)}")
    kinds = [i.kind for i in images]
    for k in ("main", "sub"):
        if kinds.count(k) > 1:
            raise ValueError(f"{os.path.basename(path)} contains multiple {k} "
                             "firmware files; expected at most one of each")
    return images


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
    written: list[str]
    cleared: list[str]
    backup: str


def stage(card_root: str, images: list[FirmwareImage], *,
          backup: bool = True) -> StageResult:
    """Write firmware ``images`` (main and/or sub) into firmware/, clearing any
    prior firmware first (the single-version rule). The .dat tables are kept.

    Caller is responsible for model-match and confirmation checks.
    """
    d = firmware_dir(card_root)
    if not os.path.isdir(d):
        raise ValueError(f"no firmware/ folder on card at {d}")
    bak = backup_firmware_dir(card_root) if backup else ""
    cleared = []
    for f in staged_firmware(card_root):     # remove any prior main/sub binaries
        os.remove(os.path.join(d, f))
        cleared.append(f)
    written = []
    for img in images:
        path = os.path.join(d, img.name)
        with open(path, "wb") as fh:
            fh.write(img.data)
        _strip_macos_metadata(path)
        written.append(img.name)
    # Remove any AppleDouble sidecars in the folder -- the scanner could mistake
    # a ._SDS-100_*.bin for a second firmware file and refuse to update.
    for f in os.listdir(d):
        if f.startswith("._") or f == ".DS_Store":
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
    return StageResult(written, cleared, bak)


def _strip_macos_metadata(path: str) -> None:
    """Clear extended attributes so macOS doesn't write a ._ AppleDouble file
    for this firmware when the card is ejected. Best-effort, macOS only."""
    import subprocess
    try:
        subprocess.run(["xattr", "-c", path], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError):
        pass
