#!/usr/bin/env python3
"""Builds ../index.html from index.template.html by inlining the SVG figures from ../diagrams.

Usage: python assemble.py
"""
import pathlib
import re

here = pathlib.Path(__file__).resolve().parent
diagrams = here.parent / "diagrams"
out = here.parent / "index.html"
tpl = (here / "index.template.html").read_text(encoding="utf-8")


def inline(match):
    svg = (diagrams / match.group(1)).read_text(encoding="utf-8")
    svg = re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)
    tag = re.search(r"<svg\b[^>]*>", svg).group(0)
    width = float(re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) [\d.]+"', tag).group(1))
    new = re.sub(r'\s(width|height|style)="[^"]*"', "", tag)
    new = new[:-1] + f' style="max-width:{round(width)}px">'
    return svg.replace(tag, new, 1)


html, n = re.subn(r"\{\{FIG:([^}]+)\}\}", inline, tpl)
out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({n} figures inlined)")
