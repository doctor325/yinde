"""Owned, loopback-only llama.cpp process for an explicitly invoked experiment."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request

from search.evidence_reader import SYSTEM, SCHEMA, validate

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOME = ROOT / 'src/data/evidence-reader'


class LocalReader:
    def __init__(self, home=DEFAULT_HOME, timeout=120):
        self.home = Path(home).resolve()
        if not self.home.is_relative_to(ROOT):
            raise ValueError('Reader assets must remain inside this repository')
        self.timeout = timeout
        self.process = self.log = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.key = secrets.token_hex(24)

    def request(self, path, payload=None, timeout=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf8')
        request = urllib.request.Request(self.url+path, data=body,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.key})
        with self.opener.open(request, timeout=timeout or self.timeout) as response:
            return json.load(response)

    def __enter__(self):
        from scripts.setup_evidence_reader import ASSETS, digest
        self.receipt = json.loads((self.home/'receipt.json').read_text(encoding='utf8'))
        if self.receipt['assets'] != list(ASSETS):
            raise ValueError('Unrecognized dependency receipt')
        for asset in ASSETS:
            path=self.home/asset['name']
            if path.stat().st_size!=asset['bytes'] or digest(path)!=asset['sha256']:
                raise ValueError('Reader dependency failed integrity check')
        executable = self.home/'runtime/llama-server.exe'
        model = self.home/'Qwen3-4B-Q4_K_M.gguf'
        if not executable.is_file():raise FileNotFoundError(executable)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        self.url='http://127.0.0.1:'+str(port)
        temp=self.home/'tmp';temp.mkdir(exist_ok=True)
        env={k:v for k,v in os.environ.items() if not k.startswith('LLAMA_')}
        env.update(TMP=str(temp),TEMP=str(temp),HF_HUB_OFFLINE='1')
        self.log=(self.home/'reader-runtime.log').open('w',encoding='utf8')
        started=time.perf_counter()
        try:
            self.process=subprocess.Popen([str(executable),'-m',str(model),'--offline',
                '--host','127.0.0.1','--port',str(port),'--api-key',self.key,
                '-c','8192','-np','1','-t','4','-ngl','0','--reasoning','off',
                '--no-context-shift','--cache-ram','0'],cwd=self.home,env=env,
                stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while time.perf_counter()-started<60:
                if self.process.poll() is not None:raise RuntimeError('Local reader exited during startup')
                try:
                    if self.request('/health',timeout=1).get('status')=='ok':break
                except (OSError,urllib.error.URLError):pass
                time.sleep(.25)
            else:raise TimeoutError('Local reader startup exceeded 60 seconds')
            self.startup_ms=round((time.perf_counter()-started)*1000,2)
            return self
        except BaseException:
            self.close();raise

    def read(self, question, context):
        prompt=dict(question=question, source_context=context)
        started=time.perf_counter()
        response=self.request('/v1/chat/completions',dict(model='local',
            messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(prompt,ensure_ascii=False))],
            temperature=0,seed=20261111,max_tokens=1100,
            chat_template_kwargs={'enable_thinking':False},
            response_format={'type':'json_schema','json_schema':{'name':'event_evidence','strict':True,'schema':SCHEMA}}))
        choice=response['choices'][0]
        if choice['finish_reason']!='stop':
            raise ValueError('Reader output incomplete: '+str(choice['finish_reason']))
        raw=choice['message']['content']
        value=json.loads(raw)
        return dict(output=value,validation=validate(value,context),
                    elapsed_ms=round((time.perf_counter()-started)*1000,2),
                    usage=response.get('usage',{}))

    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill();self.process.wait(timeout=5)
            self.process=None
        if self.log:
            self.log.close();self.log=None

    def __exit__(self,*args):
        self.close()
