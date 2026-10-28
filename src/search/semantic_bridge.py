"""Optional local inference process, with no ML dependency in the desktop shell."""
import atexit
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import subprocess
import sys
import threading

from scripts.pipeline import config


class SemanticUnavailable(Exception):
    pass


class SemanticBusy(Exception):
    pass


def state_dir():
    root = Path(config.PROJECT_ROOT).resolve()
    state = (root / "src/data/semantic").resolve()
    if not state.is_relative_to(root):
        raise SemanticUnavailable("语义实验目录必须位于项目内")
    return state


def status():
    state = state_dir()
    try:
        manifest = json.loads((state / "index/manifest.json").read_text(encoding="utf-8"))
        if manifest.get("validation_status") == "failed_single_vs_batch_consistency":
            return {"ready": False, "reason": "语义索引一致性校验未通过，实验入口已停用"}
        from search.semantic_local import ENCODER_VERSION
        if manifest.get("encoder_version") != ENCODER_VERSION:
            return {"ready": False, "reason": "编码器配置变化，语义索引需重建"}
        stat = config.DB_PATH.stat()
        if manifest.get("database") != {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}:
            return {"ready": False, "reason": "语料已变化，语义索引需重建"}
        if manifest.get("complete") is not True:
            return {"ready": False, "reason": "语义索引尚未完成"}
        if not all((state / p).is_file() for p in ("runtime.json", "model-receipt.json", "onnx-receipt.json", "model/encoder.onnx", "index/vectors.npy", "index/chunks.sqlite")):
            return {"ready": False, "reason": "缺少本地模型、运行环境或索引文件"}
        exported = json.loads((state / "onnx-receipt.json").read_text(encoding="utf-8"))
        if exported.get("passed") is not True or manifest.get("encoder_artifact_sha256") != exported.get("onnx_sha256"):
            return {"ready": False, "reason": "本地编码器与索引版本不一致，需重建"}
        from search.semantic_contextual import available
        from search.semantic_lexical_indexed import VERSION as LEXICAL_VERSION
        modes = ["semantic", "hybrid", "expanded"]
        if available(state):
            modes.append('contextual')
        return {"ready": True, "model": "BAAI/bge-small-zh-v1.5", "manifest": manifest,
                "modes": modes, "lexical_backend": LEXICAL_VERSION,
                "reason": "本地语义索引已就绪；首次查询需加载CPU模型"}
    except (OSError, ValueError, AttributeError):
        return {"ready": False, "reason": "本地语义索引尚未就绪，不会改用关键词结果冒充语义结果"}


class Worker:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.responses = None
        self.root = None
        self.signature = None

    def close(self):
        proc, self.proc = self.proc, None
        if proc:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def _start(self):
        if not status()["ready"]:
            raise SemanticUnavailable(status()["reason"])
        self.close()
        state = state_dir()
        self.root = Path(config.PROJECT_ROOT).resolve()
        self.signature = hashlib.sha256((state / "index/manifest.json").read_bytes()).hexdigest()
        runtime = json.loads((state / "runtime.json").read_text(encoding="utf-8"))
        python = Path(runtime["python"])
        if not python.is_absolute() or not python.is_file() or python.name.lower() not in ("python.exe", "python", "python3"):
            raise SemanticUnavailable("未找到配置的本地Python推理运行环境")
        import os
        env = dict(os.environ, TMP=str(state / "tmp"), TEMP=str(state / "tmp"), PYTHONDONTWRITEBYTECODE="1")
        self.proc = subprocess.Popen([str(python), "-B", "-u", str(self.root / "src/semantic_worker.py")],
                                     cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        responses = self.responses = queue.Queue(maxsize=4)
        proc = self.proc
        logger = logging.getLogger("yinde.semantic.worker")
        if not logger.handlers:
            log = RotatingFileHandler(state / "semantic.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
            logger.addHandler(log)
            logger.setLevel(logging.WARNING)
            logger.propagate = False

        def read_stdout():
            try:
                while line := proc.stdout.readline(2_000_001):
                    if len(line) > 2_000_000:
                        break
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        break
                    responses.put(value, timeout=2)
            except (OSError, ValueError, queue.Full):
                pass
            try:
                responses.put({"error": "本地推理进程已退出或协议异常"}, timeout=1)
            except queue.Full:
                pass

        def read_stderr():
            try:
                for line in proc.stderr:
                    logger.warning(line.rstrip()[:2000])
            except (OSError, ValueError):
                pass

        threading.Thread(target=read_stdout, name="semantic-responses", daemon=True).start()
        threading.Thread(target=read_stderr, name="semantic-log", daemon=True).start()
        try:
            ready = responses.get(timeout=90)
        except queue.Empty:
            self.close()
            raise SemanticUnavailable("本地模型加载超时，请查看语义日志")
        from search.semantic_local import ENCODER_VERSION
        if ready.get("ready") is not True or ready.get("protocol") != 1 or ready.get("encoder_version") != ENCODER_VERSION:
            self.close()
            raise SemanticUnavailable(ready.get("error", "本地模型加载失败"))

    def rerank(self, text, texts):
        if not isinstance(texts, list) or len(texts) > 100 or any(not isinstance(t, str) or len(t) > 1000 for t in texts):
            raise ValueError('重排候选须为不超过100条、每条不超过1000字的文本')
        return self.query(text, _texts=texts)

    def query(self, text, book=None, top_k=50, _texts=None, _punctuated=False):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
            raise ValueError("问题须为1至300字")
        if not self.lock.acquire(blocking=False):
            raise SemanticBusy("已有语义查询正在处理，请稍后再试")
        try:
            if not status()["ready"]:
                self.close()
                raise SemanticUnavailable(status()["reason"])
            changed = self.signature is not None and self.signature != hashlib.sha256((state_dir() / "index/manifest.json").read_bytes()).hexdigest()
            if not self.proc or self.proc.poll() is not None or self.root != Path(config.PROJECT_ROOT).resolve() or changed:
                try:
                    self._start()
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.close()
                    raise SemanticUnavailable("本地推理运行环境配置不可用") from exc
            try:
                request = {"protocol": 1, "query": text, "book": book, "top_k": top_k}
                if _texts is not None:
                    request.update(operation='rerank', texts=_texts)
                elif _punctuated:
                    request.update(operation='punctuated_query')
                self.proc.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
                self.proc.stdin.flush()
                result = self.responses.get(timeout=120 if _texts is not None else 60)
            except (OSError, queue.Empty):
                self.close()
                raise SemanticUnavailable("本地语义查询失败或超时")
            if "error" in result:
                raise SemanticUnavailable(result["error"])
            return result
        finally:
            self.lock.release()


worker = Worker()
atexit.register(worker.close)
