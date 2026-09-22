"""Passport USB voice -> STT -> cloud Agent -> authorized desktop -> Passport audio."""
import argparse
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import time
import uuid
import wave

import aiohttp
import serial
from gateway.adapters import DesktopAdapter
from gateway.runtime import Gateway
from gateway.nameplates import NameplateClient
from gateway.passport_media import Recording,decode_line


class VoiceAdapter(DesktopAdapter):
    def __init__(self,config):
        super().__init__(config)
        self.replies=asyncio.Queue()

    async def execute(self,request):
        result=await super().execute(request)
        if request['action']['capability']=='display.text':
            self.replies.put_nowait((request['session_id'],request['command_id'],request['action']['args']['text']))
        return result


class Passport:
    def __init__(self,gateway,port,private,default_plate,test_wav=None):
        self.gateway=gateway;self.client=NameplateClient(gateway)
        self.port=port;self.private=private;self.default_plate=default_plate
        self.recording=Recording();self.task=None;self.turn=0;self.acks={}
        self.serial=None;self.tx=asyncio.Lock();self.stopping=False
        self.test_wav=test_wav

    async def send(self,kind,**values):
        data=('SUMMON1 '+json.dumps(dict(type=kind,turn=self.turn,**values),ensure_ascii=False)+'\n').encode()
        if len(data)>2048:raise ValueError('USB frame too large')
        async with self.tx:await asyncio.to_thread(self.serial.write,data)

    async def say_status(self,text):await self.send('status',text=text[:250])

    async def frames(self):
        # Windows readline performs many one-byte reads. Batch USB reads to keep
        # audio flowing; preserve partial JSON frames across USB packet boundaries.
        buffer=bytearray()
        while not self.stopping:
            data=await asyncio.to_thread(lambda:self.serial.read(min(4096,self.serial.in_waiting or 1)))
            buffer.extend(data)
            while b'\n' in buffer:
                line,_,tail=buffer.partition(b'\n');buffer=bytearray(tail)
                frame=decode_line(line)
                if frame and 'type' in frame:yield frame
            if len(buffer)>2048:buffer.clear()

    async def authorize(self):
        base=self.gateway.base
        code=os.environ.get('SUMMON_OPERATOR_CODE')
        if not code:raise ValueError('SUMMON_OPERATOR_CODE is required for initial device authorization')
        async with aiohttp.ClientSession(headers={'User-Agent':'SUMMON-Passport/0.1','Origin':base},timeout=aiohttp.ClientTimeout(total=15)) as h:
            async def call(path,body):
                async with h.post(base+path,json=body) as r:
                    if r.status>=400:raise RuntimeError('Device authorization HTTP '+str(r.status))
                    return await r.json()
            await call('/v1/operator-session',{'access_code':code})
            pair=await self.client.call('POST','/v1/gateway/pairings',{})
            preview=await call('/v1/device-pairings/preview',{'request_id':uuid.uuid4().hex,'user_code':pair['user_code']})
            if preview['shell_id']!=self.gateway.shell_id:raise RuntimeError('Device identity mismatch')
            await call('/v1/device-pairings/approve',{'request_id':uuid.uuid4().hex,'user_code':pair['user_code'],
                'shell_id':self.gateway.shell_id,'capabilities':['display.text','browser.open','command.exec']})
            self.client.grant=await self.client.call('POST','/v1/gateway/pairings/'+pair['pairing_id']+'/claim',{})
        print('电脑设备授权完成；执行命令与结果上传云端。',flush=True)

    async def transcribe(self,pcm):
        wav=io.BytesIO()
        with wave.open(wav,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(pcm)
        form=aiohttp.FormData();form.add_field('model',self.private['stt_model'])
        form.add_field('file',wav.getvalue(),filename='passport.wav',content_type='audio/wav')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as h:
            async with h.post(self.private['base_url'].rstrip('/')+'/audio/transcriptions',data=form,
                headers={'Authorization':'Bearer '+self.private['api_key']}) as r:
                if r.status!=200:raise RuntimeError('STT HTTP '+str(r.status))
                text=(await r.json()).get('text','').strip()
                if not text or len(text)>2000:raise ValueError('没有识别到有效语音，请再说一次')
                return text

    async def speak(self,text):
        with tempfile.TemporaryDirectory(prefix='summon-speech-') as folder:
            source=Path(folder)/'reply.txt';dest=Path(folder)/'reply.wav'
            source.write_text(text[:250],encoding='utf-8')
            process=await asyncio.create_subprocess_exec('powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                str(Path(__file__).with_name('passport_speech.ps1')),'-Mode','tts','-InputPath',str(source),'-OutputPath',str(dest),
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,creationflags=0x08000000)
            try:await asyncio.wait_for(process.wait(),15)
            finally:
                if process.returncode is None:process.kill();await process.wait()
            if process.returncode!=0:raise RuntimeError('Speech synthesis failed')
            with wave.open(str(dest),'rb') as w:
                if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,16000):raise ValueError('Unsupported TTS format')
                duration_ms=round(w.getnframes()/16)
                seq=0
                while True:
                    pcm=w.readframes(512)
                    if not pcm:break
                    future=asyncio.get_running_loop().create_future();self.acks[seq]=future
                    try:
                        await self.send('play.chunk',seq=seq,pcm=base64.b64encode(pcm).decode())
                        await asyncio.wait_for(future,2)
                    finally:self.acks.pop(seq,None)
                    seq+=1
            future=asyncio.get_running_loop().create_future();self.acks['done']=future
            try:
                await self.send('play.end');done=await asyncio.wait_for(future,3)
            finally:self.acks.pop('done',None)
            return {'chunks':seq,'expected_ms':duration_ms,'device':done}

    async def handle(self,pcm,plate):
        stage='语音识别'
        report={'source':'injected WAV' if self.test_wav else 'Passport microphone','started_at':time.time(),'success':False}
        try:
            await self.say_status('正在识别你的话…')
            text=await self.transcribe(pcm)
            report['stt_completed_at']=time.time()
            print('Passport 识别：'+text,flush=True)
            await self.say_status('你说：'+text[:60]+'\n正在联系云端 Agent…')
            stage='连接 Agent'
            await self.client.status()
            if self.client.session and self.client.session['state'] not in ('RELEASED','FAILED'):await self.client.release()
            await self.client.lookup(plate or self.default_plate)
            await self.client.connect()
            for _ in range(40):
                await self.client.status()
                if self.client.session and self.client.session['state']=='ACTIVE':break
                await asyncio.sleep(.25)
            else:raise RuntimeError('Agent 未激活，请检查铭牌是否在线')
            sid=self.client.session['session_id']
            report['session_id']=sid
            report['session_active_at']=time.time()
            stage='云端执行'
            await self.client.submit(text)
            report['input_submitted_at']=time.time()
            async def reply():
                while True:
                    reply_sid,command_id,value=await self.gateway.adapter.replies.get()
                    if reply_sid==sid:return command_id,value
            command_id,result=await asyncio.wait_for(reply(),42)
            report['reply_received_at']=time.time()
            # Do not announce completion before the Gateway has persisted the result.
            for _ in range(100):
                row=self.gateway.journal.get(command_id)
                if row and row['outcome'] and row['uploaded']:break
                await asyncio.sleep(.1)
            else:raise RuntimeError('Cloud receipt not acknowledged')
            if row['outcome']['status']!='COMPLETED':raise RuntimeError('Reply not completed')
            report.update(reply_command_id=command_id,cloud_reply_acknowledged=True,reply=result)
            print('Agent 回复：'+result,flush=True)
            await self.say_status(result[:140])
            stage='设备语音播放'
            report['playback']=await self.speak(result)
            report['playback_finished_at']=time.time()
            report['success']=True
            print('语音回路完成：云端回执已确认，Passport 音频驱动已确认播放。',flush=True)
        except asyncio.CancelledError:raise
        except Exception as exc:
            print('Passport '+stage+'失败：'+type(exc).__name__,flush=True)
            await self.say_status(stage+'未完成\n请看电脑日志，确定重试')
        finally:
            report['last_stage']=stage
            dest=Path(self.gateway.config['database']).parent/'passport'/'last-turn.json'
            dest.parent.mkdir(exist_ok=True)
            dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            try:await self.client.release()
            except Exception:pass

    async def run(self):
        self.serial=serial.Serial()
        self.serial.port=self.port;self.serial.baudrate=115200
        self.serial.timeout=.1;self.serial.write_timeout=2;self.serial.dtr=False;self.serial.rts=False;self.serial.open()
        try:
            await self.send('hello')
            await self.say_status('语音网关已连接\n确定说话 · 长按上键配网')
            await self.authorize()
            print('Passport 就绪；按设备确定键说话。默认铭牌 '+self.default_plate,flush=True)
            if self.test_wav:
                async def test():
                    await asyncio.sleep(1)
                    with wave.open(str(self.test_wav),'rb') as w:
                        if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,16000):raise ValueError('Test needs 16 kHz mono PCM WAV')
                        pcm=w.readframes(80000)
                    await self.handle(pcm,self.default_plate)
                    self.stopping=True
                self.task=asyncio.create_task(test())
            async for frame in self.frames():
                if frame['type']=='hello':
                    self.turn=frame.get('turn',0)
                    print('Passport 握手，audio_ready='+str(frame.get('audio_ready')),flush=True)
                elif frame['type'] in ('play.ack','play.error','play.done') and frame.get('turn')==self.turn:
                    future=self.acks.get('done' if frame['type']=='play.done' else frame.get('seq'))
                    if future and not future.done():
                        if frame['type'] in ('play.ack','play.done'):future.set_result(frame)
                        else:future.set_exception(RuntimeError('Passport playback failed'))
                elif frame['type']=='cancel':
                    self.recording.feed(frame)
                    if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
                else:
                    if frame['type']=='record.start':
                        if self.task and not self.task.done():self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
                        self.turn=frame['turn']
                    try:pcm=self.recording.feed(frame)
                    except ValueError:
                        await self.say_status('USB 音频不完整，请重试');continue
                    if pcm is not None:self.task=asyncio.create_task(self.handle(pcm,self.recording.nameplate))
        finally:
            if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
            self.serial.close()


async def main(args):
    cfg=json.loads(args.config.read_text(encoding='utf-8-sig'))
    if cfg.get('experience_upload') is not True:raise ValueError('Experience upload must be enabled')
    cfg['database']=str((args.config.parent/cfg['database']).resolve())
    gateway=Gateway(cfg,os.environ[cfg['token_env']],VoiceAdapter(cfg['adapter']))
    from gateway.console import setup
    setup(cfg,args.config.parent/'logs/passport.log')
    task=asyncio.create_task(gateway.run())
    try:
        for _ in range(100):
            if task.done():task.result()
            if gateway.ws is not None and not gateway.ws.closed:break
            await asyncio.sleep(.2)
        else:raise RuntimeError('Cloud connection unavailable')
        passport=Passport(gateway,args.port,json.loads(args.private.read_text(encoding='utf-8-sig')),args.nameplate,args.test_wav)
        await passport.run()
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True);gateway.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--private',type=Path,required=True)
    parser.add_argument('--port',default='COM6');parser.add_argument('--nameplate',required=True)
    parser.add_argument('--test-wav',type=Path,help='Explicitly inject WAV instead of microphone; may execute its spoken instruction')
    args=parser.parse_args()
    try:asyncio.run(main(args))
    except KeyboardInterrupt:pass
