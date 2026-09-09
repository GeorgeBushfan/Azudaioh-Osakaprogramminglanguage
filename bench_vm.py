"""Time the Osaka lexer (run on the Python VM) over the bundle sources."""
import time
from pathlib import Path

from selfhost_harness import invoke

names = ("support.saka", "diagnostic.saka", "token.saka", "lexer.saka")
text = "\n".join(
    Path("selfhost", name).read_text(encoding="utf-8") for name in names[:3]
)
started = time.time()
result = invoke("lex_source", [text], names)
elapsed = time.time() - started
print("ok:", result["ok"],
      "tokens:", len(result.get("tokens") or []),
      f"secs: {elapsed:.1f}")