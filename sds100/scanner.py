"""Talk to an SDS100 / BCDx36HP scanner's microSD card.

Mount the card either by putting the radio in **Mass Storage** mode (connect
USB, press ``E`` at the prompt) or with a card reader.  The card has a
top-level ``BCDx36HP`` folder; favorites live under::

    <volume>/BCDx36HP/favorites_lists/f_list.cfg      index of lists
    <volume>/BCDx36HP/favorites_lists/f_NNNNNN.hpd     one plain-text list each

Each ``.hpd`` uses the *same* tab-delimited record format as the inner text of
a ``.hpe`` export (see :mod:`sds100.codec`), stored as plain UTF-8 with CRLF
line endings and **no** ``File`` signature line.

``f_list.cfg`` lines look like::

    F-List <display name> <filename.hpd> <flag> x115

where the 115 flags are the list's quick-key assignments.  Replacing an
existing list only rewrites its ``.hpd`` (the index already points at it, so it
is left untouched); adding a new list appends one ``F-List`` line.

Verified against a physical SDS100 (main fw 1.21.00): all on-card ``.hpd``
files round-trip byte-identically through :class:`sds100.model.FavoritesList`.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
from dataclasses import dataclass
from typing import Optional

from .codec import CRLF, SIGNATURE
from .model import FavoritesList

SCANNER_DIR = "BCDx36HP"
FAVORITES_SUBDIR = os.path.join(SCANNER_DIR, "favorites_lists")
INDEX_FILE = "f_list.cfg"
HPD_GLOB = "f_*.hpd"
HPD_NAME_RE = re.compile(r"^f_(\d{6})\.hpd$")
N_FLIST_FLAGS = 115


@dataclass
class FavListEntry:
    """One row of ``f_list.cfg``."""

    name: str
    filename: str
    flags: list[str]

    def to_line(self) -> str:
        return "\t".join(["F-List", self.name, self.filename] + self.flags)


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
        return sorted(glob.glob(os.path.join(self.favorites_dir, HPD_GLOB)))

    # -- index ---------------------------------------------------------------
    def read_index(self) -> tuple[list[str], list[FavListEntry]]:
        """Return (header_lines, entries) from ``f_list.cfg``."""
        with open(self.index_path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        header, entries = [], []
        for line in text.split(CRLF):
            if not line:
                continue
            parts = line.split("\t")
            if parts[0] == "F-List":
                entries.append(FavListEntry(parts[1], parts[2], parts[3:]))
            else:
                header.append(line)
        return header, entries

    def write_index(self, header: list[str], entries: list[FavListEntry]) -> None:
        lines = list(header) + [e.to_line() for e in entries]
        text = CRLF.join(lines) + CRLF
        with open(self.index_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def allocate_filename(self) -> str:
        used = set()
        for p in self.hpd_files():
            m = HPD_NAME_RE.match(os.path.basename(p))
            if m:
                used.add(int(m.group(1)))
        n = (max(used) + 1) if used else 1
        return f"f_{n:06d}.hpd"


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


def serial_ports() -> list[str]:
    """USB-serial ports that may be a scanner in PC-control mode (not mass
    storage).  Used only to give a better hint when no SD card is mounted."""
    return sorted(glob.glob("/dev/cu.usbmodem*"))


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
            "no scanner card mounted. Put the SDS100 in Mass Storage mode "
            "(connect USB, press E) or use a card reader, then retry.")
    if len(scanners) > 1:
        raise ValueError("multiple candidate volumes: "
                         + ", ".join(s.mount for s in scanners)
                         + " -- pass --mount to choose one")
    return scanners[0]


# --------------------------------------------------------------- list <-> file
def read_hpd(path: str) -> FavoritesList:
    """Load a plain-text ``.hpd`` list from the card into the model."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return FavoritesList.parse(fh.read())


def hpd_text(fav: FavoritesList) -> str:
    """Serialize a list to on-card ``.hpd`` text (plain, no signature line)."""
    text = fav.to_text()
    return text.replace(CRLF + SIGNATURE + CRLF, CRLF)


def backup_favorites(scanner: Scanner) -> str:
    """Copy the whole favorites_lists folder next to it as a ``.bak`` tree."""
    base = scanner.favorites_dir + ".bak"
    dst, n = base, 0
    while os.path.exists(dst):
        n += 1
        dst = f"{base}{n}"
    shutil.copytree(scanner.favorites_dir, dst)
    return dst


@dataclass
class PushResult:
    name: str
    filename: str
    replaced: bool
    backup: str


def push(scanner: Scanner, fav: FavoritesList, name: str,
         backup: bool = True) -> PushResult:
    """Write ``fav`` onto the card as favorites list ``name``.

    If a list with that name already exists, its ``.hpd`` is overwritten and
    ``f_list.cfg`` is left untouched (safest path).  Otherwise a new ``.hpd``
    is allocated and one ``F-List`` line is appended, cloning the quick-key
    flags of an existing entry so the new list scans like the others.
    """
    header, entries = scanner.read_index()
    existing = next((e for e in entries if e.name.lower() == name.lower()), None)

    bak = backup_favorites(scanner) if backup else ""

    if existing is not None:
        target = os.path.join(scanner.favorites_dir, existing.filename)
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(hpd_text(fav))
        return PushResult(existing.name, existing.filename, True, bak)

    filename = scanner.allocate_filename()
    target = os.path.join(scanner.favorites_dir, filename)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(hpd_text(fav))
    # clone flags from an existing entry, else default to "monitor on".
    if entries:
        flags = list(entries[-1].flags)
    else:
        flags = ["Off"] * N_FLIST_FLAGS
    entries.append(FavListEntry(name, filename, flags))
    scanner.write_index(header, entries)
    return PushResult(name, filename, False, bak)
