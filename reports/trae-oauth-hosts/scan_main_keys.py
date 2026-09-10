from pathlib import Path
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace")
keys = [
    "https://api.", "https://www.", "baseURL", "baseUrl", "TRAECN", "traeapi",
    "ZIJIEAPI", "region", "CN_HOST", "apiHost", "cloudide/api", "/oauth/",
    "ExchangeToken", "GetUserToken", "Authorize", "auth_callback",
    "127.0.0.1", "localhost", "mssdk", "secsdk", "bdms", "host:",
]
low = text
for k in keys:
    c = text.count(k) if k.islower() is False else text.lower().count(k.lower())
    print("COUNT", k, text.lower().count(k.lower()))

print("\n==== interesting windows ====")
for k in ["https://api.", "baseURL", "baseUrl", "ZIJIEAPI", "traeapi", "mssdk", "cloudide/api", "ExchangeToken", "127.0.0.1", "host:\"https", "host:'https"]:
    start=0
    n=0
    while n<8:
        i=text.find(k, start)
        if i<0:
            i=text.lower().find(k.lower(), start)
            if i<0: break
        snip=text[max(0,i-100):i+220].replace("\n"," ")
        print(k, i)
        print(" ", snip)
        print()
        start=i+len(k)
        n+=1
