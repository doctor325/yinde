"""Verify packaged backend bytecode against the current repository sources."""
import argparse,hashlib,json,sys,types
from pathlib import Path

def normalized(code):
 return code.replace(co_filename='<source>',co_consts=tuple(normalized(c) if isinstance(c,types.CodeType) else c for c in code.co_consts))

def main():
 parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);parser.add_argument('--receipt',type=Path,required=True);a=parser.parse_args()
 root=Path(__file__).resolve().parents[2]
 exe=a.executable.resolve();receipt=a.receipt.resolve()
 if not exe.is_relative_to(root) or not receipt.is_relative_to(root):raise ValueError('Path outside repository')
 sys.path.insert(0,str(root/'src/data/desktop/deps'))
 from PyInstaller.archive.readers import CArchiveReader
 archive=CArchiveReader(str(exe)).open_embedded_archive('PYZ.pyz')
 names=['search.evidence_pipeline','search.punctuated_roles','search.punctuated_relevance','search.person_references','search.patient_references','search.biography_office','search.event_links','search.relative_time','search.block_page_cache','search.result_block','desktop.main']
 rows=[]
 for name in names:
  source=root/'src'/Path(*name.split('.')).with_suffix('.py')
  code=archive.extract(name)
  match=normalized(code)==normalized(compile(source.read_bytes(),str(source),'exec'))
  rows.append(dict(module=name,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),embedded_code_matches=match))
 with exe.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
 result=dict(executable_sha256=digest,modules=rows)
 receipt.write_text(json.dumps(result,indent=2),encoding='utf8')
 if not all(r['embedded_code_matches'] for r in rows):raise RuntimeError('Packaged backend differs from source')
 print('Verified',len(rows),'embedded modules')
if __name__=='__main__':main()
