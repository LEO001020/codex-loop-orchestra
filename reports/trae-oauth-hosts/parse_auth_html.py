from pathlib import Path
import json, re
from urllib.parse import urljoin, urlparse
html_path = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html")
text = html_path.read_text(encoding="utf-8", errors="replace")
HOST_RE = re.compile(r"https?://[a-zA-Z0-9._:-]+")
ATTR_RE = re.compile(r"(?:src|href|action|data-src|data-href)=['\"]([^'\"]+)", re.I)
SCRIPT_SRC = re.compile(r"<script[^>]+src=['\"]([^'\"]+)['\"]", re.I)
LINK_HREF = re.compile(r"<link[^>]+href=['\"]([^'\"]+)['\"]", re.I)
URL_IN_JS = re.compile(r"['\"](https?://[^'\"]+)['\"]")
hosts = sorted(set(HOST_RE.findall(text)))
attrs = ATTR_RE.findall(text)
scripts = SCRIPT_SRC.findall(text)
links = LINK_HREF.findall(text)
js_urls = URL_IN_JS.findall(text)
out = {
  "len": len(text),
  "hosts": hosts,
  "attrs": attrs,
  "scripts": scripts,
  "links": links,
  "js_urls": sorted(set(js_urls)),
  "head": text[:2000],
}
Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization_parse.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote parse json", len(text))
print("HOSTS")
for h in hosts:
    print(h)
print("SCRIPTS")
for s in scripts:
    print(s)
print("LINKS")
for s in links:
    print(s)
