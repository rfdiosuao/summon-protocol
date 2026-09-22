import json
import unittest
from hub.passport_agent import plan_and_execute


class Reply:
    status=200
    def __init__(self,plan):self.plan=plan
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def json(self):return {'choices':[{'message':{'content':json.dumps(self.plan)}}]}


class Model:
    def __init__(self,plans):self.plans=iter(plans);self.messages=[]
    def post(self,*args,**kwargs):
        self.messages.append(list(kwargs['json']['messages']))
        return Reply(next(self.plans))


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    cfg={'base_url':'https://test.invalid','name':'test','api_key':'test'}

    async def test_discovers_then_launches_using_actual_observation(self):
        model=Model([{'command':'Get-StartApps'}, {'command':'launch discovered app'}, {'reply':'进程已出现'}])
        commands=[]
        async def execute(cap,args):
            commands.append(args['command'])
            return {'status':'COMPLETED','execution':{'stdout':'observed-app-id'}}
        reply=await plan_and_execute(model,self.cfg,'打开飞书',['command.exec'],execute)
        self.assertEqual(commands,['Get-StartApps','launch discovered app'])
        self.assertEqual(reply,'进程已出现')
        self.assertIn('observed-app-id',model.messages[1][-1]['content'])

    async def test_unknown_stops_without_replay_or_more_actions(self):
        model=Model([{'command':'test-command'}]);calls=[]
        async def execute(cap,args):calls.append(args);return {'status':'UNKNOWN'}
        self.assertIsNone(await plan_and_execute(model,self.cfg,'运行任务',['command.exec'],execute))
        self.assertEqual(len(calls),1)

    async def test_no_keyword_override_and_missing_capability_is_rejected(self):
        model=Model([{'command':'custom-time-command'}]);calls=[]
        async def execute(cap,args):calls.append(args);return {'status':'UNKNOWN'}
        await plan_and_execute(model,self.cfg,'查看时间',['command.exec'],execute)
        self.assertEqual(calls[0]['command'],'custom-time-command')
        with self.assertRaisesRegex(ValueError,'Capability unavailable'):
            await plan_and_execute(Model([{'command':'x'}]),self.cfg,'运行',['display.text'],execute)


if __name__=='__main__':unittest.main()
