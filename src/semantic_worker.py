"""Private stdin/stdout protocol: local inference, never a public service."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "src/data/semantic"
sys.path.insert(0, str(STATE / "deps"))
for name in ("HF_HOME", "HUGGINGFACE_HUB_CACHE", "TORCH_HOME"):
    os.environ[name] = str(STATE / "cache" / name.lower())
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                  DO_NOT_TRACK="1", TMP=str(STATE / "tmp"), TEMP=str(STATE / "tmp"))


def main():
    import traceback
    from search.semantic_local import LocalIndex
    try:
        index = LocalIndex(STATE, ROOT / "src/data/database/history.db")
        print(json.dumps({"ready": True, "protocol": 1, "encoder_version": index.manifest["encoder_version"]}), flush=True)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ready": False, "error": "本地语义模型或索引加载失败，请查看语义日志"}), flush=True)
        return 1
    reranker = None
    punctuated_index = None
    for line in iter(lambda: sys.stdin.readline(1_000_001), ''):
        try:
            if len(line) > 1_000_000:
                raise ValueError("request too large")
            request = json.loads(line)
            if request.get("protocol") != 1:
                raise ValueError("unsupported local inference protocol")
            if request.get('operation', 'query') == 'rerank':
                if reranker is None:
                    from search.semantic_reranker import Reranker
                    reranker = Reranker(STATE)
                response = reranker.score(request['query'], request['texts'])
            elif request.get('operation') == 'punctuated_query':
                if punctuated_index is None:
                    from search.punctuated_search import DenseIndex
                    punctuated_index = DenseIndex(index.encoder)
                response = punctuated_index.query(request['query'],request.get('book'),request.get('top_k',60))
            elif request.get('operation', 'query') == 'query':
                response = index.query(request["query"], request.get("book"), request.get("top_k", 30))
            else:
                raise ValueError('Unknown operation')
        except Exception:
            traceback.print_exc(file=sys.stderr)
            response = {"error": "语义检索失败，请检查索引状态或查看本机语义日志"}
        print(json.dumps(response, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
