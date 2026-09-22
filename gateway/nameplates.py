"""Desktop client API. Device grants live in memory, never in configuration."""
import asyncio
import time
import uuid
from urllib.parse import quote

import aiohttp


class NameplateClient:
    def __init__(self,gateway):
        self.gateway=gateway
        self.grant=None
        self.pairing=None
        self.preview=None
        self.session=None
        self.directory=[]

    async def call(self,method,path,body=None):
        if self.gateway.http is None or self.gateway.http.closed:
            raise ValueError('正在连接云端，请稍后再试。')
        if body is not None:body=dict(body,request_id=body.get('request_id','ui_'+uuid.uuid4().hex))
        headers={'X-Summon-Device-Grant':self.grant['device_grant']} if self.grant else {}
        for attempt in range(3):
            try:
                async with self.gateway.http.request(method,self.gateway.base+path,json=body,headers=headers) as response:
                    data=await response.json()
                    if response.status>=400:
                        if response.status>=500 and attempt<2:
                            await asyncio.sleep(.5*(attempt+1));continue
                        raise ValueError(data.get('error',{}).get('message','请求未成功'))
                    return data
            except (aiohttp.ClientError,asyncio.TimeoutError):
                if attempt==2:raise ValueError('网络请求失败；请刷新状态后重试，不要重复执行未知任务。') from None
                await asyncio.sleep(.5*(attempt+1))

    async def authorize(self):
        self.pairing=await self.call('POST','/v1/gateway/pairings',{})
        deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            pid=self.pairing['pairing_id']
            state=await self.call('GET','/v1/gateway/pairings/'+pid)
            if state['status']=='APPROVED':
                self.grant=await self.call('POST','/v1/gateway/pairings/'+pid+'/claim',{})
                self.pairing=None
                return
            await asyncio.sleep(max(2,state['interval']))
        raise ValueError('配对已过期，请重新按 A。')

    async def lookup(self,code):
        self.preview=None
        self.preview=await self.call('GET','/v1/nameplates/'+quote(code.strip(),safe=''))

    async def refresh(self):
        self.directory=(await self.call('GET','/v1/nameplates'))['items']

    async def status(self):
        self.session=(await self.call('GET','/v1/gateway/session'))['session']

    async def connect(self):
        if not self.grant:raise ValueError('先按 A 完成设备授权。')
        if not self.preview:raise ValueError('先按 M 输入铭牌并核对身份。')
        if self.session and self.session['state'] not in ('RELEASED','FAILED'):
            raise ValueError('先按 R 释放当前设备，再连接新的 Agent。')
        plate=self.preview
        result=await self.call('POST','/v1/gateway/sessions',{'code':plate['code'],'agent_id':plate['agent']['agent_id']})
        self.session=result['session']
        recent=self.gateway.journal.metadata('recent_plates') or []
        recent=[{'code':plate['code'],'name':plate['agent']['name']}]+[p for p in recent if p['code']!=plate['code']]
        self.gateway.journal.metadata('recent_plates',recent[:5])

    async def release(self):
        await self.gateway.stop()
        await self.status()
        if self.session:
            await self.call('POST','/v1/gateway/sessions/'+self.session['session_id']+'/release',{})
            for _ in range(12):
                await self.status()
                if not self.session or self.session['state']=='RELEASED':return
                if self.session['state']=='FAILED':raise ValueError('停止未确认，请检查设备；禁止直接切换。')
                await asyncio.sleep(.5)
            raise ValueError('释放尚未确认，请等待状态更新。')

    async def submit(self,text):
        if not self.session or self.session['state']!='ACTIVE':raise ValueError('尚无激活会话，先连接 Agent。')
        await self.call('POST','/v1/gateway/sessions/'+self.session['session_id']+'/inputs',{'text':text})
