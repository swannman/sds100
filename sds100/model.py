"""In-memory model for a Sentinel favorites list.

Parses the inner text of a ``.hpe`` file (see :mod:`sds100.codec`) into a tree
of :class:`Record` nodes and serializes it back **byte-for-byte identically**,
preserving every column whether or not this tool understands it.  Edit
operations mutate the tree; meaningful columns are exposed through
:mod:`sds100.schema`.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Iterator, Optional

from . import schema
from .codec import CRLF, SIGNATURE


@dataclass
class Record:
    """A single tab-delimited record plus its nested children."""

    tag: str
    fields: list[str]                      # full split, fields[0] == tag
    children: list["Record"] = dc_field(default_factory=list)

    # -- named column access -------------------------------------------------
    def get(self, key: str, default: str = "") -> str:
        return schema.named(self.tag, self.fields, key, default)

    def set(self, key: str, value: str) -> None:
        idx = schema.FIELDS.get(self.tag, {}).get(key)
        if idx is None:
            raise KeyError(f"{self.tag} has no field {key!r}")
        while len(self.fields) <= idx:
            self.fields.append("")
        self.fields[idx] = value

    @property
    def name(self) -> str:
        return self.get("name")

    @property
    def avoided(self) -> bool:
        return self.get("avoid", "Off").lower() == "on"

    def set_avoid(self, on: bool) -> None:
        self.set("avoid", "On" if on else "Off")

    # -- tree helpers --------------------------------------------------------
    def of(self, *tags: str) -> Iterator["Record"]:
        """Yield direct children whose tag is in ``tags``."""
        for c in self.children:
            if c.tag in tags:
                yield c

    def to_line(self) -> str:
        return "\t".join(self.fields)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} {self.name!r} +{len(self.children)}>"


@dataclass
class FavoritesList:
    """A parsed favorites list: header lines plus a forest of systems."""

    header: list[str] = dc_field(default_factory=list)   # raw header lines
    systems: list[Record] = dc_field(default_factory=list)

    # ------------------------------------------------------------------ parse
    @classmethod
    def parse(cls, text: str) -> "FavoritesList":
        fav = cls()
        cur_system: Optional[Record] = None
        cur_group: Optional[Record] = None       # C-Group / T-Group
        cur_site: Optional[Record] = None

        for raw_line in text.split(CRLF):
            if not raw_line:
                continue
            fields = raw_line.split("\t")
            tag = fields[0]

            if tag in schema.HEADER_TAGS:
                fav.header.append(raw_line)
                continue
            if tag == schema.FOOTER_TAG:        # signature line; re-added later
                continue

            rec = Record(tag, fields)

            if tag in schema.SYSTEM_TAGS:
                fav.systems.append(rec)
                cur_system, cur_group, cur_site = rec, None, None
            elif tag in schema.GROUP_TAGS:
                _attach(cur_system, rec)
                cur_group, cur_site = rec, None
            elif tag in schema.SITE_TAGS:
                _attach(cur_system, rec)
                cur_site, cur_group = rec, None
            elif tag == "C-Freq":
                _attach(cur_group, rec)
            elif tag == "T-Freq":
                _attach(cur_site, rec)
            elif tag == "TGID":
                _attach(cur_group, rec)
            else:
                # Rectangle, BandPlan_Mot, DQKs_Status, and any future
                # secondary row attach to the innermost open container.
                # Only one of cur_site / cur_group is ever active at a time.
                _attach(cur_site or cur_group or cur_system, rec)
        return fav

    # -------------------------------------------------------------- serialize
    def to_text(self) -> str:
        lines: list[str] = list(self.header)
        for system in self.systems:
            _emit(system, lines)
        lines.append(SIGNATURE)
        return CRLF.join(lines) + CRLF

    # ---------------------------------------------------------------- queries
    @property
    def name(self) -> str:
        """Display name of the list (first system name is the closest proxy)."""
        return self.systems[0].name if self.systems else ""

    def walk(self) -> Iterator[tuple[Record, ...]]:
        """Yield (system, group/site, leaf) paths for every channel/tgid."""
        for sysrec in self.systems:
            for child in sysrec.children:
                if child.tag in schema.GROUP_TAGS:
                    for leaf in child.of("C-Freq", "TGID"):
                        yield (sysrec, child, leaf)
                elif child.tag in schema.SITE_TAGS:
                    for leaf in child.of("T-Freq"):
                        yield (sysrec, child, leaf)

    def find_system(self, name: str) -> Optional[Record]:
        return _by_name(self.systems, name)

    def groups(self, system: Record):
        return list(system.of(*schema.GROUP_TAGS))

    def sites(self, system: Record):
        return list(system.of(*schema.SITE_TAGS))

    # ------------------------------------------------------------------- edits
    def add_system(self, kind: str, name: str) -> Record:
        """Create an empty Conventional or Trunk system."""
        tag = {"conventional": "Conventional", "trunk": "Trunk"}.get(kind.lower())
        if tag is None:
            raise ValueError("system kind must be 'conventional' or 'trunk'")
        if tag == "Trunk":
            raise ValueError(
                "creating Trunk systems from scratch is not supported (they "
                "require site/band-plan data); build trunk lists in Sentinel "
                "or copy an existing one")
        rec = make_record("Conventional", name=name)
        self.systems.append(rec)
        return rec

    def add_group(self, system: Record, name: str) -> Record:
        """Add a department/group to a system (C-Group or T-Group to match)."""
        gtag = "T-Group" if system.tag == "Trunk" else "C-Group"
        rec = make_record(gtag, name=name)
        _insert_grouped(system, rec, after_tags=schema.GROUP_TAGS | schema.SITE_TAGS)
        return rec

    def add_channel(self, group: Record, name: str, freq_hz: int,
                    modulation: str = "NFM", tone: str = "",
                    service_type: str = "") -> Record:
        if group.tag != "C-Group":
            raise ValueError("channels belong to a conventional group (C-Group)")
        rec = make_record(
            "C-Freq", name=name, freq=str(freq_hz),
            modulation=modulation, tone=schema.normalize_tone(tone),
            service_type=schema.resolve_service_type(service_type))
        group.children.append(rec)
        return rec

    def add_talkgroup(self, group: Record, name: str, tgid: str,
                      service_type: str = "") -> Record:
        if group.tag != "T-Group":
            raise ValueError("talkgroups belong to a trunk group (T-Group)")
        rec = make_record(
            "TGID", name=name, tgid=str(tgid),
            service_type=schema.resolve_service_type(service_type))
        group.children.append(rec)
        return rec

    def remove(self, target: Record) -> bool:
        """Remove ``target`` (a system or any nested record).  Returns True."""
        if target in self.systems:
            self.systems.remove(target)
            return True
        for sysrec in self.systems:
            if _remove_descendant(sysrec, target):
                return True
        return False


# --------------------------------------------------------------------- helpers
def _attach(parent: Optional[Record], rec: Record) -> None:
    if parent is None:
        raise ValueError(f"{rec.tag} record appeared with no parent container")
    parent.children.append(rec)


def _emit(rec: Record, lines: list[str]) -> None:
    lines.append(rec.to_line())
    for child in rec.children:
        _emit(child, lines)


def _insert_grouped(parent: Record, rec: Record, after_tags) -> None:
    """Insert ``rec`` after the last existing child whose tag is in
    ``after_tags`` (keeps like records together), else append."""
    pos = len(parent.children)
    for i, c in enumerate(parent.children):
        if c.tag == rec.tag:
            pos = i + 1
    parent.children.insert(pos, rec)


def _remove_descendant(node: Record, target: Record) -> bool:
    if target in node.children:
        node.children.remove(target)
        return True
    for child in node.children:
        if _remove_descendant(child, target):
            return True
    return False


def _by_name(records, name: str) -> Optional[Record]:
    name_l = name.lower()
    for r in records:
        if r.name.lower() == name_l:
            return r
    return None


def make_record(tag: str, **named_values) -> Record:
    """Build a new record from its schema template, filling named columns."""
    template = schema.TEMPLATES.get(tag)
    if template is None:
        raise ValueError(f"no template for record type {tag!r}")
    fields = [tag] + list(template)
    rec = Record(tag, fields)
    for key, value in named_values.items():
        if value is not None:
            rec.set(key, value)
    # Any remaining required (None) slots are a programming error.
    if any(f is None for f in rec.fields):
        missing = [k for k, i in schema.FIELDS.get(tag, {}).items()
                   if i < len(rec.fields) and rec.fields[i] is None]
        raise ValueError(f"{tag}: missing required field(s) {missing}")
    return rec
