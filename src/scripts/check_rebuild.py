"""Isolated, bounded full-source rebuild acceptance (not a daily unit test).

Keep the original row/ID/source/hash checks. The 28-book job takes more than
240 seconds; it has an explicit 900-second job budget, not a relaxed verdict.
Do not run model evaluations or other rebuilds concurrently with this job.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    output=args.output.resolve()
    if not output.is_relative_to(root/'src/data') or output==root/'src/data':
        parser.error('--output must be a new dedicated directory under src/data')
    if output.exists():parser.error('output already exists; preserve prior evidence')
    started=time.perf_counter()
    env=dict(os.environ)
    env['PYTHONPATH']=str(root/'src')+os.pathsep+env.get('PYTHONPATH','')
    proc=subprocess.Popen([sys.executable,'-m','scripts.rebuild_corpus','--output',str(output)],cwd=root,env=env)
    try:
        result=proc.wait(timeout=900)
    except subprocess.TimeoutExpired:
        # This owns exactly this child. No server/model or unrelated processes.
        proc.kill();proc.wait()
        print('Rebuild exceeded 900 seconds; acceptance NOT complete.',file=sys.stderr)
        return 124
    print(f'Rebuild acceptance exit={result}, wall_seconds={time.perf_counter()-started:.3f}')
    return result


if __name__=='__main__':raise SystemExit(main())
