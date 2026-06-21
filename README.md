# sds100

A command-line replacement for the Windows-only **Uniden Sentinel** software,
for managing **SDS100 / BCDx36HP** favorites lists (`.hpe` files) on macOS and
Linux. Pure Python, no dependencies.

It reads, searches, and edits your existing Sentinel favorites lists — add or
remove channels, talkgroups, groups and systems from the command line instead
of round-tripping through a spreadsheet — and writes valid `.hpe` files that
import back into Sentinel.

> Format note: `.hpe` files are `XOR-0x0C( gzip( tab-delimited text ) )` with a
> trailing `File\tHomePatrol Export File` signature. The codec was
> reverse-engineered from `BCDx36HP_Sentinel.exe` v3.00.01; every column not
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

Connect the SDS100, choose **Mass Storage** on the radio so its microSD mounts,
then:

```sh
sds100 detect                      # find the scanner, list its favorites
sds100 pull [NAME] [-o out.hpe]    # copy a list off the card to a .hpe
sds100 push FILE                   # write a list to the card  (see below)
```

`detect` and `pull` are read-only and safe. **`push` is not yet enabled**: the
on-card `f_list.cfg` index format must be confirmed against a physical scanner
before writing, to avoid corrupting the list menu. Running `push` makes a
backup of the card's `FavoriteLists` folder and explains the next step. Once
you connect the radio and share an `f_list.cfg`, push can be finalized.

## Development

```sh
python -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

Round-trip tests run against your real `.hpe` files if present and verify that
parse → edit → serialize is byte-stable.
