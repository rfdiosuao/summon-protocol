"""Explicit rule-based test Agent, hosted remotely; only requests one configured URL."""
import asyncio
import json
import os
import secrets
from pathlib import Path
import time

import aiohttp
from hub.app import uid,utc,stamp


async def run(config_path,credentials_path):
    cfg=json.loads(Path(config_path).read_text())
    if cfg['mode']!='SIMULATED':raise RuntimeError('Test agent requires SIMULATED mode')
    base='http://127.0.0.1:8840'
    url='https://summon.entermodetwo.com/#summon-browser-cloud-test'
    path=Path(credentials_path)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),headers={'User-Agent':'SUMMON-Browser-Test-Agent/0.1'}) as http:
        for attempt in range(30):
            try:
                async with http.get(base+'/healthz') as response:
                    if response.status==200:break
            except (aiohttp.ClientError,asyncio.TimeoutError):pass
            await asyncio.sleep(.5)
        else:raise RuntimeError('Hub did not become ready')
        if path.exists():registration=json.loads(path.read_text())
        else:
            async with http.post(base+'/v1/agents',json={
                'request_id':'reg_'+secrets.token_hex(16),'name':'海鸥 · 浏览器联调 Agent',
                'bio':'固定规则测试：打开官网，或执行 powershell: 后的命令，非大模型',
                'capabilities':['browser.open','command.exec']}) as r:
                if r.status!=201:raise RuntimeError('Browser test agent registration failed: '+str(r.status))
                registration=await r.json()
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(registration,f)
        headers={'Authorization':'Bearer '+registration['agent_token']}
        while True:
            sessions={};pending={};seq={}
            try:
                async with http.ws_connect(base+'/v1/connect',headers=headers) as ws:
                    async def send(kind,p):
                        await ws.send_json({'v':1,'message_id':uid('testmsg'),'sent_at':utc(),'type':kind,'payload':p})
                    await send('hello',{'role':'agent','id':registration['agent']['agent_id']})
                    async for frame in ws:
                        if frame.type!=aiohttp.WSMsgType.TEXT:break
                        m=json.loads(frame.data);kind,p=m['type'],m['payload']
                        if kind=='heartbeat':await send('heartbeat',p)
                        elif kind=='session.offer':
                            s=p['session'];sessions[s['session_id']]=dict(s,active=False);seq[s['session_id']]=0
                            await send('session.ready',{'session_id':s['session_id'],'lease_epoch':s['lease_epoch'],'role':'agent'})
                        elif kind=='session.activate':sessions[p['session_id']]['active']=True
                        elif kind=='session.revoke':
                            if p['session_id'] in sessions:sessions[p['session_id']]['active']=False
                        elif kind=='input.text':
                            s=sessions.get(p['session_id'])
                            if not s or not s['active']:continue
                            seq[p['session_id']]+=1;cid=uid('browser')
                            pending[cid]=p
                            action={'capability':'browser.open','args':{'url':url}}
                            if p['text'].startswith('powershell:'):
                                action={'capability':'command.exec','args':{'command':p['text'][len('powershell:'):].strip()}}
                            await send('action.request',{'session_id':s['session_id'],'input_id':p['input_id'],
                                'lease_epoch':s['lease_epoch'],'command_id':cid,'seq':seq[s['session_id']],
                                'expires_at':utc(min(time.time()+4,stamp(s['expires_at']))),
                                'action':action})
                            print('Browser test action requested:',cid,flush=True)
                        elif kind in ('action.completed','action.failed'):
                            original=pending.pop(p['command_id'],None)
                            print('Browser test result:',p['command_id'],p['status'],flush=True)
                            if original and sessions.get(p['session_id'],{}).get('active'):
                                await send('input.finished',{'session_id':p['session_id'],'input_id':original['input_id'],'status':'COMPLETED' if kind=='action.completed' else 'FAILED'})
            except aiohttp.WSServerHandshakeError as exc:
                if exc.status in (401,403):raise
            except (aiohttp.ClientError,asyncio.TimeoutError):pass
            await asyncio.sleep(3)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--credentials',required=True)
    args=parser.parse_args()
    asyncio.run(run(args.config,args.credentials))
