"""Small formatting helpers shared by the CLI."""

from __future__ import annotations


def hz_to_mhz(hz: str | int) -> str:
    """Format a frequency in Hz as a trimmed MHz string ('462.5500')."""
    try:
        n = int(hz)
    except (TypeError, ValueError):
        return str(hz)
    if n == 0:
        return ""
    return f"{n / 1_000_000:.5f}".rstrip("0").rstrip(".")


def mhz_to_hz(mhz: str | float) -> int:
    """Parse a user-supplied MHz value into integer Hz."""
    return round(float(str(mhz).replace(",", "").strip()) * 1_000_000)


def table(rows: list[list[str]], headers: list[str]) -> str:
    """Render a simple left-aligned text table."""
    cols = list(zip(*([headers] + rows))) if rows else [[h] for h in headers]
    widths = [max(len(str(c)) for c in col) for col in cols]
    out = []
    sep = "  "
    out.append(sep.join(str(h).ljust(w) for h, w in zip(headers, widths)))
    out.append(sep.join("-" * w for w in widths))
    for row in rows:
        out.append(sep.join(str(c).ljust(w) for c, w in zip(row, widths)))
    return "\n".join(out)
