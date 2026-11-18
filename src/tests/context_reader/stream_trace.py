"""Loopback-only, bounded streaming observation; never changes model instructions."""
import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit


def capture(reader, request, folder):
    url = urlsplit(reader.url)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username:
        raise ValueError('Only owned loopback reader is allowed')
    started = time.monotonic()
    deadline = started + reader.timeout
    expired = threading.Event()
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=reader.timeout)
    trace = dict(done=False, finish_reason=None, events=0, first_reasoning_ms=None,
                 first_final_ms=None, reasoning_chars=0, final_chars=0)
    message = dict(content='', reasoning_content='')
    usage = {}
    timer = None
    response = None
    wire = dict(request, stream=True, stream_options={'include_usage': True})
    try:
        conn.connect()
        owned_socket = conn.sock

        def interrupt():
            expired.set()
            try:
                owned_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(max(0, deadline-time.monotonic()), interrupt)
        timer.daemon = True
        timer.start()
        conn.request('POST', '/v1/chat/completions', json.dumps(wire).encode(),
                     {'Content-Type': 'application/json', 'Authorization': 'Bearer '+reader.key})
        response = conn.getresponse()
        trace['headers_ms'] = round((time.monotonic()-started)*1000, 2)
        if response.status != 200:
            raise ValueError('HTTP '+str(response.status))
        with (folder/'events.jsonl').open('w', encoding='utf8') as log, \
                (folder/'reasoning.txt').open('w', encoding='utf8') as reasoning, \
                (folder/'final.txt').open('w', encoding='utf8') as final:
            data = []
            size = 0
            while True:
                line = response.readline(1048577)
                elapsed = round((time.monotonic()-started)*1000, 2)
                if expired.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError('Overall streaming deadline exceeded')
                if not line:
                    raise ValueError('EOF without DONE')
                size += len(line)
                if len(line) > 1048576 or size > 16777216:
                    raise ValueError('Stream size limit exceeded')
                line = line.decode('utf8').rstrip('\r\n')
                if line.startswith('data:'):
                    data.append(line[5:].lstrip(' '))
                elif not line and data:
                    raw = '\n'.join(data)
                    data = []
                    log.write(json.dumps(dict(ms=elapsed, data=raw), ensure_ascii=False)+'\n')
                    log.flush()
                    trace['events'] += 1
                    trace['last_event_ms'] = elapsed
                    if raw == '[DONE]':
                        trace['done'] = True
                        break
                    chunk = json.loads(raw)
                    if chunk.get('error'):
                        raise ValueError('Server stream error')
                    usage = chunk.get('usage') or usage
                    for choice in chunk.get('choices', []):
                        if choice.get('index', 0) != 0:
                            raise ValueError('Unexpected choice index')
                        if choice.get('finish_reason'):
                            trace['finish_reason'] = choice['finish_reason']
                            trace['finish_ms'] = elapsed
                        delta = choice.get('delta') or {}
                        for field, phase, output in [('reasoning_content','reasoning',reasoning),
                                                     ('content','final',final)]:
                            value = delta.get(field) or ''
                            if not isinstance(value, str):
                                raise ValueError('Invalid stream text')
                            if value:
                                message[field] += value
                                output.write(value)
                                output.flush()
                                if trace['first_'+phase+'_ms'] is None:
                                    trace['first_'+phase+'_ms'] = elapsed
                                trace['last_'+phase+'_ms'] = elapsed
                                trace[phase+'_chars'] += len(value)
                    (folder/'progress.json').write_text(json.dumps(trace), encoding='utf8')
    except (OSError, ValueError, http.client.HTTPException) as exc:
        trace['error'] = dict(type='TimeoutError' if expired.is_set() else type(exc).__name__,
                              message=str(exc))
    finally:
        if timer:
            timer.cancel()
            timer.join()
        if response:
            response.close()
        conn.close()
    trace['elapsed_ms'] = round((time.monotonic()-started)*1000, 2)
    trace['completed'] = bool(trace['done'] and not trace.get('error') and
                              trace['finish_reason'] == 'stop' and message['content'].strip())
    (folder/'progress.json').write_text(json.dumps(trace, indent=2), encoding='utf8')
    return trace, dict(choices=[dict(message=message, finish_reason=trace['finish_reason'])], usage=usage)
