#!/usr/bin/env python3

from __future__ import annotations

import argparse
import configparser
import itertools
import struct
import sys
from struct import calcsize, unpack_from
from typing import (
    Any,
    BinaryIO,
    Dict,
    Iterable,
    Iterator,
    List,
    Sequence,
    Tuple,
    Union,
)

BitFieldLayout = Tuple[Tuple[str, int], ...]
LayoutItem = Tuple[Union[str, BitFieldLayout], str]
Layout = Sequence[LayoutItem]

ParsedBlock = Dict[str, Union[int, bytes, List[int], Iterable[bytes], Any]]


def read_block_dec(key: Union[str, BitFieldLayout], value: int) -> List[Tuple[str, int]]:
    """Decode bit-field tuples produced by *read_block*."""
    if isinstance(key, str):
        return [(key, value)]

    shift: int = 0
    out: List[Tuple[str, int]] = []

    for name, size in key:
        if not name.startswith("reserved"):
            mask = (1 << size) - 1
            out.append((name, (value >> shift) & mask))
        shift += size

    return out


def read_block(fh: BinaryIO, layout: Layout) -> ParsedBlock:
    """
    Read a binary structure according to *layout*.

    Each entry in *layout* is ``(key, struct_format)`` where *key* is either
    a string (scalar field) or a :pydata:`BitFieldLayout`.

    The returned dict always contains a ``raw`` key with the bytes read.
    """
    fmt: str = "<" + "".join(fmt for _key, fmt in layout)
    size: int = calcsize(fmt)

    blob: bytes = fh.read(size)
    if len(blob) != size:
        raise EOFError("Unexpected EOF while reading a block")

    parsed = unpack_from(fmt, blob)

    decoded: ParsedBlock = dict(
        itertools.chain.from_iterable(
            read_block_dec(k, v)
            for k, v in zip(
                (k for k, fmt in layout if "x" not in fmt),  # skip padding
                parsed,
            )
        )
    )
    decoded["raw"] = list(unpack_from(f"{len(blob)}B", blob))
    return decoded


def read_image_descriptor(fh: BinaryIO) -> ParsedBlock:
    info: ParsedBlock = read_block(
        fh,
        (
            ("x", "H"),
            ("y", "H"),
            ("width", "H"),
            ("height", "H"),
            (
                (
                    ("LCT size", 3),
                    ("reserved", 2),
                    ("sorted", 1),
                    ("interleaced", 1),
                    ("has LCT", 1),
                ),
                "B",
            ),
        ),
    )

    info["LCT len"] = 3 * (2 ** (info["LCT size"] + 1))
    if info["has LCT"]:
        raw = fh.read(info["LCT len"])
        info["colors"] = unpack_from(f"{info['LCT len']}B", raw)

    info["image"] = read_image(fh)
    return info


def read_graphic_control_ext(fh: BinaryIO, _size: int) -> ParsedBlock:
    return read_block(
        fh,
        (
            (
                (
                    ("transparent flag", 1),
                    ("user input", 1),
                    ("disposal method", 3),
                    ("reserved", 3),
                ),
                "B",
            ),
            ("delay", "H"),  # 1/100 s
            ("transparent index", "B"),
            ("terminator", "x"),
        ),
    )


def read_data_chunks(fh: BinaryIO) -> Tuple[bytes, ...]:
    """Read a GIF *data sub-blocks* sequence and return it as tuple of bytes."""
    size_b: bytes = fh.read(1)
    size: int = unpack_from("B", size_b)[0]

    chunks: List[bytes] = []
    while size:
        chunks.append(size_b)
        chunks.append(fh.read(size))
        size_b = fh.read(1)
        size = unpack_from("B", size_b)[0]
    chunks.append(size_b)  # trailing 0-byte
    return tuple(chunks)


def read_image(fh: BinaryIO) -> Iterator[bytes]:
    lzw_min_code_size: bytes = fh.read(1)  # 1 byte
    return itertools.chain((lzw_min_code_size,), read_data_chunks(fh))


def ignore_block(fh: BinaryIO, size: int) -> ParsedBlock:
    return {"raw": list(unpack_from(f"{size + 1}B", fh.read(size + 1)))}


def read_application_ext(fh: BinaryIO, _size: int) -> ParsedBlock:
    info: ParsedBlock = read_block(
        fh,
        (
            ("application id", "8s"),
            ("application id code", "3s"),
        ),
    )

    chunks = read_data_chunks(fh)
    raw = b"".join(chunks)
    info["raw"].extend(unpack_from(f"{len(raw)}B", raw))

    if info["application id"] + info["application id code"] == b"NETSCAPE2.0":
        # Netscape loop extension
        info["loop"] = unpack_from("<xH", chunks[1])[0]
    else:
        info["content"] = chunks
    return info


def read_extension_block(fh: BinaryIO) -> ParsedBlock:
    marker, size = unpack_from("BB", fh.read(2))

    reader = {
        0xF9: read_graphic_control_ext,
        0xFE: ignore_block,  # comment
        0x01: ignore_block,  # plain-text
        0x21: ignore_block,  # app-specific (unused here)
        0xFF: read_application_ext,
    }.get(marker, ignore_block)

    info = reader(fh, size)
    info["ext id"] = marker
    info["raw"] = [marker, size] + info["raw"]
    return info


def add_ini_section(section: str, src: ParsedBlock, cfg: configparser.RawConfigParser) -> None:
    cfg.add_section(section)
    for key, value in src.items():
        key_norm = key.replace(" ", "_")
        if isinstance(value, (int, bytes, str)):
            cfg.set(section, key_norm, str(value))
        else:  # iterable of ints → hex string
            try:
                cfg.set(section, key_norm, "".join(f"{v:02x}" for v in value))  # type: ignore[arg-type]
            except TypeError:
                pass


def read_gif(path: str, only_body: bool) -> None:
    pict_num: int = 1

    in_fh: BinaryIO
    if path == "-":
        in_fh = sys.stdin.buffer
    else:
        in_fh = open(path, "rb")

    with in_fh as fh:
        header: ParsedBlock = read_block(
            fh,
            (
                ("header", "3x"),
                ("version", "3s"),
                ("width", "H"),
                ("height", "H"),
                (
                    (
                        ("GCT size", 3),
                        ("sorted", 1),
                        ("color resolution", 3),
                        ("has GCT", 1),
                    ),
                    "B",
                ),
                ("bgcolor index", "B"),
                ("ratio", "B"),
            ),
        )

        header["GCT len"] = 3 * (2 ** (header["GCT size"] + 1))
        if header["has GCT"]:
            raw = fh.read(header["GCT len"])
            header["colors"] = unpack_from(f"{header['GCT len']}B", raw)

        cfg = configparser.RawConfigParser()
        add_ini_section("global", header, cfg)

        while True:
            nxt = fh.read(1)
            if not nxt:
                break  # EOF

            block_id: int = nxt[0]
            readers = {
                0x2C: read_image_descriptor,
                0x21: read_extension_block,
                0x3B: lambda _fh: {"raw": [0x3B]},  # trailer
            }
            block: ParsedBlock = readers.get(block_id, lambda *_: ignore_block(fh, 0))(fh)

            # `--body` → dump first image body and exit
            if only_body and block_id == 0x2C:
                sys.stdout.buffer.write(b"".join(block["image"]))  # type: ignore[arg-type]
                return

            block["raw"].insert(0, block_id)  # type: ignore[index]
            block["block id"] = block_id
            add_ini_section(str(pict_num), block, cfg)
            pict_num += 1

    cfg.write(sys.stdout)

def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a GIF into an INI structure")
    ap.add_argument("gif", help="path to GIF file or '-' for stdin")
    ap.add_argument(
        "--body",
        action="store_true",
        default=False,
        help="write only raw GIF image body to stdout",
    )
    args = ap.parse_args()
    read_gif(args.gif, args.body)


if __name__ == "__main__":
    main()
