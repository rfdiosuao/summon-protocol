import asyncio
import json
from pathlib import Path
import unittest
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from tests.test_gateway import GatewayIntegrationTests
from hub.passport_agent import plan_and_execute,run,parse_model_plan


class ModelPlanParsingTests(unittest.TestCase):
    def test_accepts_fenced_or_prefaced_json_without_moving_on_invalid_text(self):
        self.assertEqual(parse_model_plan('```json\n{"reply":"ok"}\n```'), {'reply':'ok'})
        self.assertEqual(parse_model_plan('计划如下： {"motion":null,"reply":"稍后"}'),
                         {'motion':None,'reply':'稍后'})
        with self.assertRaises(ValueError):
            parse_model_plan('no JSON plan')


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

    async def test_model_agent_may_opt_in_to_arm_gesture_at_registration(self):
        config=Path(self.tmp.name)/'arm-agent.json'
        config.write_text(json.dumps({'hub_url':self.base,'enable_arm_gestures':True,
            'model':{'base_url':'http://127.0.0.1:1','name':'unused','api_key':'unused'}}))
        credentials=Path(self.tmp.name)/'arm-agent-credentials.json'
        task=asyncio.create_task(run(config,credentials))
        try:
            await self.wait(lambda:credentials.exists())
            identity=json.loads(credentials.read_text())
            self.assertIn('arm.gesture',identity['agent']['capabilities'])
            await self.wait(lambda:self.app['hub'].agents[identity['agent']['agent_id']]['public']['status']=='ONLINE')
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def test_cross_device_agent_registers_one_identity_for_display_and_arm(self):
        config=Path(self.tmp.name)/'cross-device-agent.json'
        config.write_text(json.dumps({'hub_url':self.base,'enable_cross_device':True,
            'enable_arm_planning':True,
            'model':{'base_url':'http://127.0.0.1:1','name':'unused','api_key':'unused'}}))
        credentials=Path(self.tmp.name)/'cross-device-credentials.json'
        task=asyncio.create_task(run(config,credentials))
        try:
            await self.wait(lambda:credentials.exists())
            identity=json.loads(credentials.read_text())
            self.assertEqual(set(identity['agent']['capabilities']),
                             {'display.text','arm.observe','arm.motion'})
            await self.wait(lambda:self.app['hub'].agents[identity['agent']['agent_id']]['public']['status']=='ONLINE')
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


class ArmPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_display_reply_carries_saved_preference_after_handoff(self):
        async def model_reply(request):
            body=await request.json()
            self.assertIn('先用一句话解释',body['messages'][0]['content'])
            self.assertIn('笔记本显示壳',body['messages'][0]['content'])
            return web.json_response({'choices':[{'message':{'content':'{"reply":"这是简短介绍。"}'}}]})
        server=TestServer(web.Application())
        server.app.router.add_post('/chat/completions',model_reply)
        await server.start_server()
        calls=[]
        async def execute(cap,args):
            calls.append((cap,args))
        try:
            async with aiohttp.ClientSession() as http:
                reply=await plan_and_execute(http,{'base_url':str(server.make_url('')).rstrip('/'),
                    'name':'test','api_key':'test'},'介绍一下展品',['display.text'],execute,
                    memory={'preferences':{'response_style':'brief'}})
            self.assertEqual(reply,'这是简短介绍。')
            self.assertEqual(calls,[])
        finally:
            await server.close()

    async def test_model_observes_body_before_generating_motion(self):
        replies = iter([
            '{"motion":{"intent":"向观众挥手","speed_dps":8,"waypoints":[{"J4":-4},{"J4":0}]},"reason":"当前手腕在-12度，向负方向摆动后返回"}',
            '{"reply":"已根据实机回执完成挥手。","motion":null}',
        ])
        async def model_reply(request):
            body = await request.json()
            self.assertIn('hand_xyz_mm', str(body['messages']))
            return web.json_response({'choices':[{'message':{'content':next(replies)}}]})
        server = TestServer(web.Application())
        server.app.router.add_post('/chat/completions', model_reply)
        await server.start_server()
        calls = []
        async def execute(cap, args):
            calls.append((cap,args))
            if cap == 'arm.observe':
                return {'status':'COMPLETED','evidence':'controller_feedback',
                        'result':'{"joints_deg":{"J4":-12},"hand_xyz_mm":[260,0,205]}'}
            return {'status':'COMPLETED','evidence':'controller_feedback'}
        try:
            async with aiohttp.ClientSession() as http:
                answer = await plan_and_execute(http,{'base_url':str(server.make_url('')).rstrip('/'),
                    'name':'test','api_key':'test'},'请招手',['arm.observe','arm.motion'],execute,
                    motion_policy={'absolute_joint_windows_deg':{'J4':[-20,-5]},'max_speed_dps':8})
            self.assertEqual([item[0] for item in calls], ['arm.observe','arm.motion'])
            self.assertEqual(calls[1][1]['waypoints'], [{'J4':-4},{'J4':0}])
            self.assertIn('实机回执', answer)
        finally:
            await server.close()

    async def test_model_selects_only_recorded_catalog_name(self):
        async def model_reply(request):
            body = await request.json()
            self.assertIn('wave_wide', body['messages'][0]['content'])
            return web.json_response({'choices':[{'message':{'content':
                '{"gesture":{"name":"wave_wide","repeat":1},"reason":"远距离需要明显招手"}'}}]})
        server = TestServer(web.Application())
        server.app.router.add_post('/chat/completions', model_reply)
        await server.start_server()
        calls = []
        async def execute(cap, args):
            calls.append(args)
            return {'status':'FAILED','evidence':'none'}
        events = []
        try:
            async with aiohttp.ClientSession() as http:
                answer = await plan_and_execute(http, {'base_url':str(server.make_url('')).rstrip('/'),
                    'name':'test','api_key':'test'}, '请明显招手', ['arm.gesture'], execute,
                    gesture_catalog=[{'name':'wave_wide','description':'明显招手，约6度'}],
                    on_event=lambda role, text:events.append(text))
            self.assertEqual(calls, [{'name':'wave_wide','repeat':1}])
            self.assertIn('选择依据：远距离需要明显招手', events)
            self.assertIn('未完成', answer)
        finally:
            await server.close()

    async def test_model_uses_only_offered_gesture_and_waits_for_receipt(self):
        replies=iter(['{"gesture":{"name":"wave","repeat":1},"reply":""}',
                      '{"reply":"机械臂已完成招手。"}'])
        async def model_reply(request):
            return web.json_response({'choices':[{'message':{'content':next(replies)}}]})
        server=TestServer(web.Application())
        server.app.router.add_post('/chat/completions',model_reply)
        await server.start_server()
        calls=[]
        async def execute(cap,args):
            calls.append((cap,args))
            return {'status':'COMPLETED','evidence':'controller_feedback'}
        try:
            async with aiohttp.ClientSession() as http:
                reply=await plan_and_execute(http,{'base_url':str(server.make_url('')).rstrip('/'),
                    'name':'test','api_key':'test'},'请机械臂招手',['arm.gesture'],execute)
            self.assertEqual(calls,[('arm.gesture',{'name':'wave','repeat':1})])
            self.assertEqual(reply,'机械臂已完成招手。')
        finally:
            await server.close()
