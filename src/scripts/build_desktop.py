"""Build a local folder app, with all build/cache writes inside this repository."""
from pathlib import Path
import argparse
import importlib.metadata
import json
import os
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", action="store_true", help="构建候选包，不覆盖运行中的桌面版")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    state = root / "src" / "data" / "desktop"
    deps = state / "deps"
    output = state / "candidate" if args.staging else root / "desktop-dist"
    for target in (state, output, state / "build", state / "pyinstaller-cache"):
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("Desktop build path escapes repository")
    sys.path.insert(0, str(deps))
    for package in ("pywebview", "pyinstaller"):
        importlib.metadata.version(package)
    env = dict(os.environ, PYTHONPATH=str(deps), PYINSTALLER_CONFIG_DIR=str(state / "pyinstaller-cache"),
               TMP=str(state / "build-tmp"), TEMP=str(state / "build-tmp"), PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
           "--name", "Yinde", "--distpath", str(output),
           "--workpath", str(state / "build"), "--specpath", str(state),
           "--paths", str(root / "src"), "--paths", str(deps),
           "--collect-all", "webview", "--collect-submodules", "api",
           "--collect-submodules", "search", "--collect-submodules", "scripts.pipeline",
           "--add-data", f"{root / 'src/scripts/pipeline/corpus_catalog.json'};scripts/pipeline",
           "--add-data", f"{root / 'src/search/history_qa.py'};search",
           "--exclude-module", "pytest", "--exclude-module", "torch",
           "--exclude-module", "transformers", "--exclude-module", "numpy",
           "--exclude-module", "onnxruntime", "--exclude-module", "onnx", "--exclude-module", "tokenizers",
           "--exclude-module", "PyQt5", "--exclude-module", "PyQt6",
           "--exclude-module", "PySide6", str(root / "desktop_entry.py")]
    subprocess.run(cmd, cwd=root, env=env, check=True)
    app = output / "Yinde"
    (app / "yinde-project.json").write_text(json.dumps({"relative_root": os.path.relpath(root, app)}), encoding="utf-8")
    versions = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions(path=[str(deps)])}
    (state / "dependency-versions.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    (app / "BUILD.json").write_text(json.dumps({"python": sys.version, "dependencies": versions}, indent=2), encoding="utf-8")
    print(app / "Yinde.exe")


if __name__ == "__main__":
    main()
