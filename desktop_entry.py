"""PyInstaller and source entry; dependencies live inside this project only."""
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root / "src" / "data" / "desktop" / "deps"))

from desktop.main import main

if __name__ == "__main__":
    raise SystemExit(main())
