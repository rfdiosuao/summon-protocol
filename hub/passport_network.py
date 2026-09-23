"""Authenticated Passport media bridge. Runs beside Hub, never executes host commands."""
import argparse
import asyncio
import base64
import hashlib
import io
import json
import logging
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
import wave

import aiohttp
from aiohttp import web
from gateway.passport_media import Recording

log = logging.getLogger(__name__)


class Speech:
    def __init__(self, config):
        self.config = config

    async def transcribe(self, pcm):
        log.info('STT start pcm_bytes=%d duration_ms=%d', len(pcm), len(pcm) // 32)
        data = io.BytesIO()
        with wave.open(data, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
        form = aiohttp.FormData()
        form.add_field('model', self.config['stt_model'])
        form.add_field('file', data.getvalue(), filename='speech.wav', content_type='audio/wav')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
            async with http.post(self.config['base_url']+'/audio/transcriptions', data=form,
                                 headers={'Authorization': 'Bearer '+self.config['api_key']}) as r:
                if r.status != 200:
                    log.warning('STT failed http_status=%d', r.status)
                    raise RuntimeError('Speech recognition unavailable')
                text = (await r.json()).get('text', '').strip()
                log.info('STT complete text_chars=%d', len(text))
                if not text: raise ValueError('No speech detected')
                if len(text) > 2000: raise ValueError('Oversized transcription')
                return text

    async def synthesize(self, text):
        model = self.config.get('tts_model', 'FunAudioLLM/CosyVoice2-0.5B')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as http:
            async with http.post(self.config['base_url']+'/audio/speech',
                                 headers={'Authorization': 'Bearer '+self.config['api_key']},
                                 json={'model': model, 'voice': model+':anna', 'input': text[:250],
                                       'response_format': 'pcm', 'sample_rate': 16000}) as r:
                if r.status != 200: raise RuntimeError('Speech synthesis unavailable')
                data = bytearray()
                async for chunk in r.content.iter_chunked(8192):
                    data.extend(chunk)
                    if len(data) > 16000*2*60: raise ValueError('Speech exceeds 60 seconds')
                if not data or len(data) % 2: raise ValueError('Invalid PCM')
                log.info('TTS complete pcm_bytes=%d text_chars=%d', len(data), len(text))
                return bytes(data)


class HubChannel:
    """Use existing operator session APIs; bind every device to one authorized target."""
    def __init__(self, config): self.config = config

    async def ask(self, device, plate, text):
        log.info('Hub ask start plate=%s shell=%s text_chars=%d', plate, device['target_shell_id'], len(text))
        base = self.config['hub_url'].rstrip('/')
        jar = aiohttp.CookieJar(unsafe=True, treat_as_secure_origin=[base])
        async with aiohttp.ClientSession(cookie_jar=jar, timeout=aiohttp.ClientTimeout(total=10),
                headers={'Origin': self.config['origin'], 'User-Agent': 'SUMMON-Passport-Network/0.1'}) as http:
            async def call(method, path, body=None):
                if body is not None: body = dict(body, request_id=uuid.uuid4().hex)
                async with http.request(method, base+path, json=body) as r:
                    if r.status >= 400: raise RuntimeError('Hub request failed: '+str(r.status))
                    return await r.json()
            # Login has a strict schema without request_id.
            async with http.post(base+'/v1/operator-session', json={'access_code': device['operator_code']}) as r:
                if r.status != 200: raise RuntimeError('Hub authorization failed')
            name = await call('GET', '/v1/nameplates/'+plate)
            session = (await call('POST', '/v1/sessions',
                {'agent_id': name['agent']['agent_id'], 'shell_id': device['target_shell_id']}))['session']
            sid = session['session_id']
            log.info('Hub session created sid=%s', sid)
            try:
                for _ in range(24):
                    state = await call('GET', '/v1/state')
                    active = next((s for s in state['sessions'] if s['session_id'] == sid), None)
                    if active and active['state'] == 'ACTIVE': break
                    if active and active['state'] in ('FAILED', 'RELEASED'): raise RuntimeError('Session failed')
                    await asyncio.sleep(.25)
                else:
                    log.warning('Hub session activation timed out sid=%s', sid)
                    raise RuntimeError('Agent or target computer offline')
                log.info('Hub session active sid=%s', sid)
                inp = await call('POST', '/v1/sessions/'+sid+'/inputs', {'text': text})
                log.info('Hub input accepted sid=%s input=%s', sid, inp['input_id'])
                for _ in range(90):
                    state = await call('GET', '/v1/state')
                    for command in state['commands']:
                        req = command['request']
                        if req['session_id'] != sid or req['input_id'] != inp['input_id']: continue
                        if req['action']['capability'] != 'display.text': continue
                        outcome = command.get('outcome')
                        if outcome and outcome['status'] == 'COMPLETED':
                            log.info('Hub command completed sid=%s command=%s', sid, command['request']['command_id'])
                            return req['action']['args']['text'], sid
                        if outcome and outcome['status'] in ('FAILED','UNKNOWN'):
                            log.warning('Hub command failed sid=%s status=%s', sid, outcome['status'])
                            raise RuntimeError('Reply did not complete')
                    await asyncio.sleep(.5)
                log.warning('Hub command timed out sid=%s input=%s', sid, inp['input_id'])
                raise RuntimeError('Agent response timed out')
            finally:
                # Releasing a session stops its target; never release somebody else's session.
                try: await call('POST', '/v1/sessions/'+sid+'/release', {})
                except Exception: pass


class Peer:
    def __init__(self, service, device_id, config, ws):
        self.service=service; self.device_id=device_id; self.config=config; self.ws=ws
        self.turn=0; self.recording=Recording(); self.job=None; self.acks={}; self.ready=False

    async def send(self, kind, **payload):
        await self.ws.send_json(dict(type=kind, turn=self.turn, **payload))

    async def speak(self, text):
        await self.send('status', text=text[:140])
        pcm=await self.service.speech.synthesize(text)
        log.info('Playback start turn=%d pcm_bytes=%d', self.turn, len(pcm))
        # A 1024-byte PCM frame is 32 ms at 16 kHz / 16 bit / mono. Pace the
        # server-to-device stream at that rate and only wait for the final
        # receipt. Avoiding a write ACK for every frame keeps the ESP TLS
        # connection half-duplex while audio is being delivered.
        done=asyncio.get_running_loop().create_future(); self.acks['done']=done
        started=asyncio.get_running_loop().time(); seq=0
        try:
            for at in range(0, len(pcm), 1024):
                if done.done(): await done
                await self.send('play.chunk', seq=seq, pcm=base64.b64encode(pcm[at:at+1024]).decode())
                seq+=1
                deadline=started+seq*.032
                await asyncio.sleep(max(0,deadline-asyncio.get_running_loop().time()))
            if done.done(): await done
            await self.send('play.end')
            receipt=await asyncio.wait_for(done,max(8,len(pcm)/32000+5))
            log.info('Playback complete turn=%d', self.turn)
            return receipt
        finally:
            self.acks.clear()

    async def work(self, request_id, text=None, pcm=None, plate=None, announce=False):
        result={'request_id':request_id, 'device_id':self.device_id, 'status':'RUNNING', 'started_at':time.time()}
        self.service.save(result)
        try:
            log.info('Request start id=%s mode=%s pcm_bytes=%s plate=%s', request_id,
                     'announce' if announce else 'agent', len(pcm) if pcm is not None else 0, plate or self.config['default_plate'])
            if pcm is None:
                ready=asyncio.get_running_loop().create_future(); self.acks['ready']=ready
                await self.send('remote.begin')
                await asyncio.wait_for(ready,5); self.acks.pop('ready',None)
            if pcm is not None:
                await self.send('status',text='云端正在识别…')
                text=await self.service.speech.transcribe(pcm)
                log.info('Request STT ok id=%s text_chars=%d', request_id, len(text))
            if not announce:
                await self.send('status',text='已上传，Agent 正在处理…')
                text,sid=await self.service.channel.ask(self.config,plate or self.config['default_plate'],text)
                result['session_id']=sid
                log.info('Request Agent ok id=%s sid=%s reply_chars=%d', request_id, sid, len(text))
            result['playback']=await self.speak(text)
            result['status']='COMPLETED'
            await self.send('status',text='播报完成\n确定继续说话')
        except asyncio.CancelledError:
            result['status']='CANCELLED'; raise
        except Exception as exc:
            result['status']='FAILED'; result['error']=type(exc).__name__
            result['error_detail']=str(exc)[:120]
            log.warning('Request failed id=%s error=%s detail=%s', request_id, type(exc).__name__, str(exc)[:120])
            if not self.ws.closed:
                if isinstance(exc, ValueError) and str(exc) == 'No speech detected':
                    message='没有识别到语音\n请靠近麦克风再说一次'
                elif isinstance(exc, RuntimeError) and str(exc) in ('Agent or target computer offline', 'Agent response timed out'):
                    message='目标电脑或 Agent 离线\n请确认 EvoX 客户端已连接'
                else:
                    message='请求未完成\n检查网络和目标电脑，确定重试'
                await self.send('status',text=message)
        finally:
            result['finished_at']=time.time(); self.service.save(result)

    async def consume(self, f):
        kind=f.get('type')
        if kind=='hello':
            heap=f.get('free_heap')
            log.info('Passport hello free_heap=%s', heap if isinstance(heap,int) else 'unknown')
            self.turn=int(f.get('turn',0)); self.ready=True
            await self.send('status',text='云端已连接\n确定说话 · 长按上键配网')
        elif kind in ('remote.ready','play.ack','play.done','play.error') and f.get('turn')==self.turn:
            if kind in ('play.ack','play.error','play.done'):
                log.info('Device media event kind=%s turn=%s seq=%s',kind,self.turn,f.get('seq'))
            if kind=='play.error' and f.get('seq') is None:
                # Playback-task failures have no chunk sequence. Fail all
                # outstanding waits immediately rather than masking them as
                # a later per-chunk ACK timeout.
                targets=list(self.acks.values())
                error=RuntimeError('Device playback task failed')
                for pending in targets:
                    if not pending.done(): pending.set_exception(error)
            else:
                seq=f.get('seq')
                key='ready' if kind=='remote.ready' or (kind=='play.error' and seq==-1) else 'done' if kind in ('play.done','play.error') else seq
                pending=self.acks.get(key)
                if pending and not pending.done():
                    if kind=='play.error':
                        detail='Device rejected remote playback request' if seq==-1 else f'Device rejected playback frame seq={seq}'
                        pending.set_exception(RuntimeError(detail))
                    else:pending.set_result(f)
        elif kind in ('cancel','record.start'):
            log.info('Passport recording event=%s',kind)
            if self.job and not self.job.done():
                self.job.cancel(); await asyncio.gather(self.job,return_exceptions=True)
            self.turn=int(f['turn']); self.recording.feed(f)
        elif kind in ('record.chunk','record.end','record.cancel'):
            pcm=self.recording.feed(f)
            if pcm is not None:
                log.info('Passport recording complete bytes=%d',len(pcm))
                plate=self.recording.nameplate or self.config['default_plate']
                if plate not in self.config['allowed_plates']: raise ValueError('Nameplate not authorized')
                self.job=asyncio.create_task(self.work(uuid.uuid4().hex,pcm=pcm,plate=plate))


class Service:
    def __init__(self,config,speech=None,channel=None):
        self.config=config; self.peers={}; self.speech=speech or Speech(config['speech'])
        self.channel=channel or HubChannel(config)
        self.speech_slots=asyncio.Semaphore(2)
        self.db=sqlite3.connect(config['database'])
        self.db.execute('CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, device TEXT, body TEXT)')
        for rid,body in self.db.execute('SELECT id,body FROM messages').fetchall():
            result=json.loads(body)
            if result['status'] in ('ACCEPTED','RUNNING'):
                result['status']='UNKNOWN';result['error']='ServiceRestarted'
                self.save(result)

    def save(self,result):
        old=self.db.execute('SELECT body FROM messages WHERE id=?',(result['request_id'],)).fetchone()
        if old:
            previous=json.loads(old[0])
            if 'digest' in previous:result['digest']=previous['digest']
        self.db.execute('INSERT OR REPLACE INTO messages VALUES(?,?,?)',
                        (result['request_id'],result['device_id'],json.dumps(result)))
        self.db.commit()

    def device(self,request,role):
        value=request.headers.get('Authorization','').removeprefix('Bearer ')
        for key,cfg in self.config['devices'].items():
            if value and secrets.compare_digest(value,cfg[role+'_token']):return key,cfg
        raise web.HTTPUnauthorized()

    async def socket(self,request):
        key,cfg=self.device(request,'device')
        if key in self.peers:raise web.HTTPConflict(text='Device already connected')
        # The ESP WebSocket client keeps the TCP connection and reconnects on
        # transport loss, but does not answer aiohttp's periodic PING frames
        # reliably through the reverse proxy. Do not turn a healthy idle link
        # into a 60-second heartbeat timeout.
        ws=web.WebSocketResponse(max_msg_size=2048)
        await ws.prepare(request)
        peer=Peer(self,key,cfg,ws);self.peers[key]=peer
        try:
            await peer.send('hello')
            async for message in ws:
                if message.type==aiohttp.WSMsgType.TEXT:
                    try:
                        f=json.loads(message.data)
                        if not isinstance(f,dict):raise ValueError('Expected object')
                        await peer.consume(f)
                    except (ValueError,KeyError,TypeError):
                        log.warning('Passport invalid media frame; closing with 1008')
                        await ws.close(code=1008,message=b'Invalid media frame');break
        finally:
            log.info('Passport disconnected code=%s exception=%s chunks=%d bytes=%d',
                     ws.close_code,type(ws.exception()).__name__,peer.recording.seq,len(peer.recording.pcm))
            if peer.job:peer.job.cancel();await asyncio.gather(peer.job,return_exceptions=True)
            self.peers.pop(key,None)
        return ws

    async def messages(self,request):
        key,cfg=self.device(request,'sender')
        if request.method=='GET':
            row=self.db.execute('SELECT body FROM messages WHERE id=? AND device=?',(request.match_info['rid'],key)).fetchone()
            if not row:raise web.HTTPNotFound()
            return web.json_response(json.loads(row[0]))
        body=await request.json()
        if not isinstance(body,dict):raise web.HTTPBadRequest()
        rid=body.get('request_id');text=body.get('text');mode=body.get('mode','announce')
        if not isinstance(rid,str) or not 1<=len(rid)<=80 or not isinstance(text,str) or not 1<=len(text)<=250 or mode not in ('announce','agent'):
            raise web.HTTPBadRequest(text='Invalid message')
        # Idempotency is persistent; completed/unknown jobs are never silently replayed.
        digest=hashlib.sha256(json.dumps([text,mode],ensure_ascii=False).encode()).hexdigest()
        old=self.db.execute('SELECT body FROM messages WHERE id=?',(rid,)).fetchone()
        if old:
            previous=json.loads(old[0])
            if previous['device_id']!=key or previous.get('digest')!=digest:raise web.HTTPConflict()
            return web.json_response(previous)
        peer=self.peers.get(key)
        if not peer or not peer.ready:raise web.HTTPServiceUnavailable(text='Passport offline')
        if (peer.job and not peer.job.done()) or peer.recording.turn is not None:raise web.HTTPConflict(text='Passport busy')
        # Firmware grants a new idle turn before remote audio is accepted.
        peer.turn+=1
        result={'request_id':rid,'device_id':key,'status':'ACCEPTED','digest':digest}
        self.save(result)
        peer.job=asyncio.create_task(peer.work(rid,text=text,announce=mode=='announce'))
        return web.json_response(result,status=202)

    async def status(self,request):
        key,cfg=self.device(request,'sender');peer=self.peers.get(key)
        return web.json_response({'device_id':key,'online':bool(peer and peer.ready),
            'busy':bool(peer and ((peer.job and not peer.job.done()) or peer.recording.turn is not None)),
            'target_shell_id':cfg['target_shell_id'],'default_plate':cfg['default_plate']})

    async def transcribe(self,request):
        self.device(request,'sender')
        if self.speech_slots.locked():raise web.HTTPTooManyRequests()
        data=await request.read()
        try:
            with wave.open(io.BytesIO(data),'rb') as wav:
                if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,16000):raise ValueError()
                if not 1600<=wav.getnframes()<=128000:raise ValueError()
                pcm=wav.readframes(wav.getnframes())
                if len(pcm)!=wav.getnframes()*2:raise ValueError()
        except (ValueError,wave.Error,EOFError):raise web.HTTPBadRequest(text='Expected 0.1–8 s WAV, mono PCM16 16000 Hz')
        async with self.speech_slots:
            try:text=await self.speech.transcribe(pcm)
            except Exception:raise web.HTTPBadGateway(text='Speech recognition unavailable') from None
        return web.json_response({'text':text,'executed':False})

    def app(self):
        app=web.Application(client_max_size=260096)
        app.router.add_get('/v1/passport/connect',self.socket)
        app.router.add_post('/v1/passport/messages',self.messages)
        app.router.add_get('/v1/passport/messages/{rid}',self.messages)
        app.router.add_get('/v1/passport/status',self.status)
        app.router.add_post('/v1/passport/transcribe',self.transcribe)
        async def close(app):
            for peer in list(self.peers.values()):await peer.ws.close()
        app.on_shutdown.append(close)
        async def cleanup(app): self.db.close()
        app.on_cleanup.append(cleanup)
        return app


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);args=p.parse_args()
    cfg=json.loads(args.config.read_text(encoding='utf-8'))
    web.run_app(Service(cfg).app(),host='127.0.0.1',port=8842,access_log=None)
