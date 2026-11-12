"""Download pinned, public local-reader assets inside this repository only.

No inference, corpus upload, system install, or modification of user settings.
"""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'src/data/evidence-reader'
ASSETS = (
    dict(name='llama-b12283-bin-win-cpu-x64.zip',
         url='https://github.com/ggml-org/llama.cpp/releases/download/b12283/llama-b12283-bin-win-cpu-x64.zip',
         bytes=25058174, sha256='dafb19ac757bfb7cfc1f91c6e26703264df861c4331e3ef6dc9543ae8d010c5e'),
    dict(name='Qwen3-4B-Q4_K_M.gguf',
         url='https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf',
         bytes=2497280256, sha256='7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5'),
)


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for asset in ASSETS:
        path = DEST / asset['name']
        if not path.exists() or path.stat().st_size != asset['bytes'] or digest(path) != asset['sha256']:
            part = path.with_suffix(path.suffix + '.partial')
            request = urllib.request.Request(asset['url'], headers={'User-Agent': 'yinde-local-reader-experiment'})
            print('Downloading ' + asset['name'], flush=True)
            with urllib.request.urlopen(request, timeout=45) as response, part.open('wb') as f:
                while data := response.read(4 * 1024 * 1024):
                    f.write(data)
            if part.stat().st_size != asset['bytes'] or digest(part) != asset['sha256']:
                raise ValueError('Asset size/hash mismatch: ' + asset['name'])
            part.replace(path)
        if path.suffix == '.zip':
            runtime = DEST / 'runtime'
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if not (runtime / member.filename).resolve().is_relative_to(runtime.resolve()):
                        raise ValueError('Archive path escaped runtime directory')
                archive.extractall(runtime)
        print('Verified ' + asset['name'], flush=True)
    (DEST / 'receipt.json').write_text(json.dumps(dict(assets=ASSETS,
        runtime='llama.cpp b12283 CPU x64', model='Qwen/Qwen3-4B-GGUF',
        mode='non-thinking, offline experiment only'), indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
