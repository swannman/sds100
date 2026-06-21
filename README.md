# sds100

A command-line replacement for the Windows-only **Uniden Sentinel** software,
for managing **SDS100 / BCDx36HP** favorites lists (`.hpe` files) on macOS and
Linux. Pure Python, no dependencies.

It reads, searches, and edits your existing Sentinel favorites lists — add or
remove channels, talkgroups, groups and systems from the command line instead
of round-tripping through a spreadsheet — and writes valid `.hpe` files that
import back into Sentinel.

> Format note: `.hpe` files are `XOR-0x0C( gzip( tab-delimited text ) )` with a
> trailing `File\tHomePatrol Export File` signature. The format was determined
> by analysis of real export files and the scanner's SD card; every column not
> explicitly understood is preserved byte-for-byte on round-trip. See
> [docs/FORMAT.md](docs/FORMAT.md).

## Install / run

Zero-install (run from the source tree):

```sh
./sds100-cli info "Amateur.hpe"
```

Or install it so `sds100` is on your PATH:

```sh
pip install -e .
sds100 info "Amateur.hpe"
```

(The examples below use `sds100`; substitute `./sds100-cli` if not installed.)

## Viewing

```sh
sds100 info     FILE                 # summary: model, #systems/channels/talkgroups
sds100 ls       FILE                 # one row per system
sds100 show     FILE [SYSTEM]        # tree of groups + channels/talkgroups
sds100 channels FILE [-s SYSTEM]     # table of conventional channels
sds100 talkgroups FILE [-s SYSTEM]   # table of trunk talkgroups
sds100 search   FILE QUERY           # match by name, MHz, or TGID
sds100 export   FILE --format csv|json [-o OUT]
```

## Editing

Edits write back in place and leave a `.bak` (use `-o FILE` to write elsewhere,
`--dry-run` to preview, `--no-backup` to skip the backup):

```sh
# add a conventional channel (frequency in MHz; tone & service type optional)
sds100 add-channel FILE -s "GMRS - USA" -g GMRS \
    -n "Club Repeater" -f 462.6750 --mod NFM --tone 100.0 --service-type Ham

# add a trunk talkgroup
sds100 add-talkgroup FILE -s "Pacific NorthWest DMR" -g Systemwide \
    -n "County Fire" -t 31555 --service-type "Fire Dispatch"

sds100 add-group  FILE -s SYSTEM -n "New Department"
sds100 add-system FILE -n "My Conventional List"        # conventional only

sds100 rm      FILE -n "Channel 19" [-s SYSTEM] [-g GROUP]
sds100 avoid   FILE -n "Channel 19"        # set lockout (Avoid On)
sds100 unavoid FILE -n "Channel 19"
```

`-s/--system` and `-g/--group` are optional when there's only one, and used to
disambiguate otherwise. Names are matched case-insensitively.

**Tones** accept friendly input and are encoded to Sentinel's wire format:
`100.0`/`CTCSS 100.0` → `TONE=C100.0`, `D023`/`DCS 23` → `TONE=D023`,
`NAC=293` (P25), `CC 1` (DMR/NXDN color code). **Service types** accept a name
(`Ham`, `Fire Dispatch`, `Other`, …) or numeric id; default is `Other`.

## Raw access

```sh
sds100 decode FILE [-o out.txt]    # .hpe -> raw tab-delimited text
sds100 encode in.txt out.hpe       # raw text -> .hpe
```

## Scanner (USB)

Connect the SDS100 and put it in **Mass Storage** mode (plug in USB, press `E`
at the on-screen prompt — do it while the scanner is squelched), or pop the
microSD into a card reader. Then:

```sh
sds100 detect                      # find the scanner, list its favorites
sds100 pull [NAME] [-o out.hpe]    # copy a list off the card to a .hpe
sds100 push FILE [-n "List Name"]  # write a .hpe onto the card (needs --yes)
```

`detect`/`pull` are read-only. `push` writes a list to the card:

* If a list with that **name** already exists, only its `.hpd` is overwritten —
  the `f_list.cfg` index is left untouched (safest).
* Otherwise a new `f_NNNNNN.hpd` is created and one index line is appended.

`push` requires `--yes` and backs up `favorites_lists` to a `.bak` folder on the
card first (skip with `--no-backup`). The list name defaults to the `.hpe`
filename; override with `-n`. After pushing, eject the card / exit Mass Storage
mode and power-cycle the radio to load the change.

> The SDS100's USB also has a **serial / PC-control** mode (live remote control
> and monitoring) that does *not* expose the SD card. Only Mass Storage mode
> mounts the card, which is what these commands use.

## Firmware

SDS100 firmware updates are file-based: a single encrypted `.bin` is staged in
the card's `firmware/` folder and the scanner flashes itself on the next
power-up. These commands stage it safely (the scanner does the actual flash):

```sh
sds100 fw-status                   # installed vs. latest (main + sub)
sds100 fw-update --yes             # download the latest main+sub and stage it
sds100 fw-list                     # firmware zips on the Uniden wiki
sds100 fw-fetch 1.23.20            # download a wiki firmware zip
sds100 fw-install FW.zip --yes     # stage a local .zip/.bin/.firm onto the card
```

`fw-update` and `fw-status` pull from Uniden's own firmware server
(`ftp.homepatrol.com`, the same source Sentinel uses), so they see the latest
builds even when the public wiki lags. The SDS100 has **two** processors with
separate firmware — main (`SDS-100_V*.bin`) and sub/DSP
(`SDS-100-SUB_V*.firm`) — and `fw-update` stages both by default (`--no-sub`,
`--main VER`, `--sub VER` to control).

`fw-install` enforces the safety rules from Uniden's update Readme:

* the firmware **model must match** the scanner (refuses an SDS200 image on an
  SDS100 unless `--force`);
* only **one** firmware version is kept on the card (any prior staged binary is
  cleared);
* the `CityTable`/`ZipTable` `.dat` files are **never** touched (the scanner
  won't boot without them);
* a backup of `firmware/` is made first.

After staging: charge the battery, **eject** the card, then **press & hold the
power key** on the SDS100 to start the update — don't interrupt it. Newer
releases may only be available through Uniden's Sentinel rather than the wiki.

## Development

```sh
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

Round-trip tests run against your real `.hpe` files if present and verify that
parse → edit → serialize is byte-stable.
