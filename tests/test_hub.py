import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from hub.app import create_app


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.tmp.name) / 'hub.db', {
            'origin': 'https://summon.test', 'operator_codes': {'test-access': 'operator_a', 'other-access': 'operator_b'},
            'invite': 'test-invite', 'gateway_tokens': {'shell_a': 'ga', 'shell_b': 'gb'},
            'mode': 'SIMULATED', 'secure_cookie': False,
        })
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {'Origin': 'https://summon.test'}
        r = await self.post('/v1/operator-session', {'access_code': 'test-access'})
        self.assertEqual(r.status, 200)

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def post(self, path, body, **kw):
        return await self.client.post(path, json=body, headers=self.headers, **kw)

    async def test_auth_and_origin(self):
        r = await self.client.post('/v1/sessions', json={'request_id':'r','agent_id':'x','shell_id':'shell_a'})
        self.assertEqual(r.status, 403)
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.get('/v1/state')).status, 401)
        self.assertEqual((await self.client.get('/v1/catalog')).status, 200)

    async def test_public_resources_and_api_errors(self):
        self.client.session.cookie_jar.clear()
        for path,mime in [('/assets/agent.md','text/plain'),('/assets/device-onboarding.md','text/plain'),('/assets/style.css','text/css'),('/assets/app.js','text/javascript')]:
            r=await self.client.get(path)
            self.assertEqual(r.status,200)
            self.assertEqual(r.headers['Content-Type'],mime+'; charset=utf-8')
        for path,status in [('/v1/agents',405),('/v1/not-a-route',404),('/v1/catalog/',404),('/v1/sessions/missing/memory',401),('/v1/commands/missing',401)]:
            r=await self.client.get(path)
            self.assertEqual(r.status,status)
            self.app['hub'].validate('ErrorResponse',await r.json())
        r=await self.client.get('/healthz')
        self.app['hub'].validate('Health',await r.json())
        for path,definition in [('/v1/catalog','Catalog'),('/v1/catalog?details=1','CatalogDetailed')]:
            r=await self.client.get(path)
            self.app['hub'].validate(definition,await r.json())
        messages=[]
        for headers in [{},{'Authorization':'Bearer invalid'}]:
            r=await self.client.get('/v1/connect',headers=headers)
            self.assertEqual(r.status,401)
            messages.append((await r.json())['error']['message'])
        self.assertNotEqual(*messages)

    async def test_connection_self_check(self):
        aid,agent,gateway,send,receive=await self.raw_clients()
        registration=self.app['hub'].idem['invite:/v1/agents:raw']['result']
        headers={'Authorization':'Bearer '+registration['agent_token']}
        r=await self.client.get('/v1/agents/me',headers=headers)
        body=await r.json()
        self.app['hub'].validate('AgentConnection',body)
        self.assertTrue(body['connected'])
        self.assertEqual(body['agent']['agent_id'],aid)
        self.assertIsNotNone(body['last_handshake_at'])
        await agent.close()
        await asyncio.sleep(.03)
        body=await (await self.client.get('/v1/agents/me',headers=headers)).json()
        self.assertFalse(body['connected'])
        self.assertEqual((await self.client.get('/v1/agents/me',headers={'Authorization':'Bearer ga'})).status,403)
        await gateway.close()

    async def test_registration_idempotency(self):
        body = {'request_id':'reg','name':'Test','bio':'','capabilities':['display.text']}
        headers = {'Authorization':'Bearer test-invite'}
        r = await self.client.post('/v1/agents', json=body, headers=headers)
        self.assertEqual(r.status, 201)
        first = await r.json()
        r = await self.client.post('/v1/agents', json=body, headers=headers)
        self.assertEqual(await r.json(), first)
        body['name'] = 'Other'
        r = await self.client.post('/v1/agents', json=body, headers=headers)
        self.assertEqual(r.status, 409)

    async def test_complete_simulated_handoff(self):
        from hub.simulator import DemoFleet
        fleet = DemoFleet(str(self.client.make_url('')).rstrip('/'), 'test-invite', {'shell_a':'ga','shell_b':'gb'})
        await fleet.start()
        try:
            for _ in range(100):
                state = await (await self.client.get('/v1/state')).json()
                if state['agents'] and all(s['state']=='IDLE' for s in state['shells']):
                    break
                await asyncio.sleep(.03)
            aid = state['agents'][0]['agent_id']
            r = await self.post('/v1/sessions', {'request_id':'open','agent_id':aid,'shell_id':'shell_a'})
            self.assertEqual(r.status, 202)
            sid = (await r.json())['session']['session_id']
            await self.wait_session(sid, 'ACTIVE')
            r = await self.post('/v1/sessions', {'request_id':'busy','agent_id':aid,'shell_id':'shell_b'})
            self.assertEqual(r.status, 409)
            r = await self.post('/v1/sessions/'+sid+'/feedback', {'request_id':'fb','update_id':'u','expected_version':0,'patch':{'response_style':'brief'}})
            self.assertEqual(r.status, 200)
            self.assertEqual((await r.json())['memory_version'], 1)
            r = await self.post('/v1/sessions/'+sid+'/feedback', {'request_id':'conflict','update_id':'u2','expected_version':0,'patch':{'response_style':'detailed'}})
            self.assertEqual(r.status, 409)
            r = await self.post('/v1/sessions/'+sid+'/inputs', {'request_id':'in','text':'介绍一下展品'})
            self.assertEqual(r.status, 202)
            for _ in range(100):
                state = await (await self.client.get('/v1/state')).json()
                if state['commands'] and state['commands'][-1]['outcome']['status']=='COMPLETED':
                    break
                await asyncio.sleep(.03)
            self.assertEqual(state['commands'][-1]['outcome']['status'], 'COMPLETED')
            experiences=await (await self.client.get('/v1/experiences')).json()
            self.app['hub'].validate('ExperienceResult',experiences)
            self.assertEqual(experiences['total'],1)
            self.assertEqual(experiences['retrievals'],1)
            self.assertEqual(experiences['items'][0]['shell_id'],'shell_a')
            self.assertNotIn('介绍一下展品',str(experiences))
            same=await (await self.client.get('/v1/sessions/'+sid+'/experiences')).json()
            self.assertEqual(same['total'],1)
            r = await self.post('/v1/sessions/'+sid+'/handoff', {'request_id':'move','target_shell_id':'shell_b'})
            self.assertEqual(r.status, 202)
            for _ in range(100):
                state = await (await self.client.get('/v1/state')).json()
                target = [s for s in state['sessions'] if s['shell_id']=='shell_b' and s['state']=='ACTIVE']
                if target:
                    break
                await asyncio.sleep(.03)
            self.assertTrue(target)
            self.assertEqual(target[0]['memory_version'], 1)
            old = next(s for s in state['sessions'] if s['session_id']==sid)
            self.assertEqual(old['state'], 'RELEASED')
            registration=self.app['hub'].idem['invite:/v1/agents:demo_agent_registration_v1']['result']
            cid=state['commands'][-1]['request']['command_id']
            r=await self.client.get('/v1/commands/'+cid,headers={'Authorization':'Bearer '+registration['agent_token']})
            self.assertEqual(r.status,200)
            self.app['hub'].validate('ActionRecord',await r.json())
            other=await (await self.client.get('/v1/sessions/'+target[0]['session_id']+'/experiences')).json()
            self.assertEqual(other['total'],0)
            self.assertEqual((await self.client.get('/v1/sessions/'+sid+'/experiences')).status,409)
            await self.post('/v1/operator-session', {'access_code':'other-access'})
            self.assertEqual((await (await self.client.get('/v1/experiences')).json())['total'],0)
            self.assertEqual((await self.client.get('/v1/sessions/'+sid+'/memory')).status, 403)
        finally:
            await fleet.close()

    async def wait_session(self, sid, status):
        for _ in range(100):
            state = await (await self.client.get('/v1/state')).json()
            if any(s['session_id']==sid and s['state']==status for s in state['sessions']):
                return
            await asyncio.sleep(.03)
        self.fail('Session did not reach '+status)

    async def raw_clients(self):
        from hub.app import uid, utc
        r=await self.client.post('/v1/agents',headers={'Authorization':'Bearer test-invite'},json={'request_id':'raw','name':'Raw','bio':'','capabilities':['display.text']})
        registration=await r.json()
        aid=registration['agent']['agent_id']
        agent=await self.client.ws_connect('/v1/connect',headers={'Authorization':'Bearer '+registration['agent_token']})
        gateway=await self.client.ws_connect('/v1/connect',headers={'Authorization':'Bearer ga'})
        async def send(ws,kind,payload):
            await ws.send_json({'v':1,'message_id':uid('m'),'sent_at':utc(),'type':kind,'payload':payload})
        async def receive(ws,kind):
            for _ in range(40):
                message=await ws.receive_json(timeout=2)
                if message['type']=='heartbeat':
                    await send(ws,'heartbeat',message['payload'])
                elif message['type']==kind:
                    return message['payload']
            self.fail('Missing message '+kind)
        await send(agent,'hello',{'role':'agent','id':aid})
        await send(gateway,'hello',{'role':'gateway','id':'shell_a'})
        await receive(agent,'welcome'); await receive(gateway,'welcome')
        await send(gateway,'shell.report',{'shell_id':'shell_a','physical_state':'READY','enabled':True,'detail':''})
        await asyncio.sleep(.03)
        return aid,agent,gateway,send,receive

    async def test_no_activation_before_both_ready_and_stale_action(self):
        from hub.app import utc
        import time
        aid,agent,gateway,send,receive=await self.raw_clients()
        r=await self.post('/v1/sessions',{'request_id':'s','agent_id':aid,'shell_id':'shell_a'})
        s=(await r.json())['session']; sid=s['session_id']
        await receive(agent,'session.offer')
        offer=await receive(gateway,'session.offer')
        self.assertNotIn('memory',offer)
        r=await self.post('/v1/sessions/'+sid+'/inputs',{'request_id':'early','text':'too early'})
        self.assertEqual(r.status,409)
        await send(agent,'session.ready',{'session_id':sid,'lease_epoch':s['lease_epoch'],'role':'agent'})
        await asyncio.sleep(.03)
        self.assertEqual(self.app['hub'].sessions[sid]['state'],'CONNECTING')
        await send(gateway,'session.ready',{'session_id':sid,'lease_epoch':s['lease_epoch'],'role':'gateway'})
        await receive(agent,'session.activate'); await receive(gateway,'session.activate')
        r=await self.post('/v1/sessions/'+sid+'/inputs',{'request_id':'input','text':'hello'})
        iid=(await r.json())['input_id']
        q={'session_id':sid,'input_id':iid,'lease_epoch':s['lease_epoch']+1,'command_id':'stale','seq':1,'expires_at':utc(time.time()+4),'action':{'capability':'display.text','args':{'text':'x'}}}
        await send(agent,'action.request',q)
        result=await receive(agent,'error')
        self.assertEqual(result['error']['error']['code'],'STALE_LEASE')
        self.assertFalse(self.app['hub'].commands)
        q['lease_epoch']=s['lease_epoch']; q['expires_at']=utc(time.time()-1)
        await send(agent,'action.request',q)
        self.assertEqual((await receive(agent,'error'))['error']['error']['code'],'EXPIRED')
        await agent.close(); await gateway.close()

    async def test_release_without_stop_keeps_occupancy(self):
        aid,agent,gateway,send,receive=await self.raw_clients()
        r=await self.post('/v1/sessions',{'request_id':'s','agent_id':aid,'shell_id':'shell_a'})
        s=(await r.json())['session']; sid=s['session_id']
        await receive(agent,'session.offer'); await receive(gateway,'session.offer')
        for ws,role in [(agent,'agent'),(gateway,'gateway')]:
            await send(ws,'session.ready',{'session_id':sid,'lease_epoch':s['lease_epoch'],'role':role})
        await receive(agent,'session.activate')
        await self.post('/v1/sessions/'+sid+'/release',{'request_id':'release'})
        await send(agent,'input.finished',{'session_id':sid,'input_id':'late','status':'FAILED'})
        self.assertEqual((await receive(agent,'error'))['error']['error']['code'],'SESSION_NOT_ACTIVE')
        self.assertEqual(self.app['hub'].shells['shell_a']['current_session_id'],sid)
        r=await self.post('/v1/sessions',{'request_id':'again','agent_id':aid,'shell_id':'shell_a'})
        self.assertEqual(r.status,409)
        await agent.close(); await gateway.close()
