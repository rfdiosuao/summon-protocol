"""One local shell per process; outbound-only connections and durable result upload."""
import asyncio
import json
import logging
import random
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path

import aiohttp
from jsonschema import Draft202012Validator,FormatChecker

from gateway.store import Journal,failed,fingerprint
from gateway.commands import CommandFailure

ROOT=Path(__file__).resolve().parents[1]
LOG=logging.getLogger('summon.gateway')


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')


def stamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


class Gateway:
    def __init__(self,config,token,adapter):
        self.config,self.token,self.adapter=config,token,adapter
        self.base=config['hub_url'].rstrip('/')
        self.shell_id=config['shell_id']
        self.journal=Journal(config['database'])
        binding={'hub':self.base,'shell_id':self.shell_id}
        if self.journal.metadata('binding') not in (None,binding):
            self.journal.close()
            raise ValueError('Journal belongs to another Hub or shell; use its own database')
        self.journal.metadata('binding',binding)
        schema=json.loads((ROOT/'protocol/summon.schema.json').read_text(encoding='utf-8'))
        self.validators={name:Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/'+name},format_checker=FormatChecker()) for name in ('Message','ActionCompleted','ActionFailed')}
        self.session=None
        self.active=False
        self.deadline=0
        self.seq=0
        self.work=None
        self.ws=None
        self.http=None
        self.fault=False
        self.last_rx=0
        self.upload_event=asyncio.Event()
        self.pending_stop=None

    async def send(self,kind,payload):
        message={'v':1,'message_id':'gw_'+uuid.uuid4().hex,'sent_at':utc(),'type':kind,'payload':payload}
        self.validators['Message'].validate(message)
        if self.ws is None or self.ws.closed:
            raise ConnectionError('Hub disconnected')
        await asyncio.wait_for(self.ws.send_json(message),1)

    async def report(self):
        await self.send('shell.report',{'shell_id':self.shell_id,'physical_state':'FAULT' if self.fault else 'READY',
            'enabled':bool(self.config['enabled'] and not self.fault),'detail':'SUMMON Gateway; cloud execution evidence upload enabled'})

    async def preflight(self):
        async with self.http.get(self.base+'/v1/gateway/config') as response:
            if response.status in (401,403):
                raise PermissionError('Gateway credential rejected; obtain authorized shell credentials')
            if response.status!=200:
                raise ConnectionError('Hub config unavailable')
            approved=await response.json()
        shell=approved['shell']
        if approved['mode']!=self.config['mode'] or shell['shell_id']!=self.shell_id:
            raise ValueError('Hub mode or shell identity differs from local config')
        if not set(self.adapter.capabilities).issubset(shell['capabilities']) or not set(shell['allowed_actions']).issubset(self.adapter.capabilities):
            raise ValueError('Approved actions differ from implemented adapter capabilities')
        if shell['stop_kind']!=self.adapter.stop_kind:
            raise ValueError('Stop mechanism differs from local adapter')
        if any(approved['profile'].get(k)!=self.config['profile'].get(k) for k in ('model','firmware','adapter_version')):
            raise ValueError('Device profile differs from deployment-approved profile')
        self.allowed=set(shell['allowed_actions']) if shell['gate']!='readonly' else set()

    async def flush(self):
        for outcome in self.journal.pending():
            async with self.http.post(self.base+'/v1/gateway/results',json=outcome) as response:
                if response.status in (401,403):
                    raise PermissionError('Cloud result upload not authorized')
                if response.status==409:
                    self.fault=True
                    raise ValueError('Cloud result conflicts with journal; inspect rather than discard')
                if response.status!=200:
                    raise ConnectionError('Cloud did not persist result; retained in local outbox')
                receipt=await response.json()
                if receipt.get('command_id')!=outcome['command_id'] or receipt.get('stored') is not True:
                    raise ValueError('Invalid cloud persistence acknowledgement')
                self.journal.acknowledge(outcome['command_id'])
                LOG.info('经验已上传，云端确认保存 | %s | late=%s | 待上传=%s',outcome['command_id'],receipt.get('late'),len(self.journal.pending()))

    async def upload_loop(self):
        delay=1
        while True:
            try:
                await self.flush()
                if self.pending_stop:
                    await self.send('session.stopped',self.pending_stop)
                    self.pending_stop=None
                delay=1
                self.upload_event.clear()
                try:
                    await asyncio.wait_for(self.upload_event.wait(),5)
                except asyncio.TimeoutError:
                    pass
            except (aiohttp.ClientError,ConnectionError,asyncio.TimeoutError):
                LOG.warning('经验上传暂不可用，结果已保留，稍后重试。')
                await asyncio.sleep(random.uniform(delay,min(30,delay*1.5)))
                delay=min(30,delay*2)

    async def execute(self,request):
        LOG.info('执行任务 | %s | %s',request['command_id'],request['action']['capability'])
        outcome=failed(request)
        try:
            base={'session_id':request['session_id'],'command_id':request['command_id']}
            await self.send('action.accepted',dict(base,status='ACCEPTED'))
            await self.send('action.started',dict(base,status='EXECUTING'))
            result=await asyncio.wait_for(self.adapter.execute(request),max(.001,min(10,self.deadline-time.monotonic())))
            outcome=dict(base,status='COMPLETED',**result)
            self.validators['ActionCompleted'].validate(outcome)
        except asyncio.CancelledError:
            outcome=failed(request)
            raise
        except CommandFailure as exc:
            outcome=failed(request,'DRIVER_ERROR')
            outcome['error']['error']['message']='Command timed out or exited with a nonzero status; see execution.'
            outcome['execution']=exc.result
            self.validators['ActionFailed'].validate(outcome)
        except Exception:
            outcome=failed(request)
            self.fault=True
        finally:
            self.journal.finish(outcome)
            LOG.info('任务终态 | %s | %s；等待云端确认',request['command_id'],outcome['status'])
            self.upload_event.set()
        if self.fault:
            await self.stop()
            await self.report()

    async def stop(self):
        self.active=False
        task=self.work
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        for cid,sid in self.journal.db.execute('SELECT id,session FROM commands WHERE outcome IS NULL').fetchall():
            self.journal.finish(failed({'command_id':cid,'session_id':sid}))
        self.upload_event.set()
        session=self.session or self.journal.metadata('session')
        try:
            if not await asyncio.wait_for(self.adapter.stop(session),3):
                raise RuntimeError('No local stop confirmation')
        except Exception:
            self.fault=True
            return False
        return True

    async def message(self,message):
        self.validators['Message'].validate(message)
        kind,p=message['type'],message['payload']
        if kind=='heartbeat':
            await self.send('heartbeat',p)
        elif kind=='session.offer':
            s=p['session']
            if self.fault or not self.config['enabled'] or self.active or (self.work and not self.work.done()):
                raise ValueError('Shell not ready for an offer')
            if s['shell_id']!=self.shell_id or not set(p['permitted_capabilities']).issubset(self.allowed) or not p['permitted_capabilities']:
                raise ValueError('Offer exceeds approved local capabilities')
            if not 0<stamp(s['expires_at'])-time.time()<=61:
                raise ValueError('Invalid or expired lease')
            self.journal.epoch(s['lease_epoch'])
            self.session=s
            self.journal.metadata('session',s)
            self.deadline=time.monotonic()+stamp(s['expires_at'])-time.time()
            self.permitted=set(p['permitted_capabilities'])
            self.seq=0
            await self.send('session.ready',{'session_id':s['session_id'],'lease_epoch':s['lease_epoch'],'role':'gateway'})
        elif kind=='session.activate':
            if not self.session or p['session_id']!=self.session['session_id'] or p['lease_epoch']!=self.session['lease_epoch'] or time.monotonic()>=self.deadline or self.fault:
                raise ValueError('Invalid activation')
            self.active=True
            LOG.info('会话已激活 | %s',p['session_id'])
        elif kind=='session.revoke':
            LOG.info('收到会话释放请求，停止本地执行。')
            known=self.session or self.journal.metadata('session')
            if not known or p['session_id']!=known['session_id'] or p['lease_epoch']!=known['lease_epoch']:
                raise ValueError('Invalid revocation')
            if await self.stop():
                # Stop locally immediately. Only acknowledge after results are durable,
                # in the uploader task so a slow HTTP request cannot block heartbeats.
                self.pending_stop={k:p[k] for k in ('session_id','lease_epoch')}
                self.upload_event.set()
            else:
                await self.report()
        elif kind=='action.request':
            s=self.session
            if not self.active or self.fault or not s or p['session_id']!=s['session_id'] or p['lease_epoch']!=s['lease_epoch']:
                raise ValueError('No active lease for action')
            old=self.journal.get(p['command_id'])
            if old:
                if old['hash']!=fingerprint(p):
                    raise ValueError('IDEMPOTENCY_CONFLICT')
                if old['outcome']:
                    await self.send('action.completed' if old['outcome']['status']=='COMPLETED' else 'action.failed',old['outcome'])
                return
            now=time.time()
            # Past sent_at includes transport delay, not just clock skew. Keep
            # the absolute command expiry and lease bound; reject future clocks.
            if stamp(message['sent_at'])-now>.5 or not now<stamp(p['expires_at'])<=min(stamp(s['expires_at']),stamp(message['sent_at'])+5.1) or time.monotonic()>=self.deadline:
                raise ValueError('Expired action or clock skew')
            if p['seq']!=self.seq+1 or (self.work and not self.work.done()) or p['action']['capability'] not in self.permitted:
                raise ValueError('Command order, busy state or capability rejected')
            self.journal.reserve(p)
            self.seq=p['seq']
            self.work=asyncio.create_task(self.execute(p))

    async def watchdog(self):
        while True:
            await asyncio.sleep(.1)
            if not self.adapter.healthy():
                self.fault=True
                await self.stop()
                try:
                    await self.report()
                finally:
                    await self.ws.close()
                return
            if time.monotonic()-self.last_rx>3 or (self.active and time.monotonic()>=self.deadline):
                await self.stop()
                await self.ws.close()
                return

    async def connection(self,timing):
        LOG.info('正在连接云端，检查设备身份与配置…')
        await self.preflight()
        stopped=await self.stop()
        await self.flush()
        async with self.http.ws_connect(self.base+'/v1/connect',max_msg_size=16384) as ws:
            self.ws=ws
            await self.send('hello',{'role':'gateway','id':self.shell_id})
            welcome=await ws.receive_json(timeout=5)
            self.validators['Message'].validate(welcome)
            if welcome['type']!='welcome':
                raise ValueError('Missing welcome')
            timing['welcome']=time.monotonic()
            self.last_rx=time.monotonic()
            old=self.journal.metadata('session')
            if old and stopped:
                await self.send('session.stopped',{k:old[k] for k in ('session_id','lease_epoch')})
                self.pending_stop=None
            await self.report()
            LOG.info('已连接云端 | %s | 等待任务 | 待上传=%s',self.shell_id,len(self.journal.pending()))
            watcher=asyncio.create_task(self.watchdog())
            uploader=asyncio.create_task(self.upload_loop())
            async def receive():
                async for raw in ws:
                    if raw.type!=aiohttp.WSMsgType.TEXT:
                        break
                    self.last_rx=time.monotonic()
                    await self.message(json.loads(raw.data))
            receiver=asyncio.create_task(receive())
            try:
                done,_=await asyncio.wait([receiver,watcher,uploader],return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in (receiver,watcher,uploader):
                    task.cancel()
                await asyncio.gather(receiver,watcher,uploader,return_exceptions=True)
                await self.stop()
                self.ws=None

    async def run(self):
        headers={'Authorization':'Bearer '+self.token,'User-Agent':'SUMMON-Gateway/0.1','Accept':'application/json'}
        try:
            async with aiohttp.ClientSession(headers=headers,timeout=aiohttp.ClientTimeout(total=10,sock_connect=5),trust_env=True) as self.http:
                await self.adapter.open()
                LOG.info('本地适配器已就绪。')
                delay=1
                while True:
                    timing={}
                    try:
                        await self.connection(timing)
                    except aiohttp.WSServerHandshakeError as exc:
                        if exc.status in (401,403):
                            raise PermissionError('Handshake rejected; reconnect stopped') from exc
                        LOG.warning('连接握手失败，HTTP %s；准备重连。',exc.status)
                    except (aiohttp.ClientError,ConnectionError,asyncio.TimeoutError) as exc:
                        LOG.warning('连接中断或暂不可用 [%s]；准备重连。',type(exc).__name__)
                    if 'welcome' in timing and time.monotonic()-timing['welcome']>=10:
                        delay=1
                    retry=random.uniform(delay,min(30,delay*1.5))
                    LOG.info('%.1f 秒后重新连接；不会重放旧动作。',retry)
                    await asyncio.sleep(retry)
                    delay=min(30,delay*2)
        finally:
            await self.stop()
            await self.adapter.close()

    def close(self):
        self.journal.close()
