#!/usr/bin/env python3
"""Fetch official pages as readable text (for hand-curated station files).

Usage: python3 fetch.py [-a ja] URL [URL ...]
Prints status, title, and the page text with links inlined as "text -> href" so the
citation URL and the verbatim wording can be captured in one pass.
"""
import html
import re
import subprocess
import sys
import urllib.parse

UA = "eki-play-research/1.0 (https://github.com/ooep/eki-play)"


def get(url, lang="ja"):
    r = subprocess.run(["curl", "-sSL", "--max-time", "30", "-A", UA,
                        "-H", f"Accept-Language: {lang}",
                        "-w", "\n@@%{http_code}\n@%{url_effective}", url],
                       capture_output=True, text=True)
    body, _, tail = r.stdout.rpartition("\n@@")
    lines = tail.split("\n@")
    code = (lines[0] or "").strip() or "000"
    eff = lines[1].strip() if len(lines) > 1 else url
    return code, eff, body


def text(html_src):
    s = re.sub(r"(?is)<(script|style|noscript|svg|header|footer)[^>]*>.*?</\1>", " ", html_src)
    links = []

    def keep_link(m):
        href, label = m.group(1), re.sub("<[^>]+>", "", m.group(2))
        links.append((re.sub(r"\s+", " ", label).strip(), href.strip()))
        return m.group(0)

    s = re.sub(r'(?is)<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', keep_link, s)
    hrefs = dict()
    for lab, href in links:
        hrefs.setdefault(lab, href)
    s = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xA0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    out = []
    for ln in s.split("\n"):
        ln = ln.strip()
        if len(ln) < 2:
            continue
        out.append(ln)
    return "\n".join(out), hrefs


def main():
    args = sys.argv[1:]
    lang = "ja"
    if args[:2] == ["-a", "ja"] or args[:2] == ["-a", "en"] or args[:2] == ["-a", "zh"]:
        lang = args[1]
        args = args[2:]
    cap = 3800
    for u in args:
        code, eff, body = get(u, lang)
        t, hrefs = text(body)
        print(f"\n===== {code} {eff}  ({len(t)} chars) =====")
        print(t[:cap])
        if hrefs:
            print("----- links -----")
            for lab, href in list(hrefs.items())[:40]:
                if lab:
                    print(f"  {lab[:40]} -> {href}")


if __name__ == "__main__":
    main()
