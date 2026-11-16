"""Pinned portable GPU runtime; all downloads, caches and receipts stay in repo."""
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

from scripts.setup_evidence_reader import DEST, digest

ASSET = dict(name='llama-b12283-bin-win-vulkan-x64.zip', bytes=38022362,
             sha256='58c3426f7473bc08135c83333259366521fc940e4ad7bc6ad2b26755cc42cb03',
             url='https://github.com/ggml-org/llama.cpp/releases/download/b12283/llama-b12283-bin-win-vulkan-x64.zip')


def environment(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('LLAMA_', 'GGML_'))}
    for key in ('TMP', 'TEMP', 'LOCALAPPDATA', 'APPDATA', 'XDG_CACHE_HOME'):
        folder = home / 'vulkan-cache' / key.lower()
        folder.mkdir(parents=True, exist_ok=True)
        env[key] = str(folder)
    env['HF_HUB_OFFLINE'] = '1'
    return env


def verified_runtime(home):
    runtime = home / 'runtime-vulkan'
    receipt = json.loads((home / 'receipt-runtime-vulkan.json').read_text(encoding='utf8'))
    if receipt['asset'] != ASSET or receipt['device'] != 'Vulkan0':
        raise ValueError('Unrecognized Vulkan runtime receipt')
    archive = home / ASSET['name']
    if archive.stat().st_size != ASSET['bytes'] or digest(archive) != ASSET['sha256']:
        raise ValueError('Vulkan archive integrity failure')
    if 'llama-server.exe' not in receipt['files']:
        raise ValueError('Vulkan executable missing from receipt')
    for name, expected in receipt['files'].items():
        file = (runtime / name).resolve()
        if not file.is_relative_to(runtime.resolve()) or digest(file) != expected:
            raise ValueError('Vulkan runtime file integrity failure')
    return runtime / 'llama-server.exe', receipt


def main():
    path = DEST / ASSET['name']
    if not path.exists() or path.stat().st_size != ASSET['bytes'] or digest(path) != ASSET['sha256']:
        part = path.with_suffix('.zip.partial')
        req = urllib.request.Request(ASSET['url'], headers={'User-Agent': 'yinde-local-reader-experiment'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=45) as response, part.open('wb') as out:
            while data := response.read(1024 * 1024):
                out.write(data)
        if part.stat().st_size != ASSET['bytes'] or digest(part) != ASSET['sha256']:
            raise ValueError('Vulkan archive integrity failure')
        part.replace(path)
    runtime = DEST / 'runtime-vulkan'
    runtime.mkdir(exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if not (runtime / entry.filename).resolve().is_relative_to(runtime.resolve()):
                raise ValueError('Archive path escapes repository runtime')
        archive.extractall(runtime)
    result = subprocess.run([str(runtime / 'llama-server.exe'), '--list-devices'],
                            cwd=DEST, env=environment(DEST), stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding='utf8', errors='replace',
                            timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = result.stdout + result.stderr
    (DEST / 'vulkan-devices.log').write_text(output, encoding='utf8')
    print(output, flush=True)
    # This task targets the verified Intel GPU; no silent CPU fallback.
    if result.returncode or not any('Vulkan0:' in line and 'Intel' in line for line in output.splitlines()):
        raise RuntimeError('Intel Vulkan0 not available; stop without changing drivers')
    receipt = dict(asset=ASSET, device='Vulkan0', probe_returncode=result.returncode,
                   files={str(p.relative_to(runtime)): digest(p) for p in runtime.rglob('*') if p.is_file()})
    (DEST / 'receipt-runtime-vulkan.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf8')
    print('Verified portable Vulkan runtime; no model loaded.')


if __name__ == '__main__':
    main()
