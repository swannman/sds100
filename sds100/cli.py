"""Command-line interface for viewing and editing Sentinel ``.hpe`` lists.

Usage::

    sds100 <command> <file.hpe> [options]

Run ``sds100 --help`` or ``sds100 <command> --help`` for details.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
from typing import Optional

import os

from . import codec, schema, scanner as scanner_mod
from .format import hz_to_mhz, mhz_to_hz, table
from .model import FavoritesList, Record


# --------------------------------------------------------------------- loading
def _load(path: str) -> FavoritesList:
    return FavoritesList.parse(codec.read(path))


def _save(fav: FavoritesList, path: str, output: Optional[str],
          backup: bool = True) -> str:
    dest = output or path
    if dest == path and backup:
        shutil.copy2(path, path + ".bak")
    codec.write(dest, fav.to_text())
    return dest


def _resolve_system(fav: FavoritesList, name: Optional[str]) -> Record:
    if name:
        sysrec = fav.find_system(name)
        if not sysrec:
            _die(f"system {name!r} not found. Available: "
                 + ", ".join(repr(s.name) for s in fav.systems))
        return sysrec
    if len(fav.systems) == 1:
        return fav.systems[0]
    _die("multiple systems present; specify --system. Available: "
         + ", ".join(repr(s.name) for s in fav.systems))


def _find_group(fav: FavoritesList, system: Record,
                group_name: Optional[str]) -> Record:
    groups = fav.groups(system)
    if not groups:
        _die(f"system {system.name!r} has no groups")
    if group_name:
        for g in groups:
            if g.name.lower() == group_name.lower():
                return g
        _die(f"group {group_name!r} not found in {system.name!r}. Available: "
             + ", ".join(repr(g.name) for g in groups))
    if len(groups) == 1:
        return groups[0]
    _die("multiple groups present; specify --group. Available: "
         + ", ".join(repr(g.name) for g in groups))


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


# --------------------------------------------------------------------- reading
def cmd_info(args):
    fav = _load(args.file)
    n_chan = sum(1 for _ in fav.walk() if _[2].tag == "C-Freq")
    n_tg = sum(1 for _ in fav.walk() if _[2].tag == "TGID")
    target = next((h.split("\t", 1)[1] for h in fav.header
                   if h.startswith("TargetModel")), "?")
    print(f"File        : {args.file}")
    print(f"Target model: {target}")
    print(f"Systems     : {len(fav.systems)}")
    print(f"Channels    : {n_chan}")
    print(f"Talkgroups  : {n_tg}")


def cmd_ls(args):
    fav = _load(args.file)
    rows = []
    for s in fav.systems:
        groups = fav.groups(s)
        sites = fav.sites(s)
        leaves = sum(len(list(g.of("C-Freq", "TGID"))) for g in groups)
        kind = "conv" if s.tag == "Conventional" else "trunk"
        rows.append([s.name, kind, s.get("system_type"), str(len(groups)),
                     str(len(sites)), str(leaves),
                     "AVOID" if s.avoided else ""])
    print(table(rows, ["System", "Kind", "Type", "Groups", "Sites",
                       "Entries", ""]))


def cmd_show(args):
    fav = _load(args.file)
    systems = [_resolve_system(fav, args.system)] if args.system else fav.systems
    for s in systems:
        print(f"\n{s.tag}: {s.name}" + ("  [AVOID]" if s.avoided else ""))
        for g in fav.groups(s):
            leaves = list(g.of("C-Freq", "TGID"))
            print(f"  {g.tag}: {g.name}  ({len(leaves)})"
                  + ("  [AVOID]" if g.avoided else ""))
            for leaf in leaves:
                print("    " + _leaf_line(leaf))
        for site in fav.sites(s):
            freqs = list(site.of("T-Freq"))
            print(f"  Site: {site.name}  ({len(freqs)} freq)")
            for tf in freqs:
                print(f"    {hz_to_mhz(tf.get('freq')):>11} MHz  "
                      f"{tf.get('usage')}")


def _leaf_line(leaf: Record) -> str:
    flag = "x" if leaf.avoided else " "
    if leaf.tag == "C-Freq":
        return (f"[{flag}] {hz_to_mhz(leaf.get('freq')):>11} MHz  "
                f"{leaf.get('modulation'):<4} {leaf.get('tone'):<14} "
                f"{leaf.name}")
    if leaf.tag == "TGID":
        return (f"[{flag}] TGID {leaf.get('tgid'):<8} "
                f"{leaf.get('service_type'):<3} {leaf.name}")
    return f"[{flag}] {leaf.name}"


def cmd_channels(args):
    fav = _load(args.file)
    rows = []
    for s, g, leaf in fav.walk():
        if leaf.tag != "C-Freq":
            continue
        if args.system and s.name.lower() != args.system.lower():
            continue
        rows.append([s.name, g.name, leaf.name, hz_to_mhz(leaf.get("freq")),
                     leaf.get("modulation"), leaf.get("tone"),
                     "AVOID" if leaf.avoided else ""])
    print(table(rows, ["System", "Group", "Channel", "MHz", "Mod", "Tone", ""]))


def cmd_talkgroups(args):
    fav = _load(args.file)
    rows = []
    for s, g, leaf in fav.walk():
        if leaf.tag != "TGID":
            continue
        if args.system and s.name.lower() != args.system.lower():
            continue
        rows.append([s.name, g.name, leaf.get("tgid"), leaf.name,
                     schema.service_type_name(leaf.get("service_type")),
                     "AVOID" if leaf.avoided else ""])
    print(table(rows, ["System", "Group", "TGID", "Name", "Svc", ""]))


def cmd_search(args):
    fav = _load(args.file)
    q = args.query.lower()
    rows = []
    for s, g, leaf in fav.walk():
        hay = [leaf.name.lower(), leaf.get("freq"), hz_to_mhz(leaf.get("freq")),
               leaf.get("tgid")]
        if any(q in h for h in hay if h):
            val = (hz_to_mhz(leaf.get("freq")) + " MHz") if leaf.tag == "C-Freq" \
                else ("TGID " + leaf.get("tgid"))
            rows.append([leaf.tag, s.name, g.name, leaf.name, val,
                         "AVOID" if leaf.avoided else ""])
    if not rows:
        print("no matches")
        return
    print(table(rows, ["Type", "System", "Group", "Name", "Value", ""]))


# --------------------------------------------------------------------- editing
def cmd_add_channel(args):
    fav = _load(args.file)
    system = _resolve_system(fav, args.system)
    if system.tag != "Conventional":
        _die(f"{system.name!r} is a trunk system; use add-talkgroup")
    group = _find_group(fav, system, args.group)
    rec = fav.add_channel(group, args.name, mhz_to_hz(args.freq),
                          modulation=args.mod, tone=args.tone or "",
                          service_type=args.service_type or "")
    _commit(fav, args,
            f"added channel {rec.name!r} ({hz_to_mhz(rec.get('freq'))} MHz) "
            f"to {system.name} / {group.name}")


def cmd_add_talkgroup(args):
    fav = _load(args.file)
    system = _resolve_system(fav, args.system)
    if system.tag != "Trunk":
        _die(f"{system.name!r} is a conventional system; use add-channel")
    group = _find_group(fav, system, args.group)
    rec = fav.add_talkgroup(group, args.name, args.tgid,
                            service_type=args.service_type or "")
    _commit(fav, args,
            f"added talkgroup {rec.name!r} (TGID {rec.get('tgid')}) "
            f"to {system.name} / {group.name}")


def cmd_add_group(args):
    fav = _load(args.file)
    system = _resolve_system(fav, args.system)
    rec = fav.add_group(system, args.name)
    _commit(fav, args, f"added group {rec.name!r} to {system.name}")


def cmd_add_system(args):
    fav = _load(args.file)
    rec = fav.add_system(args.type, args.name)
    _commit(fav, args, f"added {args.type} system {rec.name!r}")


def cmd_rm(args):
    fav = _load(args.file)
    target = _resolve_target(fav, args)
    label = f"{target.tag} {target.name!r}"
    fav.remove(target)
    _commit(fav, args, f"removed {label}")


def cmd_avoid(args, on: bool):
    fav = _load(args.file)
    target = _resolve_target(fav, args)
    target.set_avoid(on)
    _commit(fav, args,
            f"{'avoided' if on else 'un-avoided'} {target.tag} {target.name!r}")


def _resolve_target(fav: FavoritesList, args) -> Record:
    """Locate a record by name, optionally scoped by system/group."""
    name_l = args.name.lower()
    candidates: list[tuple[Record, str]] = []
    for s in fav.systems:
        if args.system and s.name.lower() != args.system.lower():
            continue
        if s.name.lower() == name_l and not args.group:
            candidates.append((s, f"system {s.name}"))
        for g in fav.groups(s) + fav.sites(s):
            if args.group and g.name.lower() != args.group.lower():
                continue
            if g.name.lower() == name_l:
                candidates.append((g, f"{s.name}/{g.name}"))
            for leaf in g.of("C-Freq", "TGID", "T-Freq"):
                if leaf.name.lower() == name_l:
                    candidates.append((leaf, f"{s.name}/{g.name}/{leaf.name}"))
    if not candidates:
        _die(f"no record named {args.name!r} found")
    if len(candidates) > 1:
        paths = "\n  ".join(p for _, p in candidates)
        _die(f"{args.name!r} is ambiguous; narrow with --system/--group:\n  {paths}")
    return candidates[0][0]


def _commit(fav: FavoritesList, args, msg: str):
    if getattr(args, "dry_run", False):
        print(f"[dry-run] would have {msg}")
        return
    dest = _save(fav, args.file, args.output, backup=not args.no_backup)
    suffix = "" if dest == args.file else f" -> {dest}"
    print(f"{msg}{suffix}")


# ------------------------------------------------------------------- transform
def cmd_export(args):
    fav = _load(args.file)
    if args.format == "json":
        data = _to_dict(fav)
        out = json.dumps(data, indent=2)
    else:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["kind", "system", "group", "name", "freq_mhz_or_tgid",
                    "modulation", "tone", "service_type", "avoid"])
        for s, g, leaf in fav.walk():
            if leaf.tag == "C-Freq":
                w.writerow(["channel", s.name, g.name, leaf.name,
                            hz_to_mhz(leaf.get("freq")),
                            leaf.get("modulation"), leaf.get("tone"),
                            leaf.get("service_type"),
                            "On" if leaf.avoided else "Off"])
            elif leaf.tag == "TGID":
                w.writerow(["talkgroup", s.name, g.name, leaf.name,
                            leaf.get("tgid"), "", "",
                            leaf.get("service_type"),
                            "On" if leaf.avoided else "Off"])
        out = buf.getvalue()
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(out)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(out)


def _to_dict(fav: FavoritesList) -> dict:
    systems = []
    for s in fav.systems:
        node = {"name": s.name, "kind": s.tag, "type": s.get("system_type"),
                "avoid": s.avoided, "groups": [], "sites": []}
        for g in fav.groups(s):
            leaves = []
            for leaf in g.of("C-Freq", "TGID"):
                if leaf.tag == "C-Freq":
                    leaves.append({"name": leaf.name,
                                   "freq_mhz": hz_to_mhz(leaf.get("freq")),
                                   "modulation": leaf.get("modulation"),
                                   "tone": leaf.get("tone"),
                                   "avoid": leaf.avoided})
                else:
                    leaves.append({"name": leaf.name, "tgid": leaf.get("tgid"),
                                   "service_type": leaf.get("service_type"),
                                   "avoid": leaf.avoided})
            node["groups"].append({"name": g.name, "entries": leaves})
        for site in fav.sites(s):
            node["sites"].append(
                {"name": site.name,
                 "freqs": [hz_to_mhz(t.get("freq")) for t in site.of("T-Freq")]})
        systems.append(node)
    return {"systems": systems}


def cmd_decode(args):
    text = codec.read(args.file)
    out = args.output
    if out:
        with open(out, "w", newline="") as fh:
            fh.write(text)
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)


def cmd_encode(args):
    with open(args.file, "r", newline="") as fh:
        text = fh.read()
    codec.write(args.output, text)
    print(f"wrote {args.output}")


# ----------------------------------------------------------------- scanner I/O
def cmd_detect(args):
    scanners = scanner_mod.detect()
    if not scanners:
        ports = scanner_mod.serial_ports()
        if ports:
            print("A scanner appears to be connected in serial / PC-control "
                  "mode (" + ", ".join(ports) + "), but its microSD card is "
                  "not mounted as a USB drive.")
            print("Switch the radio to Mass Storage mode (or put the microSD "
                  "in a card reader), then re-run 'sds100 detect'.")
        else:
            print("no scanner found. Connect the SDS100 over USB and put it "
                  "in Mass Storage mode (its SD card mounts as a USB drive).")
        return
    for s in scanners:
        print(f"scanner at {s.mount}")
        print(f"  favorites dir: {s.favorites_dir}")
        if not os.path.exists(s.index_path):
            print(f"  index        : MISSING ({s.index_path})")
            continue
        _, entries = s.read_index()
        rows = []
        for e in entries:
            path = os.path.join(s.favorites_dir, e.filename)
            try:
                fav = scanner_mod.read_hpd(path)
                n = sum(1 for _ in fav.walk())
            except OSError:
                n = "?"
            on = sum(1 for f in e.flags if f == "On")
            rows.append([e.name, e.filename, str(n), str(on)])
        print(table(rows, ["List", "File", "Entries", "QuickKeys"]))


def _resolve_list(s, name):
    """Return a (FavListEntry, path) for a list on the card, by display name
    or filename fragment."""
    _, entries = s.read_index()
    if not entries:
        _die("no favorites lists registered on the scanner")
    match = None
    if name:
        for e in entries:
            if name.lower() in e.name.lower() or name.lower() in e.filename.lower():
                match = e
                break
        if match is None:
            _die(f"no list matching {name!r}; available: "
                 + ", ".join(e.name for e in entries))
    elif len(entries) == 1:
        match = entries[0]
    else:
        _die("multiple lists on the scanner; pass a name. Available: "
             + ", ".join(e.name for e in entries))
    return match, os.path.join(s.favorites_dir, match.filename)


def cmd_pull(args):
    s = scanner_mod.require_one(mount=args.mount)
    entry, path = _resolve_list(s, args.list)
    fav = scanner_mod.read_hpd(path)
    out = args.output or (schema_safe_filename(entry.name) + ".hpe")
    codec.write(out, fav.to_text())
    print(f"pulled {entry.name!r} ({entry.filename}) -> {out}")


def schema_safe_filename(name: str) -> str:
    keep = "-_. ()"
    return "".join(c for c in name if c.isalnum() or c in keep).strip() or "list"


def cmd_push(args):
    s = scanner_mod.require_one(mount=args.mount)
    fav = _load(args.file)  # validate the .hpe parses before touching the card
    if not os.path.exists(s.index_path):
        _die(f"scanner index {scanner_mod.INDEX_FILE} not found at "
             f"{s.index_path}; is this an SDS100/BCDx36HP card?")
    name = args.name or os.path.splitext(os.path.basename(args.file))[0]
    _, entries = s.read_index()
    existing = any(e.name.lower() == name.lower() for e in entries)
    action = ("overwrite existing list" if existing
              else "add new list (appends an f_list.cfg entry)")
    print(f"push {args.file!r} -> scanner list {name!r}: {action}")
    if not args.yes:
        _die("re-run with --yes to proceed (a .bak of favorites_lists is made "
             "first).")
    res = scanner_mod.push(s, fav, name, backup=not args.no_backup)
    verb = "overwrote" if res.replaced else "added"
    print(f"{verb} {res.name!r} -> {res.filename}")
    if res.backup:
        print(f"backup: {res.backup}")
    print("Eject the card / exit Mass Storage mode and power-cycle the radio "
          "to load the change.")


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sds100",
        description="View and edit Uniden Sentinel SDS100/BCDx36HP .hpe "
                    "favorites lists on the command line.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, func, help, **kw):
        sp = sub.add_parser(name, help=help, description=help, **kw)
        sp.add_argument("file", help="path to a .hpe file")
        sp.set_defaults(func=func)
        return sp

    def edit_opts(sp):
        sp.add_argument("-o", "--output", help="write to this file instead of "
                        "editing in place")
        sp.add_argument("--no-backup", action="store_true",
                        help="do not create a .bak when editing in place")
        sp.add_argument("--dry-run", action="store_true",
                        help="show what would change without writing")

    add("info", cmd_info, "summary of a list")
    add("ls", cmd_ls, "list systems")

    sp = add("show", cmd_show, "show groups and entries")
    sp.add_argument("system", nargs="?", help="limit to one system")

    sp = add("channels", cmd_channels, "table of conventional channels")
    sp.add_argument("-s", "--system", help="limit to one system")

    sp = add("talkgroups", cmd_talkgroups, "table of talkgroups")
    sp.add_argument("-s", "--system", help="limit to one system")

    sp = add("search", cmd_search, "search by name, frequency or TGID")
    sp.add_argument("query", help="text, MHz, or TGID to match")

    sp = add("add-channel", cmd_add_channel, "add a conventional channel")
    sp.add_argument("-s", "--system", help="target system (optional if one)")
    sp.add_argument("-g", "--group", help="target group (optional if one)")
    sp.add_argument("-n", "--name", required=True)
    sp.add_argument("-f", "--freq", required=True, help="frequency in MHz")
    sp.add_argument("--mod", default="NFM",
                    help="modulation: AM/FM/NFM/... (default NFM)")
    sp.add_argument("--tone", help="tone, e.g. '100.0' (CTCSS), 'D023' (DCS), "
                    "'NAC=293' (P25), 'CC 1' (DMR color code)")
    sp.add_argument("--service-type", help="service type name or id "
                    "(default Other)")
    edit_opts(sp)

    sp = add("add-talkgroup", cmd_add_talkgroup, "add a trunk talkgroup")
    sp.add_argument("-s", "--system", help="target system")
    sp.add_argument("-g", "--group", help="target group")
    sp.add_argument("-n", "--name", required=True)
    sp.add_argument("-t", "--tgid", required=True, help="talkgroup ID")
    sp.add_argument("--service-type", help="service type name or id "
                    "(default Other)")
    edit_opts(sp)

    sp = add("add-group", cmd_add_group, "add a department/group to a system")
    sp.add_argument("-s", "--system", help="target system")
    sp.add_argument("-n", "--name", required=True)
    edit_opts(sp)

    sp = add("add-system", cmd_add_system, "add an (empty) conventional system")
    sp.add_argument("--type", default="conventional",
                    choices=["conventional", "trunk"])
    sp.add_argument("-n", "--name", required=True)
    edit_opts(sp)

    sp = add("rm", cmd_rm, "remove a channel/talkgroup/group/system by name")
    sp.add_argument("-n", "--name", required=True)
    sp.add_argument("-s", "--system", help="scope to a system")
    sp.add_argument("-g", "--group", help="scope to a group")
    edit_opts(sp)

    for verb, on in (("avoid", True), ("unavoid", False)):
        sp = add(verb, lambda a, on=on: cmd_avoid(a, on),
                 f"set lockout {'on' if on else 'off'} for a record")
        sp.add_argument("-n", "--name", required=True)
        sp.add_argument("-s", "--system", help="scope to a system")
        sp.add_argument("-g", "--group", help="scope to a group")
        edit_opts(sp)

    sp = add("export", cmd_export, "export entries as CSV or JSON")
    sp.add_argument("--format", choices=["csv", "json"], default="csv")
    sp.add_argument("-o", "--output", help="output file (default stdout)")

    sp = add("decode", cmd_decode, "decode .hpe to raw tab-delimited text")
    sp.add_argument("-o", "--output", help="output file (default stdout)")

    sp = sub.add_parser("encode",
                        help="encode raw tab-delimited text back into a .hpe")
    sp.add_argument("file", help="input .txt produced by 'decode'")
    sp.add_argument("output", help="output .hpe path")
    sp.set_defaults(func=cmd_encode)

    # --- scanner I/O (these take no .hpe file positional) ---
    sp = sub.add_parser("detect",
                        help="find a connected scanner and list its favorites")
    sp.set_defaults(func=cmd_detect)

    sp = sub.add_parser("pull",
                        help="copy a list off the scanner card to a .hpe file")
    sp.add_argument("list", nargs="?",
                    help="list name or filename fragment (optional if one)")
    sp.add_argument("--mount", help="scanner volume path (default: auto)")
    sp.add_argument("-o", "--output", help="output .hpe path")
    sp.set_defaults(func=cmd_pull)

    sp = sub.add_parser("push",
                        help="write a .hpe onto the scanner card")
    sp.add_argument("file", help="path to a .hpe file")
    sp.add_argument("-n", "--name", help="favorites-list name on the scanner "
                    "(default: the .hpe filename)")
    sp.add_argument("--mount", help="scanner volume path (default: auto)")
    sp.add_argument("--no-backup", action="store_true",
                    help="do not back up favorites_lists first")
    sp.add_argument("--yes", action="store_true", help="skip confirmation")
    sp.set_defaults(func=cmd_push)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
