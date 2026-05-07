"""
Pure-Python .po → .mo compiler.

Django's stock `compilemessages` shells out to `msgfmt` from gettext, which
isn't installed by default on Windows or on minimal Linux base images. This
command reads every `.po` under `locale/<lang>/LC_MESSAGES/` and writes a
`.mo` next to it using only the Python stdlib. It also runs at deploy time
on Render so translations are guaranteed up to date.
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


# A `.po` file looks roughly like:
#
#     # comment
#     msgid "Hello"
#     msgstr "Habari"
#
#     msgid ""
#     "multi-line message"
#     msgstr ""
#     "tafsiri ya mistari mingi"
#
# We need to collect the (msgid, msgstr) pairs respecting C-style escapes.

_QUOTED_LINE = re.compile(r'^\s*"(.*)"\s*$')


def _unescape(s: str) -> str:
    """Unescape a quoted .po string body — handle the common escapes."""
    return (
        s.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\r", "\r")
        .replace("\\\"", "\"")
        .replace("\\\\", "\\")
    )


def parse_po(path: Path) -> dict[str, str]:
    """Parse a .po file into {msgid: msgstr}. Skips empty translations."""
    entries: dict[str, str] = {}
    msgid_buf: list[str] | None = None
    msgstr_buf: list[str] | None = None
    state: str | None = None  # "id" or "str"

    def flush():
        if msgid_buf is None or msgstr_buf is None:
            return
        msgid = _unescape("".join(msgid_buf))
        msgstr = _unescape("".join(msgstr_buf))
        if msgstr:  # gettext convention: empty msgstr falls back to msgid
            entries[msgid] = msgstr

    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip() or line.lstrip().startswith("#"):
                if msgid_buf is not None:
                    flush()
                    msgid_buf = msgstr_buf = None
                    state = None
                continue
            if line.startswith("msgid "):
                if msgid_buf is not None:
                    flush()
                msgid_buf = []
                msgstr_buf = None
                state = "id"
                m = _QUOTED_LINE.match(line[len("msgid"):])
                if m:
                    msgid_buf.append(m.group(1))
                continue
            if line.startswith("msgstr "):
                msgstr_buf = []
                state = "str"
                m = _QUOTED_LINE.match(line[len("msgstr"):])
                if m:
                    msgstr_buf.append(m.group(1))
                continue
            m = _QUOTED_LINE.match(line)
            if m:
                if state == "id" and msgid_buf is not None:
                    msgid_buf.append(m.group(1))
                elif state == "str" and msgstr_buf is not None:
                    msgstr_buf.append(m.group(1))
        if msgid_buf is not None:
            flush()

    # The PO header (msgid "" with the metadata) must always be present in the .mo
    # so gettext can pick up the charset. We synthesise a minimal one if missing.
    if "" not in entries:
        entries[""] = "Content-Type: text/plain; charset=UTF-8\n"
    return entries


def write_mo(entries: dict[str, str], out_path: Path) -> int:
    """Write a GNU .mo file. See https://www.gnu.org/software/gettext/manual/html_node/MO-Files.html"""
    # Sort by msgid (gettext expects this for binary search).
    keys = sorted(entries.keys())
    encoded = [(k.encode("utf-8"), entries[k].encode("utf-8")) for k in keys]
    n = len(encoded)

    # Layout:
    #   header (28 bytes) | key offset table | value offset table | strings
    keystart = 7 * 4  # header is 7 32-bit words
    valuestart = keystart + 8 * n
    stringstart = valuestart + 8 * n

    key_offsets: list[tuple[int, int]] = []
    value_offsets: list[tuple[int, int]] = []
    output = bytearray()
    cursor = stringstart
    # Pack keys
    for k, _v in encoded:
        key_offsets.append((len(k), cursor))
        output.extend(k + b"\x00")
        cursor += len(k) + 1
    # Pack values
    for _k, v in encoded:
        value_offsets.append((len(v), cursor))
        output.extend(v + b"\x00")
        cursor += len(v) + 1

    # Header: magic, version, n strings, offset key table, offset value table, hash size, hash offset
    header = struct.pack(
        "Iiiiiii",
        0x950412DE,  # little-endian magic
        0,  # version
        n,
        keystart,
        valuestart,
        0,  # no hash table
        0,
    )
    # Key offset table
    key_table = b"".join(struct.pack("ii", length, offset) for length, offset in key_offsets)
    value_table = b"".join(struct.pack("ii", length, offset) for length, offset in value_offsets)

    out_path.write_bytes(header + key_table + value_table + bytes(output))
    return n


class Command(BaseCommand):
    help = "Compile every locale/<lang>/LC_MESSAGES/*.po into a .mo (no gettext binary required)."

    def handle(self, *args, **options):
        locale_paths = list(getattr(settings, "LOCALE_PATHS", [])) or [Path(settings.BASE_DIR) / "locale"]
        total = 0
        for base in locale_paths:
            base = Path(base)
            if not base.exists():
                continue
            for po in base.rglob("*.po"):
                mo = po.with_suffix(".mo")
                try:
                    entries = parse_po(po)
                    n = write_mo(entries, mo)
                    self.stdout.write(self.style.SUCCESS(f"  OK {po.relative_to(base.parent)} -> {n} entries"))
                    total += 1
                except Exception as exc:  # pragma: no cover — surfaced to operator
                    self.stderr.write(self.style.ERROR(f"  FAIL {po}: {exc}"))
        self.stdout.write(self.style.SUCCESS(f"Compiled {total} .mo file(s)."))
