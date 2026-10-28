"""Optional server-side model transport. Config is local, ignored, and opt-in.

Only the official Anthropic host is supported in v1. No arbitrary URL, proxy,
file upload, retries or model tool execution. Never return upstream error bodies.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib import request, error

from search.history_qa import ProviderError, MAX_PROMPT_CHARS

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "ai.local.json"


def read_config():
    # Disallow redirecting the credential file through an external symlink.
    root = Path(__file__).resolve().parents[2]
    if not CONFIG_PATH.resolve().is_relative_to(root):
        raise ProviderError("模型配置必须位于项目内")
    if not CONFIG_PATH.exists():
        return {}
    try:
        if CONFIG_PATH.stat().st_size > 8192:
            raise ValueError()
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError()
        return cfg
    except (OSError, ValueError):
        raise ProviderError("模型配置无法读取") from None


def available():
    cfg = read_config()
    return bool(cfg.get("enabled") is True and cfg.get("allow_external_evidence") is True
                and cfg.get("provider") == "anthropic" and cfg.get("model")
                and cfg.get("api_key"))


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("模型接口重定向已拒绝")


class AnthropicProvider:
    def __init__(self):
        self.cfg = read_config()
        if not (self.cfg.get("enabled") is True
                and self.cfg.get("allow_external_evidence") is True
                and self.cfg.get("provider") == "anthropic"
                and isinstance(self.cfg.get("model"), str) and self.cfg["model"].strip()
                and isinstance(self.cfg.get("api_key"), str) and self.cfg["api_key"].strip()):
            raise ProviderError("尚未配置模型；可先使用本地证据预览")
        self.usage = []
        self.calls = 0

    def complete(self, system, payload):
        if self.calls >= 2:
            raise ProviderError("已达到每问两次模型调用上限")
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > MAX_PROMPT_CHARS:
            raise ProviderError("模型输入超过上限")
        self.calls += 1
        body = json.dumps({"model": self.cfg["model"], "max_tokens": 2400,
                           "system": system,
                           "messages": [{"role": "user", "content": text}]}).encode()
        req = request.Request("https://api.anthropic.com/v1/messages", body, {
            "Content-Type": "application/json", "x-api-key": self.cfg["api_key"],
            "anthropic-version": "2023-06-01"}, method="POST")
        try:
            opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
            with opener.open(req, timeout=30) as response:
                raw = response.read(262145)
            if len(raw) > 262144:
                raise ProviderError("模型响应超过上限")
            data = json.loads(raw)
            if data.get("stop_reason") != "end_turn":
                raise ProviderError("模型输出未正常结束")
            usage = data.get("usage", {})
            self.usage.append({k: usage.get(k) for k in ("input_tokens", "output_tokens")})
            content = "".join(b.get("text", "") for b in data.get("content", [])
                              if b.get("type") == "text").strip()
            return json.loads(content)
        except ProviderError:
            raise
        except (error.URLError, OSError, ValueError, TypeError, AttributeError):
            raise ProviderError("模型调用失败或输出格式不符合约定") from None
