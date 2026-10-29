"""Desktop entry point: no console, no fixed port, no native JavaScript bridge."""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
import time

from desktop.runtime import DesktopServer, InstanceLock
from desktop.windows import activate, window_state


def project_root():
    if getattr(sys, "frozen", False):
        marker = Path(sys.executable).parent / "yinde-project.json"
        settings = json.loads(marker.read_text(encoding="utf-8"))
        return (marker.parent / settings["relative_root"]).resolve(strict=True)
    return Path(__file__).resolve().parents[2]


def notify(message, error=False):
    ctypes.windll.user32.MessageBoxW(None, message, "引得", 0x10 if error else 0x40)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true", help="运行真实窗口自动验收后退出")
    parser.add_argument("--require-semantic", action="store_true", help="验收必须包含实际可用的语义索引")
    parser.add_argument("--require-punctuated", action="store_true", help="验收必须包含本机标点本阅读")
    args = parser.parse_args(argv)
    server = None
    lock = None
    owns_lock = False
    owner_file = None
    try:
        root = project_root()
        state = root / "src" / "data" / "desktop"
        state.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(state / "desktop.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
        lock = InstanceLock(state / "instance.lock")
        owner_file = state / "instance.json"
        if not lock.acquire():
            if args.smoke_test:
                return 3
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    owner = json.loads(owner_file.read_text(encoding="utf-8"))
                    # A stale PID must never activate an unrelated executable.
                    if owner.get("executable") == sys.executable and activate(owner.get("pid"), sys.executable):
                        logging.info("Existing desktop window activated")
                        return 0
                except (OSError, ValueError, AttributeError):
                    pass
                time.sleep(0.1)
            notify("检测到引得进程，但未能找到可显示的窗口。请查看 src/data/desktop/desktop.log。", error=True)
            return 1
        owns_lock = True
        owner_file.write_text(json.dumps({"pid": os.getpid(), "executable": sys.executable}), encoding="utf-8")
        import webview
        server = DesktopServer(root).start()
        # WebView storage is kept in the repository, never in a global browser profile.
        profile = state / "webview-profile"
        profile.mkdir(exist_ok=True)
        window = webview.create_window("引得 · 史料检索", server.entry, width=1180, height=820,
                                       min_size=(760, 560), text_select=True, confirm_close=False)
        smoke = {"completed": False, "checks": {}, "error": None}

        def check_window():
            started = time.monotonic()
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if window.evaluate_js("Boolean(document.querySelector('#readingMode') && window.setReadingMode)"):
                        break
                    time.sleep(0.2)
                else:
                    raise TimeoutError("界面未在60秒内就绪")
                finished = threading.Event()
                def receive(result):
                    smoke["checks"] = result
                    finished.set()
                window.evaluate_js("""(async () => {
                  const until = async (predicate, timeout = 30000) => {
                    const deadline = Date.now() + timeout;
                    while (!predicate()) {
                      if (Date.now() > deadline) throw new Error('页面等待超时: ' + document.querySelector('#view').textContent.slice(0, 200));
                      await new Promise(resolve => setTimeout(resolve, 100));
                    }
                  };
                  const health = await fetch('/api/desktop/health').then(r => r.json());
                  const books = await fetch('/api/books').then(r => r.json());
                  const search = await fetch('/api/search?q=' + encodeURIComponent('王安石') + '&variants=1&page_size=3').then(r => r.json());
                  const host = document.createElement('div'); document.body.appendChild(host);
                  const original = '國語，齊桓公𫝊 &KR123;';
                  host.innerHTML = readingTextHtml(original);
                  setReadingMode('simplified');
                  const simplified = host.textContent.includes('国语') && host.textContent.includes('齐桓公');
                  const special = host.textContent.includes('𫝊') && host.textContent.includes('&KR123;');
                  setReadingMode('orig'); const exact = host.textContent === original;
                  const safe = readingTextHtml('<img src=x onerror=alert(1)>').includes('&lt;img');
                  host.remove(); setReadingMode('simplified'); await readingSave;
                  const page = await fetch('/').then(r => r.text());
                  location.hash = '#/books';
                  await until(() => document.querySelector('#view h1')?.textContent.includes('书库'));
                  const bookRoute = document.querySelector('#view').textContent.includes('书库');
                  location.hash = '#/history-qa?source=original';
                  await until(() => document.querySelector('#historyQaForm'));
                  const qaRoute = Boolean(document.querySelector('#historyQaForm'));
                  location.hash = '#/search?source=original&q=' + encodeURIComponent('王安石');
                  await until(() => document.querySelector('#result .blk-text [data-reading-source]'));
                  const searchRoute = Boolean(document.querySelector('#result .read-link'));
                  const passageLink = document.querySelector('#result a[href^="#/p/"]').getAttribute('href');
                  const readLink = document.querySelector('#result .read-link').getAttribute('href');
                  location.hash = passageLink;
                  await until(() => document.querySelector('.readingtext [data-reading-source]') && document.querySelector('#ctxzone .ctx-item'));
                  const contextRoute = true;
                  location.hash = readLink;
                  await until(() => document.querySelector('.read-body [data-reading-source]'));
                  const readRoute = true;
                  const punctuatedStatus = await fetch('/api/punctuated/books').then(r => r.json());
                  let punctuatedCheck = !punctuatedStatus.available;
                  if (punctuatedStatus.available) {
                    const punctuatedBook = punctuatedStatus.books.find(b => b.readable > 0);
                    const chapters = await fetch('/api/punctuated/chapters?book=' + encodeURIComponent(punctuatedBook.book_id)).then(r => r.json());
                    const chapter = chapters.rows.find(r => r.status === 'readable');
                    location.hash = '#/punctuated/' + chapter.page_id;
                    await until(() => document.querySelector('.punctuated-body [data-reading-source]'));
                    const raw = document.querySelector('.punctuated-body [data-reading-source]').dataset.readingSource;
                    setReadingMode('orig');
                    const exactPunctuation = document.querySelector('.punctuated-body [data-reading-source]').textContent === raw;
                    setReadingMode('simplified'); await readingSave;
                    punctuatedCheck = exactPunctuation && document.querySelector('#view').textContent.includes('来源固定版本');
                  }
                  const qaResponse = await fetch('/api/experimental/history-qa', {method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({question: '王安石', generate: false})});
                  const qaResult = await qaResponse.json();
                  const semanticStatus = await fetch('/api/experimental/semantic/status').then(r => r.json());
                  let semanticCheck = !semanticStatus.ready;
                  let expandedCheck = !semanticStatus.ready;
                  let indexedCheck = !semanticStatus.ready;
                  let contextualCheck = !semanticStatus.ready;
                  if (semanticStatus.ready) {
                    location.hash = '#/semantic?source=original';
                    await until(() => document.querySelector('#semanticForm'));
                    document.querySelector('#semanticQuery').value = '王安石出任宰相的记载有哪些？';
                    document.querySelector('#semanticStrategy').value = 'expanded';
                    document.querySelector('#semanticForm').requestSubmit();
                    await until(() => document.querySelector('#semanticResult [data-reading-source]'), 90000);
                    semanticCheck = document.querySelector('#semanticResult').textContent.includes('语义');
                    expandedCheck = document.querySelector('#semanticResult').textContent.includes('字面线索：王安石');
                    indexedCheck = semanticStatus.lexical_backend === 'variant-like-prefilter-v1'
                      && document.querySelector('#semanticResult').textContent.includes('索引加速');
                    if (semanticStatus.modes?.includes('contextual')) {
                      document.querySelector('#semanticStrategy').value = 'contextual';
                      document.querySelector('#semanticForm').requestSubmit();
                      await until(() => document.querySelector('#semanticResult').textContent.includes('检索单位：原文片段'), 90000);
                      contextualCheck = Boolean(document.querySelector('#semanticResult [data-reading-source]'));
                    }
                  }
                  const primaryStatus = await fetch('/api/punctuated/search/status').then(r=>r.json());
                  let primarySearch=false, primaryContext=false, primarySemantic=false, relationExperiment=false, evidencePipeline=false;
                  if(primaryStatus.ready){
                    location.hash='#/search?q='+encodeURIComponent('王安石');
                    await until(()=>document.querySelector('#punctuatedSearchResult .punctuated-search-hit [data-reading-source]'));
                    primarySearch=document.querySelector('#view').textContent.includes('标点版优先')
                      && [...document.querySelectorAll('#punctuatedSearchResult .read-link')].every(a=>a.getAttribute('href').startsWith('#/punctuated/'));
                    const compare=document.querySelector('#punctuatedSearchResult a[href*="source=original"]');
                    const first=document.querySelector('#punctuatedSearchResult .read-link').getAttribute('href');
                    location.hash=first;
                    await until(()=>document.querySelector('.punctuated-location [data-reading-source]'));
                    setReadingMode('orig');await readingSave;
                    const exact=[...document.querySelectorAll('.punctuated-body [data-reading-source]')].every(n=>n.textContent===n.dataset.readingSource);
                    primaryContext=Boolean(compare)&&exact;
                    setReadingMode('simplified');await readingSave;
                    if(semanticStatus.ready){
                      location.hash='#/semantic';
                      await until(()=>document.querySelector('#punctuatedSearchForm'));
                      document.querySelector('#punctuatedSearchQuery').value='王安石出任宰相的记载有哪些？';
                      document.querySelector('#punctuatedSearchStrategy').value='expanded';
                      document.querySelector('#punctuatedSearchForm').requestSubmit();
                      await until(()=>document.querySelector('#punctuatedSearchResult .punctuated-search-hit [data-reading-source]'),90000);
                      primarySemantic=document.querySelector('#punctuatedSearchResult').textContent.includes('语义候选 60')
                        && [...document.querySelectorAll('#punctuatedSearchResult .read-link')].every(a=>a.getAttribute('href').startsWith('#/punctuated/'));
                      document.querySelector('#punctuatedSearchStrategy').value='relation';
                      document.querySelector('#punctuatedSearchForm').requestSubmit();
                      await until(()=>document.querySelector('#punctuatedSearchResult .relation-experiment-note')
                        && document.querySelector('#punctuatedSearchResult .punctuated-search-hit [data-reading-source]'),90000);
                      relationExperiment=location.hash.includes('strategy=relation')
                        && [...document.querySelectorAll('#punctuatedSearchResult .read-link')].every(a=>a.getAttribute('href').startsWith('#/punctuated/'));
                      document.querySelector('#punctuatedSearchStrategy').value='evidence';
                      document.querySelector('#punctuatedSearchForm').requestSubmit();
                      await until(()=>document.querySelector('#punctuatedSearchResult .evidence-pipeline-note')
                        && document.querySelector('#punctuatedSearchResult .evidence-audit'),90000);
                      evidencePipeline=location.hash.includes('strategy=evidence')
                        && document.querySelector('#punctuatedSearchResult').textContent.includes('不是已核实的答案')
                        && document.querySelector('.evidence-pipeline-note').dataset.pipelineVersion==='context-evidence-v2';
                    }
                  }
                  return {health: health.status === 'ready', books_loaded: books.length > 0, books_count: books.length,
                    search_has_hits: search.total > 0, search_total: search.total,
                    simplified, original_restored: exact, special_preserved: special, html_escaped: safe,
                    preferences_persisted: page.includes('window.YindeDesktopMode="simplified"'),
                    book_route: bookRoute, qa_route: qaRoute, search_route: searchRoute,
                    context_route: contextRoute, read_route: readRoute,
                    qa_local_evidence: qaResponse.ok && qaResult.evidence?.length > 0,
                    model_calls_zero: qaResult.metrics?.model_calls === 0,
                    semantic_index_ready: semanticStatus.ready, semantic_ui: semanticCheck, semantic_expanded: expandedCheck,
                    semantic_contextual: contextualCheck, semantic_indexed: indexedCheck,
                    punctuated_ready: punctuatedStatus.available, punctuated_reader: punctuatedCheck,
                    punctuated_primary_search:primarySearch,punctuated_primary_context:primaryContext,punctuated_primary_semantic:primarySemantic,
                    punctuated_relation_experiment:relationExperiment,punctuated_evidence_pipeline:evidencePipeline};
                })().catch(error => ({error: String(error)}))""", callback=receive)
                if not finished.wait(180):
                    raise TimeoutError("窗口验收脚本超时")
                required = {"health", "books_loaded", "search_has_hits", "simplified", "original_restored",
                            "special_preserved", "html_escaped", "preferences_persisted", "book_route", "qa_route",
                            "search_route", "context_route", "read_route", "qa_local_evidence", "model_calls_zero", "semantic_ui"}
                if args.require_semantic:
                    required.add("semantic_index_ready")
                    required.add("semantic_expanded")
                    required.add("semantic_indexed")
                    required.add("semantic_contextual")
                if args.require_punctuated:
                    required.update({"punctuated_ready", "punctuated_reader", "punctuated_primary_search", "punctuated_primary_context"})
                    if args.require_semantic:
                        required.add('punctuated_primary_semantic')
                        required.add('punctuated_relation_experiment')
                        required.add('punctuated_evidence_pipeline')
                if (not isinstance(smoke["checks"], dict) or not required.issubset(smoke["checks"])
                        or not all(smoke["checks"][key] is True for key in required)):
                    raise AssertionError(f"window checks failed: {smoke['checks']}")
                smoke["completed"] = True
            except Exception as exc:
                smoke["error"] = str(exc)
                logging.exception("Desktop window smoke test failed")
            finally:
                smoke["elapsed_seconds"] = round(time.monotonic() - started, 3)
                window.destroy()

        def on_start():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                activate(os.getpid(), sys.executable)
                native = window_state(os.getpid())
                if native and native["visible"] and not native["minimized"]:
                    logging.info("Desktop native window visible")
                    (state / "window-visibility.json").write_text(json.dumps(native), encoding="utf-8")
                    if args.smoke_test:
                        check_window()
                    return
                time.sleep(0.1)
            logging.error("Desktop native window did not become visible")
            if args.smoke_test:
                smoke["error"] = "native window hidden or absent"
                window.destroy()
            else:
                notify("引得窗口未能正常显示，请查看 src/data/desktop/desktop.log。", error=True)
                window.destroy()

        logging.info("Desktop window starting")
        webview.start(on_start, gui="edgechromium", debug=False,
                      private_mode=False, storage_path=str(profile))
        if args.smoke_test:
            server.close()
            smoke["service_stopped"] = not server.thread.is_alive()
            semantic = sys.modules.get("search.semantic_bridge")
            child = semantic.worker.proc if semantic else None
            if child:
                smoke["semantic_worker_pid"] = child.pid
                semantic.worker.close()
                smoke["semantic_worker_stopped"] = child.poll() is not None
            elif args.require_semantic:
                smoke["semantic_worker_stopped"] = False
                smoke["completed"] = False
            (state / "window-smoke.json").write_text(json.dumps(smoke, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0 if smoke["completed"] else 1
        return 0
    except Exception:
        logging.exception("Desktop startup failed")
        if not args.smoke_test:
            notify("引得启动失败。请查看项目 src/data/desktop/desktop.log。\n"
                   "请确认史料数据库存在，且本机已安装 Microsoft Edge WebView2 Runtime。", error=True)
        return 1
    finally:
        try:
            if server:
                server.close()
        finally:
            semantic = sys.modules.get("search.semantic_bridge")
            if semantic:
                semantic.worker.close()
            if owns_lock and owner_file:
                owner_file.unlink(missing_ok=True)
            if lock:
                lock.close()
            logging.info("Desktop stopped")


if __name__ == "__main__":
    raise SystemExit(main())
