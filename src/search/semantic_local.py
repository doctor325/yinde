"""Local-only encoder and immutable semantic index. Imported by the worker, not UI."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from search.semantic_chunks import embedding_text

QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
ENCODER_VERSION = "bge-cls-ort-fp32-v2"


def identity(path):
    stat = path.stat()
    return {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class Encoder:
    def __init__(self, model_dir):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np = np
        ort.disable_telemetry_events()
        receipt = json.loads((model_dir.parent / "onnx-receipt.json").read_text(encoding="utf-8"))
        if receipt.get("passed") is not True or receipt.get("precision") != "float32":
            raise ValueError("ONNX模型尚未通过FP32等价性校验")
        if hashlib.sha256((model_dir / "encoder.onnx").read_bytes()).hexdigest() != receipt["onnx_sha256"]:
            raise ValueError("ONNX模型文件校验失败")
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 8
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.model = ort.InferenceSession(str(model_dir / "encoder.onnx"), sess_options=options,
                                         providers=["CPUExecutionProvider"])
        self.dimension = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))["hidden_size"]

    def encode(self, texts, query=False):
        from search.zh import to_simplified
        strings = [QUERY_PREFIX + to_simplified(t) if query else t for t in texts]
        batch = self.tokenizer.encode_batch(strings)
        if max(len(x.ids) for x in batch) > 512:
            raise ValueError("输入超过模型512 token上限，拒绝静默截断")
        inputs = {key: self.np.asarray([getattr(x, attr) for x in batch], dtype="int64")
                  for key, attr in (("input_ids", "ids"), ("attention_mask", "attention_mask"), ("token_type_ids", "type_ids"))}
        vectors = self.model.run(None, inputs)[0]
        if vectors.shape != (len(strings), self.dimension) or not self.np.isfinite(vectors).all():
            raise ValueError("编码器输出不完整或包含非法数值")
        return vectors.astype("float32", copy=False)


class LocalIndex:
    def __init__(self, state, database):
        import numpy as np
        self.np = np
        self.state = state
        self.database = database
        self.manifest_digest = hashlib.sha256((state / "index/manifest.json").read_bytes()).hexdigest()
        self.manifest = json.loads((state / "index" / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest.get("encoder_version") != ENCODER_VERSION:
            raise ValueError("编码器配置变化，语义索引需重建")
        onnx_receipt = json.loads((state / "onnx-receipt.json").read_text(encoding="utf-8"))
        if self.manifest.get("encoder_artifact_sha256") != onnx_receipt.get("onnx_sha256"):
            raise ValueError("ONNX编码器与索引不一致")
        if self.manifest.get("complete") is not True or self.manifest["database"] != identity(database):
            raise ValueError("语义索引过期或构建未完成，需重建")
        receipt = json.loads((state / "model-receipt.json").read_text(encoding="utf-8"))
        if self.manifest["model_receipt_sha256"] != hashlib.sha256((state / "model-receipt.json").read_bytes()).hexdigest():
            raise ValueError("模型版本与索引不一致")
        for item in receipt["files"]:
            if item["file"] not in ("model.safetensors", "config.json", "tokenizer.json", "vocab.txt"):
                continue
            path = state / "model" / item["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("模型文件校验失败")
        self.vectors = np.load(state / "index/vectors.npy", mmap_mode="r", allow_pickle=False)
        if self.vectors.shape != tuple(self.manifest["vector_shape"]) or self.vectors.dtype != np.dtype("float32"):
            raise ValueError("索引矩阵大小不匹配")
        self.encoder = Encoder(state / "model")

    def query(self, text, book=None, top_k=30):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
            raise ValueError("问题须为1至300字")
        if identity(self.database) != self.manifest["database"]:
            raise ValueError("语料已更新，语义索引需重建")
        if hashlib.sha256((self.state / "index/manifest.json").read_bytes()).hexdigest() != self.manifest_digest:
            raise ValueError("索引版本已变化，请重新加载推理进程")
        started = time.perf_counter()
        vector = self.encoder.encode([text], query=True)[0]
        con = sqlite3.connect((self.state / "index/chunks.sqlite").as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            # Filter BEFORE top-k so a book filter cannot hide that book's best hits.
            ids = None if not book else self.np.array([r[0] - 1 for r in con.execute(
                "SELECT chunk_id FROM chunks WHERE book_id=? ORDER BY chunk_id", (book,))], dtype="int64")
            if ids is not None and len(ids) == 0:
                return {"hits": [], "ms": (time.perf_counter() - started) * 1000}
            if ids is None:
                scores = self.vectors @ vector
            else:
                scores = self.np.empty(len(ids), dtype="float32")
                for lo in range(0, len(ids), 4096):
                    scores[lo:lo+4096] = self.vectors[ids[lo:lo+4096]] @ vector
            k = min(max(1, min(int(top_k), 100)), len(scores))
            if not k:
                return {"hits": [], "ms": 0}
            selected = self.np.argpartition(-scores, k - 1)[:k]
            selected = sorted(selected, key=lambda i: (-float(scores[i]), int(i)))
            hits = []
            for i in selected:
                index = int(i if ids is None else ids[i])
                row = con.execute("SELECT payload FROM chunks WHERE chunk_id=?", (index + 1,)).fetchone()
                if row is None:
                    raise ValueError("索引缺少片段元数据")
                chunk = json.loads(row[0])
                hits.append({"chunk_id": index + 1, "semantic_score": float(scores[i]), "chunk": chunk})
            if hashlib.sha256((self.state / "index/manifest.json").read_bytes()).hexdigest() != self.manifest_digest:
                raise ValueError("查询期间索引版本变化，拒绝返回混合版本结果")
            return {"hits": hits, "ms": round((time.perf_counter() - started) * 1000, 2)}
        finally:
            con.close()
