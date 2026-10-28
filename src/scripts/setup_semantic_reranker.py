"""Pinned public ONNX reranker; all downloads stay in the project."""
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'src/data/semantic'
REVISION = '2cfc18c9415c912f9d8155881c133215df768a70'
MODEL = 'BAAI/bge-reranker-base'
FILES = ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'README.md', 'onnx/model.onnx')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    target = STATE / 'reranker'
    target.mkdir(parents=True, exist_ok=True)
    receipt = target / 'receipt.json'
    previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else None
    if previous and previous['revision'] != REVISION:
        raise ValueError('Refusing to overwrite another model revision')
    files = []
    for name in FILES:
        path = target / name
        url = f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(path.suffix + '.part')
            offset = partial.stat().st_size if partial.exists() else 0
            print('Downloading', name, 'resume_bytes', offset, flush=True)
            request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
            with urllib.request.urlopen(request, timeout=90) as response:
                resume = offset and response.status == 206
                if resume and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                    raise ValueError('Unexpected download range')
                size = offset if resume else 0
                with partial.open('ab' if resume else 'wb') as out:
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > 1_500_000_000:
                            raise ValueError('Unexpected model size')
                        out.write(block)
            partial.replace(path)
        sha = digest(path)
        if previous:
            old = next(x for x in previous['files'] if x['file'] == name)
            if old['sha256'] != sha:
                raise ValueError('Existing model digest mismatch')
        files.append({'file': name, 'bytes': path.stat().st_size, 'sha256': sha, 'source': url})
    receipt.write_text(json.dumps({'model': MODEL, 'revision': REVISION, 'files': files}, indent=2), encoding='utf-8')
    print('Local reranker ready', flush=True)


if __name__ == '__main__':
    main()
