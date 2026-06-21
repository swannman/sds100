# The Sentinel `.hpe` favorites-export format

Determined by analysis of real `.hpe` export files (BCDx36HP / SDS100 /
SDS200) and the scanner's on-card list files, and validated by byte-for-byte
round trips. This documents the on-disk format so the codec can be trusted.

## File framing

A `.hpe` file is, end to end:

```
hpe_bytes = scramble( gzip( inner_text + signature ) )
```

reading is the same steps in reverse (unscramble = scramble; it is
self-inverse). Concretely:

* **Scramble**: XOR each byte with `0x0C`. Self-inverse.
* **gzip**: standard RFC-1952; the header is written with `mtime=0`.
* **inner_text**: UTF-8, **CRLF** line endings, ending with the signature line
  `File\tHomePatrol Export File\r\n`.

## Record text

Each line is one tab-separated record; field 0 is the record *tag*. Records
nest by document order:

```
TargetModel   \t BCDx36HP                    # header
FormatVersion \t 1.00                        # header
Conventional  ...                            # a conventional system
  DQKs_Status ...                            #   dept quick-key status (102 cols)
  C-Group     ...                            #   group / department
    Rectangle ...                            #     geo filter
    C-Freq    ...                            #     conventional channel
Trunk         ...                            # a trunked system
  DQKs_Status ...
  Site        ...                            #   trunk site
    BandPlan_Mot ...                         #     Motorola band plan (per site)
    T-Freq    ...                            #     site control/voice frequency
  T-Group     ...                            #   group / department
    TGID      ...                            #     talkgroup
File          \t HomePatrol Export File      # signature footer
```

### Field counts (arity)

| Tag           | Fields |
|---------------|:------:|
| TargetModel   | 2  |
| FormatVersion | 2  |
| Conventional  | 15 |
| C-Group       | 11 |
| Rectangle     | 6  |
| C-Freq        | 18 |
| DQKs_Status   | 102 |
| Trunk         | 22 |
| BandPlan_Mot  | 26 |
| Site          | 19 |
| T-Freq        | 8  |
| T-Group       | 10 |
| TGID          | 17 |
| File          | 2  |

### Meaningful columns (0-based, including the tag at 0)

Only the human-relevant columns are interpreted; all others are preserved
verbatim.

* **C-Freq** — `3` name, `4` avoid, `5` frequency (Hz), `6` modulation,
  `7` tone (audio option), `8` service-type id.
* **TGID** — `3` name, `4` avoid, `5` talkgroup id, `7` service-type id,
  `16` slot / color-code.
* **Conventional / Trunk** — `3` name, `4` avoid, `6` system type.
* **C-Group / T-Group** — `3` name, `4` avoid.
* **Site** — `3` name, `4` avoid, `5` lat, `6` lon.
* **T-Freq** — `4` avoid, `5` frequency (Hz), `6` lcn/slot, `7` usage.
* **Rectangle** — `2..5` lat1/lon1/lat2/lon2.

Frequencies are integer **Hz** (e.g. `462550000` = 462.5500 MHz).
`avoid` is `Off`/`On` (the channel lockout flag).

### Tone (audio option) encoding

Field 7 of C-Freq (and the analogous field elsewhere) is a tagged string:

* CTCSS — `TONE=C<freq>` (e.g. `TONE=C156.7`)
* DCS   — `TONE=D<code>` (e.g. `TONE=D023`)
* P25   — `NAC=<hex>` or `NAC=Srch`
* DMR/NXDN — `ColorCode=<n>`, `RAN=<n>`, `Area=<n>`, `CommonId=<n>`
* empty — no tone

### Service types (ids)

The numeric service-type id used by channel/talkgroup records:

```
 1 Multi-Dispatch   2 Law Dispatch    3 Fire Dispatch    4 EMS Dispatch
 6 Multi-Tac        7 Law Tac         8 Fire-Tac         9 EMS-Tac
11 Interop         12 Hospital       13 Ham             14 Public Works
15 Aircraft        16 Federal        17 Business        20 Railroad
21 Other           22 Multi-Talk     23 Law Talk        24 Fire-Talk
25 EMS-Talk        26 Transportation 29 Emergency Ops   30 Military
31 Media           32 Schools        33 Security        34 Utilities
37 Corrections
```

## On the scanner's microSD card

Mount the card via Mass Storage mode (connect USB, press **E**) or a card
reader. The card has a top-level `BCDx36HP` folder. Favorites live at:

```
<volume>/BCDx36HP/favorites_lists/f_list.cfg       index of lists
<volume>/BCDx36HP/favorites_lists/f_NNNNNN.hpd      one plain-text list per file
<volume>/BCDx36HP/HPDB/                              full RadioReference database
<volume>/BCDx36HP/{profile.cfg,app_data.cfg,scanner.inf,...}
```

The `.hpd` files use the **same tab-delimited record text** as the inner `.hpe`
text, but stored **plain** (not gzip/scrambled) with **CRLF** endings and
**no** `File` signature line. List files are named `f_%06d.hpd`.

`f_list.cfg` is the index. After the usual `TargetModel`/`FormatVersion`
header, one line per list:

```
F-List <display name> <f_NNNNNN.hpd> <flag> ×115
```

The 115 flags are the list's quick-key assignments (`On`/`Off`). Replacing an
existing list only rewrites its `.hpd` (the index already points at it);
adding a new list appends one `F-List` line.

*Confirmed against a physical SDS100 (main firmware 1.21.00): every on-card
`.hpd` round-trips byte-identically through the model.* Note the USB also
offers a **serial / PC-control** mode (a CDC port speaking the remote-control
protocol) which does **not** expose the SD card — only Mass Storage mode does.
