#!/usr/bin/env python3
"""Validate Instantly-style spintax: {a|b|c} blocks, {{merge_vars}} left intact.

Usage: spintax_check.py FILE_OR_- [--original ORIGINAL] [--sample N]
Exit 1 on any failure.
"""
from __future__ import annotations

import argparse
import random
import re
import sys

VAR_RE = re.compile(r"\{\{\s*[\w.\-]+\s*\}\}")
URL_RE = re.compile(r"https?://[^\s)>\]|}]+")
NUM_RE = re.compile(r"\d[\d.,%]*")


def mask_vars(text: str) -> tuple[str, list[str]]:
    """Replace {{vars}} with sentinels so single-brace parsing ignores them."""
    vars_: list[str] = []

    def sub(m: re.Match) -> str:
        vars_.append(m.group(0))
        return f"\x00{len(vars_) - 1}\x00"

    return VAR_RE.sub(sub, text), vars_


def unmask(text: str, vars_: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: vars_[int(m.group(1))], text)


def parse(text: str):
    """Return (nodes, errors). nodes: str | list[list[nodes]] (a block = options)."""
    errors: list[str] = []
    pos = 0

    def parse_seq(depth: int, in_block: bool):
        nonlocal pos
        seq: list = []
        buf: list[str] = []
        while pos < len(text):
            c = text[pos]
            if c == "{":
                if buf:
                    seq.append("".join(buf))
                    buf = []
                pos += 1
                if depth >= 2:
                    errors.append("nesting deeper than one level")
                options = []
                while True:
                    opt, end = parse_option(depth + 1)
                    options.append(opt)
                    if end == "|":
                        continue
                    if end == "}":
                        break
                    errors.append("unclosed '{'")
                    break
                seq.append(options)
            elif c in "}|" and in_block:
                if buf:
                    seq.append("".join(buf))
                return seq, c
            elif c == "}":
                errors.append(f"stray '}}' at offset {pos}")
                pos += 1
            elif c == "|" and not in_block:
                buf.append(c)  # literal pipe outside a block
                errors.append(f"stray '|' outside a block at offset {pos}")
                pos += 1
            else:
                buf.append(c)
                pos += 1
        if buf:
            seq.append("".join(buf))
        return seq, None

    def parse_option(depth: int):
        nonlocal pos
        seq, end = parse_seq(depth, True)
        if end:
            pos += 1
        return seq, end

    nodes, _ = parse_seq(0, False)
    return nodes, errors


def render(nodes, rng: random.Random) -> str:
    out = []
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
        else:
            out.append(render(rng.choice(n), rng))
    return "".join(out)


def count(nodes) -> int:
    total = 1
    for n in nodes:
        if not isinstance(n, str):
            total *= sum(count(opt) for opt in n)
    return total


def blocks(nodes) -> int:
    return sum(1 + sum(blocks(o) for o in n) for n in nodes if not isinstance(n, str))


def check_blocks(nodes, errors: list[str]) -> None:
    for n in nodes:
        if isinstance(n, str):
            continue
        flat = ["".join(o_s for o_s in o if isinstance(o_s, str)) for o in n]
        if len(n) < 2:
            errors.append("block with a single option (not a variation)")
        stripped = [f.strip() for f in flat]
        if len(set(stripped)) < len(stripped):
            errors.append(f"duplicate options in a block: {flat}")
        for o in n:
            check_blocks(o, errors)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--original")
    ap.add_argument("--sample", type=int, default=0)
    a = ap.parse_args()

    raw = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
    masked, vars_ = mask_vars(raw)
    nodes, errors = parse(masked)
    check_blocks(nodes, errors)

    rng = random.Random()
    if a.original:
        orig = open(a.original, encoding="utf-8").read()
        need_vars = set(VAR_RE.findall(orig))
        need_tokens = set(URL_RE.findall(orig)) | set(NUM_RE.findall(orig))
        # every variant path must keep them: sample many renders
        for _ in range(200):
            r = unmask(render(nodes, rng), vars_)
            missing = [t for t in need_vars | need_tokens if t not in r]
            if missing:
                errors.append(f"a variant drops: {sorted(missing)}")
                break

    combos, nblocks = count(nodes), blocks(nodes)
    for e in dict.fromkeys(errors):
        print(f"FAIL: {e}")
    if errors:
        return 1
    print(f"OK - {combos} combinations - {nblocks} blocks")
    for i in range(a.sample):
        print(f"\n--- sample {i + 1} ---\n{unmask(render(nodes, rng), vars_)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
