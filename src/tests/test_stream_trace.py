import io,json,tempfile,unittest,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from tests.context_reader.stream_trace import capture
class TraceTests(unittest.TestCase):
 def test_complete_and_partial(self):
  for ending,expected in [(b'data: [DONE]\n\n',True),(b'',False)]:
   chunks=[dict(choices=[dict(delta=dict(reasoning_content='thinking'))]),dict(choices=[dict(delta=dict(content='answer'),finish_reason='stop')])]
   response=io.BytesIO(b''.join(('data: '+json.dumps(c)+'\n\n').encode() for c in chunks)+ending)
   response.status=200
   conn=Mock();conn.getresponse.return_value=response
   with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp,patch('tests.context_reader.stream_trace.http.client.HTTPConnection',return_value=conn):
    trace,result=capture(SimpleNamespace(url='http://127.0.0.1:9999',timeout=1,key='secret'),{},Path(temp))
    self.assertEqual(trace['completed'],expected)
    self.assertEqual(result['choices'][0]['message']['content'],'answer')
    self.assertEqual((Path(temp)/'reasoning.txt').read_text(),'thinking')
    self.assertNotIn('secret',(Path(temp)/'events.jsonl').read_text())
    conn.close.assert_called_once()
 def test_deadline(self):
  event=threading.Event();response=Mock(status=200)
  response.readline.side_effect=lambda n:(event.wait(1),b'')[1]
  conn=Mock();conn.sock.shutdown.side_effect=lambda how:event.set()
  conn.getresponse.return_value=response
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp,patch('tests.context_reader.stream_trace.http.client.HTTPConnection',return_value=conn):
   trace,_=capture(SimpleNamespace(url='http://127.0.0.1:9999',timeout=.03,key='x'),{},Path(temp))
  self.assertEqual(trace['error']['type'],'TimeoutError')
  self.assertLess(trace['elapsed_ms'],900)
