"""Read-only cold HTTPS probe. No credentials, no device actions."""
import argparse
import json
import math
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',default='https://summon.entermodetwo.com')
    parser.add_argument('--samples',type=int,default=20)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if not 1<=args.samples<=100:
        parser.error('samples must be 1..100')
    curl=shutil.which('curl.exe') or shutil.which('curl')
    rows=[]
    for _ in range(args.samples):
        result=subprocess.run([curl,'--silent','--show-error','--output','NUL' if __import__('os').name=='nt' else '/dev/null',
            '--connect-timeout','5','--max-time','10','--write-out','%{http_code} %{time_connect} %{time_appconnect} %{time_total}',
            args.base.rstrip('/')+'/healthz'],capture_output=True,timeout=12)
        parts=result.stdout.decode('utf-8','replace').split()
        row={'exit_code':result.returncode,'error':result.stderr.decode('utf-8','replace').strip()}
        if len(parts)==4:
            row.update(status=int(parts[0]),tcp_complete_ms=round(float(parts[1])*1000),
                       tls_complete_ms=round(float(parts[2])*1000),total_ms=round(float(parts[3])*1000))
        rows.append(row)
    good=[r for r in rows if r['exit_code']==0 and r.get('status')==200]
    stats={}
    for field in ('tls_complete_ms','total_ms'):
        values=sorted(r[field] for r in good)
        stats[field]={k:values[min(len(values)-1,math.ceil(len(values)*p)-1)] for k,p in [('p50',.5),('p95',.95),('max',1)]} if values else None
    report={'at':datetime.now(timezone.utc).isoformat(),'base':args.base,'kind':'cold HTTPS, new curl process per sample; not WSS or physical action latency',
            'samples':len(rows),'successful':len(good),'failed':len(rows)-len(good),'statistics':stats,'rows':rows}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))


if __name__=='__main__':
    main()
