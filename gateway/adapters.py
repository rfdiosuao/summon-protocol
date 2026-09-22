"""Local adapters. Serial uses SUMMON BridgeMessage JSONL, not arbitrary vendor bytes."""
import asyncio
import json
import sys
import time
import uuid


class TerminalAdapter:
    capabilities=['display.text']
    stop_kind='local_disable'

    def __init__(self,config=None,output=None):
        self.output=output or sys.stdout

    async def open(self):
        if self.output.closed:
            raise RuntimeError('Terminal output is closed')

    def healthy(self):
        return not self.output.closed

    async def execute(self,request):
        text=request['action']['args']['text']
        text=''.join(c for c in text if c in '\n\t' or c.isprintable())
        self.output.write(text+'\n')
        self.output.flush()
        return {'evidence':'device_ack','result':'Text written and flushed to the local terminal.'}

    async def stop(self,session):
        # No background actuator or deferred output exists in this adapter.
        return True

    async def close(self):
        pass


class SerialDisplayAdapter:
    capabilities=['display.text']
    stop_kind='local_disable'

    def __init__(self,config,validator):
        self.config,self.validator=config,validator
        self.serial=None
        self.pending={}
        self.tasks=[]
        self.last_seen=0
        self.hello=asyncio.Event()
        self.write_lock=asyncio.Lock()

    async def send(self,kind,payload):
        from datetime import datetime,timezone
        message={'v':1,'message_id':'bridge_'+uuid.uuid4().hex,'sent_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'type':kind,'payload':payload}
        self.validator.validate(message)
        raw=(json.dumps(message,ensure_ascii=False)+'\n').encode()
        if len(raw)>16384:
            raise ValueError('Bridge frame too large')
        async with self.write_lock:
            await asyncio.get_running_loop().run_in_executor(None,self.serial.write,raw)

    async def open(self):
        import serial
        # Port is explicitly configured: enumeration never selects a target.
        self.serial=serial.Serial(self.config['port'],self.config.get('baudrate',115200),timeout=.2,write_timeout=1)
        self.tasks=[asyncio.create_task(self.read()),asyncio.create_task(self.heartbeat())]
        try:
            await asyncio.wait_for(self.hello.wait(),5)
        except BaseException:
            await self.close()
            raise

    async def read(self):
        try:
            while True:
                raw=await asyncio.get_running_loop().run_in_executor(None,self.serial.read_until,b'\n',16385)
                if not raw:
                    continue
                if len(raw)>16384 or not raw.endswith(b'\n'):
                    raise ValueError('Incomplete or oversized serial frame')
                message=json.loads(raw.decode('utf-8'))
                self.validator.validate(message)
                kind,p=message['type'],message['payload']
                if p.get('device_id')!=self.config['device_id']:
                    raise ValueError('Unexpected device identity')
                if kind=='device.hello':
                    if p['shell_id']!=self.config['shell_id']:
                        raise ValueError('Unexpected shell identity')
                    self.hello.set()
                self.last_seen=time.monotonic()
                key=p.get('command_id') if kind=='device.result' else ('stop',p.get('session_id'),p.get('lease_epoch'))
                future=self.pending.get(key)
                if future and not future.done():
                    future.set_result(p)
        except asyncio.CancelledError:
            raise
        except Exception:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Serial link failed'))
            self.last_seen=0

    async def heartbeat(self):
        while True:
            await self.send('device.heartbeat',{'device_id':self.config['device_id']})
            await asyncio.sleep(1)

    def healthy(self):
        return self.hello.is_set() and time.monotonic()-self.last_seen<=3 and not any(t.done() for t in self.tasks)

    async def exchange(self,key,kind,payload):
        if not self.hello.is_set() or time.monotonic()-self.last_seen>3:
            raise RuntimeError('Device heartbeat unavailable')
        future=asyncio.get_running_loop().create_future()
        self.pending[key]=future
        try:
            await self.send(kind,payload)
            return await asyncio.wait_for(future,3)
        finally:
            self.pending.pop(key,None)

    async def execute(self,request):
        p={k:request[k] for k in ('command_id','session_id','lease_epoch','expires_at','action')}
        result=await self.exchange(request['command_id'],'device.command',p)
        if result['status']!='COMPLETED':
            raise RuntimeError('Device did not confirm completion')
        return {'evidence':'device_ack','result':'Serial device confirmed display completion.'}

    async def stop(self,session):
        if session is None:
            return True
        p={k:session[k] for k in ('session_id','lease_epoch')}
        await self.exchange(('stop',p['session_id'],p['lease_epoch']),'device.stop',p)
        return True

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        if self.serial:
            self.serial.close()
