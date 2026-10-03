"""Local entry point; no database or model credentials are required."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "patent-agent"))
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

if __name__ == "__main__":
    from cli import main
    raise SystemExit(main())
