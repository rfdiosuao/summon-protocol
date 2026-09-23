import asyncio
import json
from pathlib import Path
import unittest
from aiohttp import web
from aiohttp.test_utils import TestServer
from tests.test_gateway import GatewayIntegrationTests
from hub.passport_agent import run


class PassportAgentTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=GatewayIntegrationTests.asyncSetUp
    asyncTearDown=GatewayIntegrationTests.asyncTearDown
    wait=GatewayIntegrationTests.wait

    async def test_first_registration_needs_no_invite_and_receives_nameplate(self):
        config=Path(self.tmp.name)/'passport-self-registration.json'
        config.write_text(json.dumps({'hub_url':self.base}))
        credentials=Path(self.tmp.name)/'passport-self-credentials.json'
        task=asyncio.create_task(run(config,credentials))
        try:
            await self.wait(lambda:credentials.exists())
            identity=json.loads(credentials.read_text())
            aid=identity['agent']['agent_id']
            await self.wait(lambda:self.app['hub'].agents[aid]['public']['status']=='ONLINE')
            response=await self.client.get('/v1/agents/me/nameplate',headers={
                'Authorization':'Bearer '+identity['agent_token']})
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())['agent']['agent_id'],aid)
            self.assertFalse(credentials.with_name(credentials.name+'.registration').exists())
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_model_reply_uses_offer_capabilities_and_cloud_receipt(self):
        async def reply(request):
            self.assertEqual(request.headers['Authorization'],'Bearer model-test')
            return web.json_response({'choices':[{'message':{'content':'{"reply":"passport-model-reply"}'}}]})
        model=web.Application();model.router.add_post('/chat/completions',reply)
        modelserver=TestServer(model);await modelserver.start_server()
        config=Path(self.tmp.name)/'passport.json'
        config.write_text(json.dumps({'hub_url':self.base,
            'model':{'base_url':str(modelserver.make_url('')).rstrip('/'),'name':'test','api_key':'model-test'}}))
        credentials=Path(self.tmp.name)/'passport-credentials.json'
        agent=asyncio.create_task(run(config,credentials))
        try:
            await self.wait(lambda:credentials.exists())
            aid=json.loads(credentials.read_text())['agent']['agent_id']
            await self.wait(lambda:self.app['hub'].agents[aid]['public']['status']=='ONLINE')
            response=await self.client.post('/v1/sessions',headers=self.headers,json={'request_id':'passport-session','agent_id':aid,'shell_id':'pc'})
            self.assertEqual(response.status,202)
            sid=(await response.json())['session']['session_id']
            await self.wait(lambda:self.app['hub'].sessions[sid]['state']=='ACTIVE')
            await self.client.post('/v1/sessions/'+sid+'/inputs',headers=self.headers,json={'request_id':'passport-input','text':'hello'})
            await self.wait(lambda:'passport-model-reply' in self.output.getvalue())
            await self.wait(lambda:bool(self.app['hub'].gateway_receipts))
        finally:
            agent.cancel();await asyncio.gather(agent,return_exceptions=True);await modelserver.close()
