#!/usr/bin/env python3
"""Wrap the artifact-shaped page into a standalone document for docs/.

The artifact host supplies a doctype, head and small reset; a self-hosted copy
has to say all of it itself. Everything else is copied verbatim, so there is
only ever one source of truth for the page.
"""
import pathlib
import shutil

root = pathlib.Path(__file__).parent
src = (root / "index.html").read_text()

# the link/meta lines live at the top of the artifact file; lift them into <head>
lines = src.splitlines()
head_lines, body_lines, in_head = [], [], True
for line in lines:
    if in_head and (line.startswith("<link") or line.startswith("<title") or line.startswith("<meta")):
        if "claude:viewport-fit-sides" not in line:
            head_lines.append(line)
        continue
    if line.strip():
        in_head = False
    body_lines.append(line)

head = "\n".join(head_lines)
body = "\n".join(body_lines).strip()

out = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="toss: highlight something, hold a key, it lands on your other device. Over your own wifi, encrypted, with no server in between.">
<meta name="color-scheme" content="light dark">
<meta property="og:title" content="toss">
<meta property="og:description" content="Highlight something. Hold a key. It is on your phone before you let go.">
<meta property="og:type" content="website">
<meta property="og:image" content="icon-512.png">
<meta name="twitter:card" content="summary">
<link rel="icon" href="icon-192.png" type="image/png">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<meta name="theme-color" content="#fbfbfd" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">
{head}
<style>
  /* what the artifact host would otherwise provide */
  :root {{
    padding-top: env(safe-area-inset-top, 0px);
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }}
  [hidden] {{ display: none !important; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""

docs = root / "docs"
docs.mkdir(exist_ok=True)
(docs / "index.html").write_text(out)
for folder in ("css", "js"):
    shutil.rmtree(docs / folder, ignore_errors=True)
    shutil.copytree(root / folder, docs / folder)
for asset in ("screenshot-phone.png", "shot-terminal.png"):
    shutil.copy(root / asset, docs / asset)
print(f"docs/index.html  {len(out) / 1024:.1f} KB  +  css/ js/ + assets")
