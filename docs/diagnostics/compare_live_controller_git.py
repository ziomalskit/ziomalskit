"""Compare current running controller to pinned GitHub source; code-only short diff."""
import difflib,hashlib,pathlib,urllib.request
p=pathlib.Path("/workspace/H3_VAST_MOBILE/app/main.py")
local=p.read_text()
url=("https://raw.githubusercontent.com/ziomalskit/ziomalskit/"
     "76e7cf84416287ec6577654cd9f6b67f2325c7ef/h3/app/main.py")
with urllib.request.urlopen(url,timeout=25) as response:source=response.read().decode("utf-8")
expected="181c5cba152cdf1fc13b1dcd7107a6341fa174fdb9fc5bca4ee1d74f4633094e"
assert hashlib.sha256(source.encode()).hexdigest()==expected
print("MATCH",local==source,"DIFFERENT_BYTES",len(local.encode())-len(source.encode()))
diff=list(difflib.unified_diff(source.splitlines(),local.splitlines(),
        fromfile="GitHub branch",tofile="Live controller",lineterm=""))
print("DIFF_LINE_COUNT",len(diff))
for line in diff[:80]:
    if len(line)<260 and not any(word in line.lower() for word in ('password','private_key','api_key','authorization')):
        print("DIFF",line,flush=True)
