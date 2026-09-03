#!/usr/bin/env python3
"""Check internal links and required landmarks in site/."""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "site"
REQUIRED = [
    "index.html",
    "architecture.html",
    "installation.html",
    "quickstart.html",
    "deployment.html",
    "limitations.html",
    "404.html",
    "robots.txt",
    "sitemap.xml",
    "assets/site.css",
    "assets/site.js",
    "assets/favicon.svg",
]


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.langs: list[str] = []
        self.ids: set[str] = set()
        self.tags: list[str] = []
        self.title = ""
        self._in_title = False
        self.has_skip = False
        self.has_main = False
        self.has_viewport = False
        self.lang = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: v or "" for k, v in attrs}
        self.tags.append(tag)
        if tag == "html":
            self.lang = ad.get("lang", "")
        if tag == "a" and "href" in ad:
            self.hrefs.append(ad["href"])
            if ad.get("class") == "skip-link" or "skip-link" in ad.get("class", "").split():
                self.has_skip = True
        if tag == "main" or ad.get("id") == "main":
            self.has_main = True
        if ad.get("id"):
            self.ids.add(ad["id"])
        if tag == "meta" and "viewport" in ad.get("name", ""):
            self.has_viewport = True
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def resolve(page: Path, href: str) -> Path | None:
    if href.startswith(("http://", "https://", "mailto:", "data:")):
        return None
    href = href.split("#", 1)[0]
    if not href or href == "./":
        return ROOT / "index.html"
    return (page.parent / href).resolve()


def main() -> int:
    errors: list[str] = []
    for rel in REQUIRED:
        if not (ROOT / rel).is_file():
            errors.append(f"missing {rel}")

    html_files = sorted(ROOT.glob("*.html"))
    forbidden = ["lorem ipsum", "QPS", "已在生产验证", "在线演示环境"]
    live_demo_phrases = ["打开本站即可使用产品", "本站提供实时终端"]

    for path in html_files:
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        for phrase in forbidden + live_demo_phrases:
            if phrase.lower() in lower:
                errors.append(f"{path.name}: forbidden phrase {phrase!r}")
        parser = Page()
        parser.feed(text)
        if path.name != "404.html" and parser.lang != "zh-CN":
            errors.append(f"{path.name}: html lang is {parser.lang!r}")
        if not parser.has_viewport:
            errors.append(f"{path.name}: missing viewport")
        if not parser.has_main:
            errors.append(f"{path.name}: missing main landmark")
        if path.name != "404.html" and not parser.has_skip:
            errors.append(f"{path.name}: missing skip link")
        if "WebCoder" not in parser.title and path.name != "404.html":
            # 404 also has WebCoder
            pass
        if "WebCoder" not in parser.title:
            errors.append(f"{path.name}: title missing WebCoder")
        for href in parser.hrefs:
            if href.startswith("#"):
                target = href[1:]
                if target and target not in parser.ids:
                    errors.append(f"{path.name}: broken in-page #{target}")
                continue
            dest = resolve(path, href)
            if dest is None:
                continue
            if not dest.exists():
                errors.append(f"{path.name}: broken href {href}")

    sitemap = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
    for name in ["architecture.html", "installation.html", "quickstart.html", "deployment.html", "limitations.html"]:
        if name not in sitemap:
            errors.append(f"sitemap missing {name}")

    robots = (ROOT / "robots.txt").read_text(encoding="utf-8")
    if "unstoppablecurry.github.io/WebCoder/sitemap.xml" not in robots:
        errors.append("robots.txt sitemap URL mismatch")

    if errors:
        print("FAIL")
        for e in errors:
            print(" -", e)
        return 1
    print(f"OK {len(html_files)} html pages, all required files present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
