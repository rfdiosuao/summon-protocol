"""Send a scoped remote Passport message and wait for its playback receipt."""
import argparse
import json
from pathlib import Path
import time
import urllib.request
import uuid


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--credentials',type=Path,required=True,help='Private JSON with base_url and sender_token')
    p.add_argument('--text',required=True)
    p.add_argument('--mode',choices=['announce','agent'],default='announce')
    p.add_argument('--request-id',default=None,help='Reuse this ID after uncertain delivery; never replay with a new ID')
    args=p.parse_args();cfg=json.loads(args.credentials.read_text(encoding='utf-8-sig'))
    def call(path,body=None):
        r=urllib.request.Request(cfg['base_url'].rstrip('/')+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+cfg['sender_token'],'Content-Type':'application/json',
                     'User-Agent':'SUMMON-Passport-Sender/0.1'})
        with urllib.request.urlopen(r,timeout=15) as response:return json.load(response)
    rid=args.request_id or uuid.uuid4().hex
    print('request_id='+rid,flush=True)
    result=call('/v1/passport/messages',{'request_id':rid,'text':args.text,'mode':args.mode})
    deadline=time.monotonic()+120
    while result['status'] in ('ACCEPTED','RUNNING') and time.monotonic()<deadline:
        time.sleep(.5);result=call('/v1/passport/messages/'+rid)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['status']!='COMPLETED':raise SystemExit(1)


if __name__=='__main__':main()
