import asyncio
import copy
import io
import json
import queue
import types
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient,TestServer
from gateway.adapters import TerminalAdapter
from gateway.runtime import Gateway,utc
from gateway.store import Journal,failed
from hub.app import create_app
from hub.simulator import DemoFleet


def request(cid='command_a'):
    from hub.app import utc as at
    return {'session_id':'session_a','input_id':'input_a','lease_epoch':1,'command_id':cid,'seq':1,
            'expires_at':at(time.time()+4),'action':{'capability':'display.text','args':{'text':'hello'}}}


class JournalTests(unittest.TestCase):
    def test_crash_recovery_and_conflicting_id_never_reexecute(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'journal.db'
            journal=Journal(path)
            with self.assertRaisesRegex(ValueError,'already in use'):
                Journal(path)
            q=request()
            self.assertIsNone(journal.reserve(q))
            journal.epoch(4)
            journal.close()
            journal=Journal(path)
            self.assertEqual(journal.reserve(q)['outcome']['status'],'UNKNOWN')
            self.assertEqual(len(journal.pending()),1)
            other=copy.deepcopy(q);other['action']['args']['text']='different'
            with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
                journal.reserve(other)
            with self.assertRaisesRegex(ValueError,'STALE_LEASE'):
                journal.epoch(4)
            journal.acknowledge(q['command_id'])
            journal.close()
            journal=Journal(path)
            self.assertFalse(journal.pending())
            journal.close()


class GatewayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.profile={'model':'PC Terminal','firmware':'host','adapter_version':'terminal-1'}
        cfg={'origin':'https://test.local','operator_codes':{'access':'owner'},'invite':'invite',
             'gateway_tokens':{'pc':'gateway-secret','other':'other-secret'},'mode':'SIMULATED','secure_cookie':False,
             'device_profiles':{'pc':self.profile},'shell_policies':{'pc':{'identity_gates':['web'],'stop_kind':'local_disable'}}}
        self.app=create_app(Path(self.tmp.name)/'hub.db',cfg)
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        self.base=str(self.client.make_url('')).rstrip('/')
        self.headers={'Origin':'https://test.local'}
        await self.client.post('/v1/operator-session',headers=self.headers,json={'access_code':'access'})
        self.output=io.StringIO()
        self.gateway=Gateway({'hub_url':self.base,'shell_id':'pc','database':str(Path(self.tmp.name)/'gateway.db'),
            'profile':self.profile,'enabled':True,'mode':'SIMULATED','experience_upload':True},'gateway-secret',TerminalAdapter(output=self.output))
        self.task=asyncio.create_task(self.gateway.run())
        self.fleet=DemoFleet(self.base,'invite',{})
        await self.fleet.start()
        await self.wait(lambda:self.app['hub'].shells['pc']['state']=='IDLE' and bool(self.app['hub'].agents) and next(iter(self.app['hub'].agents.values()))['public']['status']=='ONLINE')

    async def asyncTearDown(self):
        self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        self.gateway.close()
        await self.fleet.close();await self.client.close();self.tmp.cleanup()

    async def wait(self,predicate):
        for _ in range(150):
            if predicate():return
            if self.task.done():self.task.result()
            await asyncio.sleep(.02)
        self.fail('Gateway condition timed out')

    async def test_terminal_cloud_experience_duplicate_and_late_receipt(self):
        hub=self.app['hub']
        aid=next(iter(hub.agents))
        r=await self.client.post('/v1/sessions',headers=self.headers,json={'request_id':'open','shell_id':'pc','agent_id':aid})
        self.assertEqual(r.status,202)
        sid=(await r.json())['session']['session_id']
        await self.wait(lambda:hub.sessions[sid]['state']=='ACTIVE')
        await self.client.post('/v1/sessions/'+sid+'/inputs',headers=self.headers,json={'request_id':'input','text':'Gateway end-to-end marker'})
        await self.wait(lambda:bool(hub.gateway_receipts) and not self.gateway.journal.pending())
        self.assertIn('Gateway end-to-end marker',self.output.getvalue())
        cid=next(iter(hub.gateway_receipts));outcome=hub.commands[cid]['outcome']
        self.assertEqual(outcome['status'],'COMPLETED')
        await self.gateway.message({'v':1,'message_id':'duplicate','sent_at':utc(),'type':'action.request','payload':hub.commands[cid]['request']})
        self.assertEqual(self.output.getvalue().count('Gateway end-to-end marker'),1)
        history=await (await self.client.get('/v1/experiences')).json()
        hub.validate('ExperienceResult',history)
        self.assertEqual(history['total'],1)
        self.assertNotIn('Gateway end-to-end marker',str(history))
        self.assertEqual(history['items'][0]['profile']['model'],'PC Terminal')
        headers={'Authorization':'Bearer gateway-secret'}
        r=await self.client.post('/v1/gateway/results',headers=headers,json=outcome)
        self.assertEqual(r.status,200);hub.validate('GatewayReceipt',await r.json())
        self.assertEqual(len(hub.experiences.records),1)
        r=await self.client.post('/v1/gateway/results',headers={'Authorization':'Bearer other-secret'},json=outcome)
        self.assertEqual(r.status,403)
        changed=dict(outcome,result='different')
        self.assertEqual((await self.client.post('/v1/gateway/results',headers=headers,json=changed)).status,409)
        # A late receipt preserves UNKNOWN and does not manufacture success history.
        q=copy.deepcopy(hub.commands[cid]['request']);q['command_id']='late_command'
        record={'request':q,'outcome':failed(q)}
        hub.commands['late_command']=record
        hub.experiences.contexts['late_command']=hub.experiences.context(hub.sessions[sid],hub.shells['pc'],hub.cfg)
        hub.experiences.finish(record,'timeout')
        late=dict(outcome,command_id='late_command')
        r=await self.client.post('/v1/gateway/results',headers=headers,json=late)
        self.assertTrue((await r.json())['late'])
        self.assertEqual(hub.commands['late_command']['outcome']['status'],'UNKNOWN')
        self.assertEqual(hub.experiences.records['late_command']['status'],'UNKNOWN')
        self.assertEqual(hub.gateway_receipts['late_command']['outcome']['status'],'COMPLETED')
        await self.client.post('/v1/sessions/'+sid+'/release',headers=self.headers,json={'request_id':'release'})
        await self.wait(lambda:hub.sessions[sid]['state']=='RELEASED')

    async def test_config_profile_and_capabilities_are_deployment_owned(self):
        r=await self.client.get('/v1/gateway/config',headers={'Authorization':'Bearer gateway-secret'})
        body=await r.json();self.app['hub'].validate('GatewayConfig',body)
        self.assertEqual(body['shell']['identity_gates'],['web'])
        self.assertEqual(body['profile']['model'],'PC Terminal')
        self.gateway.config['profile']={'model':'wrong','firmware':'host','adapter_version':'terminal-1'}
        with self.assertRaisesRegex(ValueError,'profile'):
            await self.gateway.preflight()

    async def test_delayed_hello_receives_welcome_before_heartbeat(self):
        async with self.client.ws_connect('/v1/connect',headers={'Authorization':'Bearer other-secret'}) as ws:
            await asyncio.sleep(.7)  # Cross a Hub tick before sending hello.
            await ws.send_json({'v':1,'message_id':'delayed_hello','sent_at':utc(),
                               'type':'hello','payload':{'role':'gateway','id':'other'}})
            message=await ws.receive_json(timeout=2)
            self.assertEqual(message['type'],'welcome')

    async def test_cloud_failure_retains_result_until_ack_without_reexecution(self):
        hub=self.app['hub']
        class Unavailable:
            status=503
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        original=self.gateway.http.post
        self.gateway.http.post=lambda *args,**kwargs:Unavailable()
        try:
            r=await self.client.post('/v1/sessions',headers=self.headers,json={
                'request_id':'outbox_open','shell_id':'pc','agent_id':next(iter(hub.agents))})
            sid=(await r.json())['session']['session_id']
            await self.wait(lambda:hub.sessions[sid]['state']=='ACTIVE')
            await self.client.post('/v1/sessions/'+sid+'/inputs',headers=self.headers,json={
                'request_id':'outbox_input','text':'outbox-marker'})
            await self.wait(lambda:bool(self.gateway.journal.pending()))
            with self.assertRaises(ConnectionError):
                await self.gateway.flush()
            self.assertFalse(hub.gateway_receipts)
            self.assertEqual(len(self.gateway.journal.pending()),1)
        finally:
            self.gateway.http.post=original
        await self.gateway.flush()
        self.assertFalse(self.gateway.journal.pending())
        self.assertEqual(len(hub.gateway_receipts),1)
        self.assertEqual(self.output.getvalue().count('outbox-marker'),1)


class SerialAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_virtual_serial_requires_matching_completion_and_stop(self):
        # A protocol peer, explicitly not physical hardware evidence.
        from unittest.mock import patch
        from jsonschema import Draft202012Validator,FormatChecker
        from gateway.adapters import SerialDisplayAdapter
        from gateway.runtime import ROOT
        defs=json.loads((ROOT/'protocol/summon.schema.json').read_text(encoding='utf-8'))['$defs']
        validator=Draft202012Validator({'$defs':defs,'$ref':'#/$defs/BridgeMessage'},format_checker=FormatChecker())
        frames=queue.Queue()
        writes=[]
        def respond(kind,payload):
            frames.put((json.dumps({'v':1,'message_id':'device_message','sent_at':utc(),'type':kind,'payload':payload})+'\n').encode())
        respond('device.hello',{'device_id':'device_a','shell_id':'shell_a'})
        class Port:
            def read_until(self,*args):
                try:return frames.get(timeout=.05)
                except queue.Empty:return b''
            def write(self,raw):
                message=json.loads(raw);validator.validate(message);writes.append(message)
                p=message['payload']
                if message['type']=='device.command':
                    respond('device.result',{'device_id':'device_a','command_id':p['command_id'],'status':'COMPLETED','detail':'rendered'})
                elif message['type']=='device.stop':
                    respond('device.stopped',dict(p,device_id='device_a'))
                else:
                    respond('device.heartbeat',{'device_id':'device_a'})
                return len(raw)
            def close(self):pass
        adapter=SerialDisplayAdapter({'port':'TEST_ONLY','device_id':'device_a','shell_id':'shell_a'},validator)
        with patch.dict('sys.modules',{'serial':types.SimpleNamespace(Serial=lambda *a,**kw:Port())}):
            await adapter.open()
            try:
                result=await adapter.execute(request())
                self.assertEqual(result['evidence'],'device_ack')
                self.assertTrue(await adapter.stop({'session_id':'session_a','lease_epoch':1}))
                self.assertTrue(adapter.healthy())
            finally:
                await adapter.close()
        self.assertIn('device.command',[m['type'] for m in writes])
        self.assertIn('device.stop',[m['type'] for m in writes])
