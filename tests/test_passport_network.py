import asyncio
import base64
import tempfile
import io
import wave
import unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from hub.passport_network import Service


class FakeSpeech:
    async def transcribe(self, pcm): return 'hello agent'
    async def synthesize(self, text): return b'\0\0'*8192


class FakeChannel:
    async def ask(self, device, plate, text): return 'agent reply','session_test'


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.service=Service({'database':str(Path(self.tmp.name)/'media.db'),
            'devices':{'passport':{'device_token':'device-test','sender_token':'sender-test',
                'default_plate':'SMN-TEST-TEST','allowed_plates':['SMN-TEST-TEST']}}},FakeSpeech(),FakeChannel())
        self.client=TestClient(TestServer(self.service.app()));await self.client.start_server()
        self.sender={'Authorization':'Bearer sender-test'}

    async def asyncTearDown(self):
        await self.client.close();self.tmp.cleanup()

    async def connect(self):
        ws=await self.client.ws_connect('/v1/passport/connect',headers={'Authorization':'Bearer device-test'})
        await ws.receive_json();await ws.send_json({'type':'hello','turn':0})
        await ws.receive_json()
        return ws

    async def playback(self,ws):
        while True:
            frame=await asyncio.wait_for(ws.receive_json(),3)
            if frame['type']=='remote.begin':
                await ws.send_json({'type':'remote.ready','turn':frame['turn']})
            elif frame['type']=='play.chunk':
                await ws.send_json({'type':'play.ack','turn':frame['turn'],'seq':frame['seq']})
            elif frame['type']=='play.end':
                await ws.send_json({'type':'play.done','turn':frame['turn'],'bytes':16384,'underruns':0})
                return

    async def receipt(self,rid):
        for _ in range(100):
            r=await self.client.get('/v1/passport/messages/'+rid,headers=self.sender)
            value=await r.json()
            if value['status'] not in ('ACCEPTED','RUNNING'):return value
            await asyncio.sleep(.01)
        self.fail('Receipt timed out')

    async def test_auth_offline_and_scoped_tokens(self):
        self.assertEqual((await self.client.post('/v1/passport/messages',json={})).status,401)
        self.assertEqual((await self.client.post('/v1/passport/messages',headers={'Authorization':'Bearer device-test'},json={})).status,401)
        r=await self.client.post('/v1/passport/messages',headers=self.sender,json={'request_id':'a','text':'hi'})
        self.assertEqual(r.status,503)

    async def test_transcription_is_separate_from_execution(self):
        data=io.BytesIO()
        with wave.open(data,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0'*4096)
        r=await self.client.post('/v1/passport/transcribe',headers=self.sender,data=data.getvalue())
        self.assertEqual(r.status,200)
        self.assertFalse((await r.json())['executed'])
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        r=await self.client.post('/v1/passport/transcribe',headers=self.sender,data=b'bad')
        self.assertEqual(r.status,400)

    async def test_remote_playback_receipt_and_persistent_idempotency(self):
        ws=await self.connect()
        body={'request_id':'remote-test','text':'hello'}
        r=await self.client.post('/v1/passport/messages',headers=self.sender,json=body)
        self.assertEqual(r.status,202)
        # Enqueueing is not completion.
        r=await self.client.get('/v1/passport/messages/remote-test',headers=self.sender)
        self.assertIn((await r.json())['status'],('ACCEPTED','RUNNING'))
        await self.playback(ws)
        self.assertEqual((await self.receipt('remote-test'))['status'],'COMPLETED')
        r=await self.client.post('/v1/passport/messages',headers=self.sender,json=body)
        self.assertEqual(r.status,200)
        self.assertEqual((await r.json())['status'],'COMPLETED')
        r=await self.client.post('/v1/passport/messages',headers=self.sender,json=dict(body,text='different'))
        self.assertEqual(r.status,409)
        await ws.close()

    async def test_microphone_cloud_reply_and_disconnect(self):
        ws=await self.connect()
        await ws.send_json({'type':'record.start','turn':1,'nameplate':'SMN-TEST-TEST'})
        for seq in range(4):
            await ws.send_json({'type':'record.chunk','turn':1,'seq':seq,'pcm':base64.b64encode(b'\0'*1024).decode()})
        await ws.send_json({'type':'record.end','turn':1,'bytes':4096})
        await self.playback(ws);await asyncio.sleep(.05)
        rows=self.service.db.execute('SELECT body FROM messages').fetchall()
        self.assertIn('COMPLETED',rows[0][0])
        await ws.close()


if __name__=='__main__':unittest.main()
