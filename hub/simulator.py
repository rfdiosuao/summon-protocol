"""Explicitly simulated Agent and two display Gateways; no model or hardware."""
import asyncio
import contextlib
import json
import os
import time
from pathlib import Path

import aiohttp

from hub.app import uid, utc, stamp


class DemoFleet:
    def __init__(self, base, invite, gateways, credentials=None):
        self.base,self.invite,self.gateways=base,invite,gateways
        self.credentials=Path(credentials) if credentials else None
        self.tasks=[]
        self.clients=[]

    async def start(self):
        self.http=aiohttp.ClientSession()
        async with self.http.get(self.base+'/healthz') as r:
            if (await r.json())['mode']!='SIMULATED':
                raise RuntimeError('DemoFleet must never attach to a LIVE Hub')
        if self.credentials and self.credentials.exists():
            registration=json.loads(self.credentials.read_text())
        else:
            async with self.http.post(self.base+'/v1/agents',headers={'Authorization':'Bearer '+self.invite},json={
                'request_id':'demo_agent_registration_v1','name':'SUMMON 联调 Agent','bio':'规则式模拟响应，用于验证连接、记忆与交接','capabilities':['display.text']}) as r:
                registration=await r.json()
                if r.status!=201:
                    raise RuntimeError('Demo registration failed')
            if self.credentials:
                self.credentials.write_text(json.dumps(registration))
                self.credentials.chmod(0o600)
        self.tasks.append(asyncio.create_task(self.run('agent',registration['agent']['agent_id'],registration['agent_token'])))
        for sid,token in self.gateways.items():
            self.tasks.append(asyncio.create_task(self.run('gateway',sid,token)))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        await self.http.close()

    async def run(self,role,identifier,token):
        async with self.http.ws_connect(self.base+'/v1/connect',headers={'Authorization':'Bearer '+token}) as ws:
            sessions, seq, pending, results = {}, {}, {}, {}
            async def send(kind,payload):
                await ws.send_json({'v':1,'message_id':uid('msg'),'sent_at':utc(),'type':kind,'payload':payload})
            await send('hello',{'role':role,'id':identifier})
            async for frame in ws:
                if frame.type!=aiohttp.WSMsgType.TEXT:
                    break
                m=json.loads(frame.data)
                kind,p=m['type'],m['payload']
                if kind=='welcome' and role=='gateway':
                    await send('shell.report',{'shell_id':identifier,'physical_state':'READY','enabled':True,'detail':'SIMULATED display; no physical device'})
                elif kind=='heartbeat':
                    await send('heartbeat',p)
                elif kind=='session.offer':
                    s=p['session']
                    sessions[s['session_id']]={'session':s,'active':False,'memory':p.get('memory')}
                    seq[s['session_id']]=0
                    await send('session.ready',{'session_id':s['session_id'],'lease_epoch':s['lease_epoch'],'role':role})
                elif kind=='session.activate':
                    sessions[p['session_id']]['active']=True
                elif kind=='session.revoke':
                    if p['session_id'] in sessions:
                        sessions[p['session_id']]['active']=False
                    if role=='gateway':
                        await send('session.stopped',{'session_id':p['session_id'],'lease_epoch':p['lease_epoch']})
                elif kind=='input.text' and role=='agent':
                    state=sessions[p['session_id']]
                    async with self.http.get(self.base+'/v1/sessions/'+p['session_id']+'/memory',headers={'Authorization':'Bearer '+token}) as response:
                        memory=await response.json()
                    brief=memory.get('preferences',{}).get('response_style')=='brief'
                    async with self.http.get(self.base+'/v1/sessions/'+p['session_id']+'/experiences?capability=display.text',headers={'Authorization':'Bearer '+token}) as response:
                        history=await response.json()
                        if response.status!=200:
                            raise RuntimeError('Experience retrieval failed')
                    text=('【模拟·简短】已记住简洁偏好：' if brief else '【模拟·详细】这是规则式联调响应，未调用大模型。收到的问题：')+p['text']
                    text+='\n【经验检索】同设备、同版本、同能力历史 '+str(history['total'])+' 条。'
                    seq[p['session_id']]+=1
                    command=uid('command')
                    pending[command]=p
                    await send('action.request',{'session_id':p['session_id'],'input_id':p['input_id'],
                        'lease_epoch':state['session']['lease_epoch'],'command_id':command,'seq':seq[p['session_id']],
                        'expires_at':utc(min(time.time()+4,stamp(state['session']['expires_at']))),
                        'action':{'capability':'display.text','args':{'text':text[:500]}}})
                elif kind=='action.request' and role=='gateway':
                    state=sessions.get(p['session_id'])
                    base={'session_id':p['session_id'],'command_id':p['command_id']}
                    if p['command_id'] in results:
                        await send('action.completed',results[p['command_id']])
                    elif state and state['active'] and state['session']['lease_epoch']==p['lease_epoch'] and stamp(p['expires_at'])>time.time():
                        await send('action.accepted',dict(base,status='ACCEPTED'))
                        await send('action.started',dict(base,status='EXECUTING'))
                        result=dict(base,status='COMPLETED',evidence='device_ack',result=p['action']['args']['text'])
                        results[p['command_id']]=result
                        await send('action.completed',result)
                elif kind in ('action.completed','action.failed') and role=='agent':
                    original=pending.pop(p['command_id'],None)
                    if original:
                        await send('input.finished',{'session_id':p['session_id'],'input_id':original['input_id'],'status':'COMPLETED' if kind=='action.completed' else 'FAILED'})


async def main():
    cfg=json.loads(Path(os.environ['SUMMON_CONFIG']).read_text())
    fleet=DemoFleet('http://127.0.0.1:'+str(cfg.get('port',8840)),cfg['invite'],cfg['gateway_tokens'],cfg.get('demo_credentials'))
    await fleet.start()
    try:
        await asyncio.gather(*fleet.tasks)
    finally:
        await fleet.close()


if __name__=='__main__':
    asyncio.run(main())
