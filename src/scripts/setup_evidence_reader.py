"""Download pinned, public local-reader assets inside this repository only.

No inference, corpus upload, system install, or modification of user settings.
"""
import argparse
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

QWEN25_REVISION = 'b466e1f8c07172155743e8e1307507d8a4f91fbd'
QWEN25_SHARDS = tuple(
    dict(name=f'qwen2.5-14b-instruct-q4_k_m-{i:05d}-of-00003.gguf',
         bytes=size, sha256=sha,
         url=f'https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-GGUF/resolve/{QWEN25_REVISION}/'
             f'qwen2.5-14b-instruct-q4_k_m-{i:05d}-of-00003.gguf')
    for i, (size, sha) in enumerate((
        (3991999872, 'a09ea5e7b1eafb1b30b241726c3cc3c905c96f14ad41e246ffa5f44e53904f68'),
        (3989373504, '21b9457d079680d284e90ef69607c4b2d8ef64a09d4729cb7b5e1357bdba41ae'),
        (1006737120, 'c8d37006760a387a35216e070e6664d7da927f10be8eb870fef2e3d4833d9976'),
    ), 1)
)

MODELS = {
    'qwen3-4b': dict(id='Qwen/Qwen3-4B-GGUF', asset=ASSETS[1], receipt='receipt.json'),
    'qwen3-8b': dict(id='Qwen/Qwen3-8B-GGUF', receipt='receipt-qwen3-8b.json', asset=dict(
        name='Qwen3-8B-Q4_K_M.gguf', bytes=5027783488,
        sha256='d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785',
        url='https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf')),
    'qwen2.5-14b-instruct': dict(id='Qwen/Qwen2.5-14B-Instruct-GGUF',
        receipt='receipt-qwen2.5-14b-instruct.json', asset=QWEN25_SHARDS[0],
        additional_assets=QWEN25_SHARDS[1:]),
    'qwen3.5-9b': dict(id='unsloth/Qwen3.5-9B-GGUF',
        receipt='receipt-qwen3.5-9b.json', mode='native thinking, isolated experiment only', asset=dict(
            name='Qwen3.5-9B-Q5_K_M.gguf', bytes=6577841376,
            sha256='dc2a39aef291f91a9116ad214058da0d86eb648743a124bd8c333787c4b9c91c',
            url='https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q5_K_M.gguf')),
}


def model_spec(key):
    spec = MODELS[key]
    # llama.cpp loads the first shard and discovers its siblings. Verify every
    # shard before startup; old single-file receipts remain byte-for-byte valid.
    return spec, (ASSETS[0], spec['asset'], *spec.get('additional_assets', ()))


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def download(asset, path):
    # Separate staging file preserves fragments from interrupted older clients.
    part = path.with_suffix('.download.partial')
    if not part.exists():
        fragments = [p for p in (path.with_suffix(path.suffix + '.partial'),
                                  path.with_suffix('.parallel.partial'))
                     if p.is_file() and 0 < p.stat().st_size <= asset['bytes']]
        if fragments:
            source = max(fragments, key=lambda p: p.stat().st_size)
            remaining = source.stat().st_size
            with source.open('rb') as old, part.open('xb') as out:
                while remaining:
                    data = old.read(min(4 * 1024 * 1024, remaining))
                    if not data:
                        raise ValueError('Source fragment changed during copy')
                    out.write(data)
                    remaining -= len(data)
    count = part.stat().st_size if part.exists() else 0
    if count > asset['bytes']:
        raise ValueError('Oversized download fragment: ' + asset['name'])
    if count < asset['bytes']:
        headers = {'User-Agent': 'yinde-local-reader-experiment', 'Accept-Encoding': 'identity'}
        if count:
            headers['Range'] = f'bytes={count}-'
        request = urllib.request.Request(asset['url'], headers=headers)
        print(f'Downloading {asset["name"]} from {count}/{asset["bytes"]}', flush=True)
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.status == 206:
                expected = f'bytes {count}-{asset["bytes"] - 1}/{asset["bytes"]}'
                if response.headers.get('Content-Range') != expected:
                    raise ValueError('Download range mismatch; fragment preserved')
                mode = 'ab'
            elif response.status == 200:
                # Server ignored Range: restart, never append a whole response.
                mode, count = 'wb', 0
            else:
                raise ValueError('Unexpected download status')
            progress = count // (256 * 1024 * 1024)
            with part.open(mode) as out:
                while data := response.read(4 * 1024 * 1024):
                    if count + len(data) > asset['bytes']:
                        raise ValueError('Oversized download response')
                    out.write(data)
                    count += len(data)
                    if count // (256 * 1024 * 1024) > progress:
                        progress = count // (256 * 1024 * 1024)
                        print(f'{count}/{asset["bytes"]} bytes', flush=True)
    if part.stat().st_size != asset['bytes'] or digest(part) != asset['sha256']:
        raise ValueError('Asset size/hash mismatch: ' + asset['name'])
    part.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=tuple(MODELS), default='qwen3-4b')
    args = parser.parse_args()
    spec, assets = model_spec(args.model)
    DEST.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        path = DEST / asset['name']
        if not path.exists() or path.stat().st_size != asset['bytes'] or digest(path) != asset['sha256']:
            download(asset, path)
        if path.suffix == '.zip' and (args.model == 'qwen3-4b' or not (DEST/'runtime/llama-server.exe').is_file()):
            runtime = DEST / 'runtime'
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if not (runtime / member.filename).resolve().is_relative_to(runtime.resolve()):
                        raise ValueError('Archive path escaped runtime directory')
                archive.extractall(runtime)
        print('Verified ' + asset['name'], flush=True)
    (DEST / spec['receipt']).write_text(json.dumps(dict(assets=assets,
        runtime='llama.cpp b12283 CPU x64', model=spec['id'],
        mode=spec.get('mode', 'non-thinking, offline experiment only')), indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
