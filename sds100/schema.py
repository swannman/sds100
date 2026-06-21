"""Record schema for the Sentinel favorites-export text format.

Each line of the inner text is a single record: a tab-separated list whose
first field is the *tag* (record type).  Records nest by document order
according to a small grammar:

    TargetModel                          (header)
    FormatVersion                        (header)
    Conventional                         system
        DQKs_Status                      department-quick-key status row
        C-Group                          group / department
            Rectangle                    geo filter
            C-Freq                       conventional channel
    Trunk                                system
        DQKs_Status
        BandPlan_Mot                     Motorola band plan
        Site                             trunk site
            T-Freq                       site (control/voice) frequency
        T-Group                          group / department
            Rectangle
            TGID                         talkgroup
    File                                 (signature footer)

Field indices below are 0-based into the *full* tab-split list, so index 0 is
always the tag itself.  Only the human-meaningful columns are named; every
other column is preserved verbatim on round-trip.  Indices were derived
empirically from real export files and the scanner's on-card list files.
"""

from __future__ import annotations

# Tags that introduce a *system* (top-level container).
SYSTEM_TAGS = {"Conventional", "Trunk"}

# Tags that are direct children of a system and act as containers themselves.
GROUP_TAGS = {"C-Group", "T-Group"}      # hold channels / talkgroups
SITE_TAGS = {"Site"}                     # hold site frequencies

# Header / footer lines handled specially (not part of the record tree).
HEADER_TAGS = ("TargetModel", "FormatVersion")
FOOTER_TAG = "File"

# Expected field counts, used for validation / sane defaults.
ARITY = {
    "TargetModel": 2,
    "FormatVersion": 2,
    "Conventional": 15,
    "C-Group": 11,
    "Rectangle": 6,
    "C-Freq": 18,
    "DQKs_Status": 102,
    "Trunk": 22,
    "BandPlan_Mot": 26,
    "Site": 19,
    "T-Freq": 8,
    "T-Group": 10,
    "TGID": 17,
    "File": 2,
}

# Named columns per tag: {name: index}.  ``avoid`` is the Off/On lockout flag.
FIELDS = {
    "Conventional": {"name": 3, "avoid": 4, "system_type": 6},
    "C-Group": {"name": 3, "avoid": 4},
    "C-Freq": {"name": 3, "avoid": 4, "freq": 5, "modulation": 6,
               "tone": 7, "service_type": 8},
    "Trunk": {"name": 3, "avoid": 4, "system_type": 6},
    "Site": {"name": 3, "avoid": 4, "lat": 5, "lon": 6},
    "T-Freq": {"avoid": 4, "freq": 5, "lcn": 6, "usage": 7},
    "T-Group": {"name": 3, "avoid": 4},
    "TGID": {"name": 3, "avoid": 4, "tgid": 5, "service_type": 7, "slot_cc": 16},
    "Rectangle": {"lat1": 2, "lon1": 3, "lat2": 4, "lon2": 5},
}

# Field templates for newly-created records.  ``None`` marks a slot the caller
# must fill in (name, frequency, etc.); everything else is a sane default
# copied from how Sentinel writes fresh entries.  Index 0 (tag) is added by
# the model, so these lists are the *fields after the tag*.
#
# Defaults below reproduce a typical entry; callers override named columns.
TEMPLATES = {
    # name@3 avoid@4 freq@5 mod@6 tone@7 svc@8 then per-channel options
    "C-Freq": ["", "", None, "Off", None, "NFM", "", "21",
               "Off", "2", "0", "Off", "Auto", "Off", "On", "Off", "Off"],
    # name@3 avoid@4 tgid@5 ALL@6 svc@7 ...
    "TGID": ["", "", None, "Off", None, "ALL", "21", "2", "0",
             "Off", "Auto", "Off", "On", "Off", "Off", "Any"],
    # name@3 avoid@4 lat lon radius shape ...
    "C-Group": ["", "", None, "Off", "0.000000", "0.000000", "0.0",
                "Rectangles", "Off", "Global"],
    "T-Group": ["", "", None, "Off", "0.000000", "0.000000", "0.0",
                "Circle", "Off"],
    # avoid@4 freq@5 lcn@6 usage@7
    "T-Freq": ["", "", "Off", None, "0", "Srch"],
    # name@3 avoid@4 type@6 ... (conventional system skeleton)
    "Conventional": ["", "", None, "Off", "", "Conventional", "Off",
                     "Off", "0", "Off", "Off", "400", "Auto", "8"],
    "Site": ["", "", None, "Off", "0.000000", "0.000000", "0.0", "AUTO",
             "Standard", "Wide", "Circle", "Off", "400", "Auto", "8",
             "Off", "0", "Global"],
}


# Service-type id -> name, as used in the channel/talkgroup records.
# Field 8 of C-Freq and field 7 of TGID hold the numeric id.  Id 21 ("Other")
# is the neutral default for hand-added entries.
SERVICE_TYPES = {
    1: "Multi-Dispatch", 2: "Law Dispatch", 3: "Fire Dispatch",
    4: "EMS Dispatch", 6: "Multi-Tac", 7: "Law Tac", 8: "Fire-Tac",
    9: "EMS-Tac", 11: "Interop", 12: "Hospital", 13: "Ham",
    14: "Public Works", 15: "Aircraft", 16: "Federal", 17: "Business",
    20: "Railroad", 21: "Other", 22: "Multi-Talk", 23: "Law Talk",
    24: "Fire-Talk", 25: "EMS-Talk", 26: "Transportation",
    29: "Emergency Ops", 30: "Military", 31: "Media", 32: "Schools",
    33: "Security", 34: "Utilities", 37: "Corrections",
}
SERVICE_TYPE_IDS = {name.lower(): i for i, name in SERVICE_TYPES.items()}
DEFAULT_SERVICE_TYPE = 21  # "Other"


def resolve_service_type(value: str) -> str:
    """Accept a service-type name or numeric id, return the numeric id string."""
    if value is None or value == "":
        return str(DEFAULT_SERVICE_TYPE)
    v = str(value).strip()
    if v.isdigit():
        return v
    if v.lower() in SERVICE_TYPE_IDS:
        return str(SERVICE_TYPE_IDS[v.lower()])
    raise ValueError(
        f"unknown service type {value!r}; use a number or one of: "
        + ", ".join(SERVICE_TYPES.values()))


def service_type_name(value: str) -> str:
    """Human label for a numeric service-type id (falls back to the id)."""
    try:
        return SERVICE_TYPES.get(int(value), value)
    except (TypeError, ValueError):
        return value


def normalize_tone(value: str) -> str:
    """Convert friendly tone input into the on-wire ``AudioOption`` encoding.

    Examples accepted -> produced::

        "100.0", "C100.0", "CTCSS 100.0"  -> "TONE=C100.0"
        "D023", "DCS 023"                 -> "TONE=D023"
        "NAC 293", "293", "0x293" (P25)   -> handled only with an explicit NAC/CC
        "NAC=293", "NAC=Srch"             -> passthrough
        "CC 1", "ColorCode 1"             -> "ColorCode=1"

    Already-encoded values (containing ``=``) pass through unchanged.  An empty
    or "none"/"off"/"search" value yields no tone.
    """
    if not value:
        return ""
    v = value.strip()
    if "=" in v:                       # already encoded
        return v
    low = v.lower()
    if low in ("none", "off", "no", "-"):
        return ""
    if low in ("search", "srch"):
        return "NAC=Srch"
    # DMR/NXDN color code
    for pfx in ("colorcode", "cc"):
        if low.startswith(pfx):
            n = v[len(pfx):].strip()
            return f"ColorCode={n}"
    if low.startswith("ran"):
        return f"RAN={v[3:].strip()}"
    if low.startswith("nac"):
        return f"NAC={v[3:].strip()}"
    # CTCSS / DCS
    if low.startswith("ctcss"):
        v = v[5:].strip()
        low = v.lower()
    elif low.startswith("dcs"):
        return f"TONE=D{v[3:].strip().zfill(3)}"
    if low.startswith("c"):
        return f"TONE=C{v[1:].strip()}"
    if low.startswith("d"):
        return f"TONE=D{v[1:].strip().zfill(3)}"
    # bare number -> CTCSS frequency
    try:
        return f"TONE=C{float(v):.1f}"
    except ValueError:
        return v


def named(tag: str, fields: list[str], key: str, default: str = "") -> str:
    """Return the named column ``key`` from a split record, or ``default``."""
    idx = FIELDS.get(tag, {}).get(key)
    if idx is None or idx >= len(fields):
        return default
    return fields[idx]
