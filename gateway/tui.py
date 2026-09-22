"""Windows keyboard TUI with separate editable input and bounded log panel."""
import asyncio
from collections import deque
from datetime import datetime,timezone
import logging
import sys
import webbrowser

from rich.console import Console,Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from gateway.nameplates import NameplateClient


class InputState:
    def __init__(self):self.mode=None;self.text='';self.extended=False
    def feed(self,key):
        if self.extended:self.extended=False;return None
        if key in ('\x00','\xe0'):self.extended=True;return None
        if key=='\x1b':self.mode=None;self.text='';return ('back',None)
        if self.mode:
            if key=='\r':
                mode,text=self.mode,self.text;self.mode=None;self.text='';return (mode,text)
            if key=='\b':self.text=self.text[:-1]
            elif key.isprintable() and len(self.text)<(80 if self.mode=='plate' else 2000):self.text+=key
            return None
        return (key.lower(),None)


class LogBuffer(logging.Handler):
    def __init__(self,lines):super().__init__();self.lines=lines;self.setFormatter(logging.Formatter('%(asctime)s %(message)s','%H:%M:%S'))
    def emit(self,record):self.lines.append(self.format(record))


class Output:
    closed=False
    def __init__(self,lines):self.lines=lines
    def write(self,text):
        self.lines.extend('设备输出: '+line for line in text.splitlines() if line)
        return len(text)
    def flush(self):pass


async def run(gateway):
    import msvcrt
    client=NameplateClient(gateway);editor=InputState();lines=deque(maxlen=100)
    logger=logging.getLogger('summon.gateway')
    removed=[h for h in logger.handlers if type(h) is logging.StreamHandler]
    for handler in removed:logger.removeHandler(handler)
    buffer=LogBuffer(lines);logger.addHandler(buffer)
    original=getattr(gateway.adapter,'output',None)
    if original is not None:gateway.adapter.output=Output(lines)
    console=Console();notice='按 A 授权设备，按 M 输入铭牌。';job=None;poll=None;service=None
    def view():
        session=client.session
        online=gateway.ws is not None and not gateway.ws.closed
        state=session['state'] if session else '未连接 Agent'
        remaining=''
        if session:
            seconds=max(0,int((datetime.fromisoformat(session['expires_at'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()))
            remaining=f' | 租约 {seconds}s | Agent {session["agent_id"]}'
        auth='未授权'
        if client.grant:
            minutes=int((datetime.fromisoformat(client.grant['expires_at'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()/60)
            auth=f'有效，剩余 {minutes} 分钟' if minutes>0 else '已到期，请重新授权'
        head=Text(f'{gateway.shell_id} | {gateway.config["mode"]}\n云端：'+('已连接' if online else '连接中 / 离线')+f' | 设备授权：{auth}\n会话：{state}{remaining}\n经验上传开启（账号内共享） | 待上传 {len(gateway.journal.pending())}')
        content=[notice]
        if client.pairing:
            content+=['配对码：'+client.pairing['user_code'],'按 B 打开设备授权页，在浏览器输入此码。']
        if client.preview:
            p=client.preview;a=p['agent'];caps=set(a['capabilities']) & set(getattr(gateway,'allowed',[]))
            if client.grant:caps &= set(client.grant['capabilities'])
            content+=[f'目标：{a["name"]} · {p["code"]} · {a["status"]}','共同能力：'+(', '.join(sorted(caps)) or '无'),'核对目标后按 C 连接；已有会话先按 R 释放。']
        if editor.mode:content+=['输入铭牌：' if editor.mode=='plate' else '输入任务：',editor.text+'▌','Enter 提交 | Esc 返回；输入中的 Q/B 为普通文字']
        elif client.directory:content+=['铭牌目录：']+[p['code']+' · '+p['agent']['name']+' · '+p['agent']['status'] for p in client.directory[:5]]
        recent=gateway.journal.metadata('recent_plates') or []
        if recent and not editor.mode:content+=['最近使用：'+', '.join(p['code'] for p in recent)]
        available=max(3,min(10,console.size.height-24))
        return Group(Panel(head,title='SUMMON · 唤名',border_style='cyan'),Panel(Text('\n'.join(content)),title='操作'),Panel(Text('\n'.join(list(lines)[-available:]) or '等待事件…'),title='执行与云端日志'),Text('A 授权  M 铭牌  C 连接  T 任务  R 释放  L 目录  B 网页  Q 退出 | Esc 返回',style='cyan'))
    try:
        service=asyncio.create_task(gateway.run())
        with Live(view(),console=console,screen=True,auto_refresh=False) as live:
            next_poll=0
            while not service.done():
                if job and job.done():
                    try:job.result();notice='操作已完成，连接状态以会话栏为准。'
                    except asyncio.CancelledError:notice='已返回。'
                    except Exception as exc:notice=str(exc).replace(gateway.token,'[REDACTED]')
                    job=None
                if poll and poll.done():
                    try:poll.result()
                    except Exception:pass
                    poll=None
                now=asyncio.get_running_loop().time()
                if now>=next_poll and poll is None and gateway.http and not gateway.http.closed:
                    poll=asyncio.create_task(client.status());next_poll=now+2
                if msvcrt.kbhit():
                    event=editor.feed(msvcrt.getwch())
                    if event:
                        key,value=event
                        if key=='q':break
                        if key=='b':webbrowser.open(gateway.base+'/assets/device.html')
                        elif key=='back':
                            if client.pairing and job:
                                job.cancel();client.pairing=None
                                notice='已返回，未完成的配对请求会自动过期。'
                            else:notice='已返回；进行中的连接/释放仍会完成，请查看状态。'
                        elif key=='m':editor.mode='plate';editor.text=''
                        elif key=='t':editor.mode='task';editor.text=''
                        elif key in ('a','c','r','l','plate','task'):
                            if job:notice='上一项操作尚在进行，请稍候。'
                            else:
                                op={'a':client.authorize,'c':client.connect,'r':client.release,'l':client.refresh,
                                    'plate':lambda:client.lookup(value),'task':lambda:client.submit(value)}[key]
                                job=asyncio.create_task(op());notice='正在处理…（心跳和上传继续运行）'
                live.update(view(),refresh=True)
                await asyncio.sleep(.08)
            if service.done():service.result()
    finally:
        for task in (job,poll):
            if task:task.cancel()
        await asyncio.gather(*[t for t in (job,poll) if t],return_exceptions=True)
        if service and not service.done():
            try:await asyncio.wait_for(client.release(),4)
            except Exception:pass
            service.cancel()
        if service:await asyncio.gather(service,return_exceptions=True)
        client.grant=None
        if original is not None:gateway.adapter.output=original
        logger.removeHandler(buffer)
        for handler in removed:logger.addHandler(handler)
        print('设备已停止。未上传结果保留在本地，下一次启动补传。',flush=True)
