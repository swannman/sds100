"""Talk to an SDS100 / BCDx36HP scanner mounted as USB mass storage.

When you connect the scanner and choose **Mass Storage** mode, its microSD
card mounts as a normal volume.  Sentinel identifies the scanner by a
top-level ``BCDx36HP`` folder on that volume; favorites live under::

    <volume>/BCDx36HP/FavoriteLists/f_list.cfg     index of lists
    <volume>/BCDx36HP/FavoriteLists/*.hpd          one plain-text file per list

The on-card ``.hpd`` files use the *same* tab-delimited record format as the
inner text of a ``.hpe`` export (see :mod:`sds100.codec`), but stored as plain
UTF-8 -- they are **not** gzip/scrambled.

Layout reverse-engineered from ``BCDx36HP_Sentinel.exe``.  The read-side
operations here (``detect``/``inspect``/``pull``) are safe.  ``push`` writes to
the card and updates the index; treat it as experimental until verified
against a real device, and always keep the backup it makes.
"""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from . import codec
from .model import FavoritesList

SCANNER_DIR = "BCDx36HP"
FAVORITES_SUBDIR = os.path.join(SCANNER_DIR, "FavoriteLists")
INDEX_FILE = "f_list.cfg"


@dataclass
class Scanner:
    """A mounted scanner volume."""

    mount: str

    @property
    def root(self) -> str:
        return os.path.join(self.mount, SCANNER_DIR)

    @property
    def favorites_dir(self) -> str:
        return os.path.join(self.mount, FAVORITES_SUBDIR)

    @property
    def index_path(self) -> str:
        return os.path.join(self.favorites_dir, INDEX_FILE)

    def hpd_files(self) -> list[str]:
        return sorted(glob.glob(os.path.join(self.favorites_dir, "*.hpd")))


def detect(volumes_dir: str = "/Volumes") -> list[Scanner]:
    """Return every mounted volume that looks like an SDS100/BCDx36HP card."""
    found = []
    try:
        entries = os.listdir(volumes_dir)
    except FileNotFoundError:
        return found
    for name in entries:
        mount = os.path.join(volumes_dir, name)
        if os.path.isdir(os.path.join(mount, SCANNER_DIR)):
            found.append(Scanner(mount))
    return found


def require_one(volumes_dir: str = "/Volumes",
                mount: Optional[str] = None) -> Scanner:
    if mount:
        s = Scanner(mount)
        if not os.path.isdir(s.root):
            raise ValueError(f"no {SCANNER_DIR} folder under {mount!r}; "
                             "is the scanner in Mass Storage mode?")
        return s
    scanners = detect(volumes_dir)
    if not scanners:
        raise ValueError(
            "no scanner found. Connect the SDS100 via USB, choose 'Mass "
            "Storage' on the radio, then retry (or pass --mount).")
    if len(scanners) > 1:
        raise ValueError("multiple candidate volumes: "
                         + ", ".join(s.mount for s in scanners)
                         + " -- pass --mount to choose one")
    return scanners[0]


def read_hpd(path: str) -> FavoritesList:
    """Load a plain-text ``.hpd`` list from the card into the model."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    return FavoritesList.parse(text)


def hpd_text(fav: FavoritesList, include_signature: bool) -> str:
    """Serialize a list to ``.hpd`` text (plain, optionally w/ signature)."""
    text = fav.to_text()
    if not include_signature:
        # to_text() always appends the File signature line; drop it for .hpd
        from .codec import SIGNATURE, CRLF
        text = text.replace(CRLF + SIGNATURE + CRLF, CRLF)
    return text


def backup_favorites(scanner: Scanner) -> str:
    """Copy the whole FavoriteLists folder next to it as a timestamp-free
    ``.bak`` tree.  Returns the backup path."""
    dst = scanner.favorites_dir + ".bak"
    n = 0
    base = dst
    while os.path.exists(dst):
        n += 1
        dst = f"{base}{n}"
    shutil.copytree(scanner.favorites_dir, dst)
    return dst
