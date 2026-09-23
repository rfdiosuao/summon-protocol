import unittest
import re

from tests.test_hub import HubTests, REG_RAW


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = HubTests.asyncSetUp
    asyncTearDown = HubTests.asyncTearDown
    raw_clients = HubTests.raw_clients
    post = HubTests.post

    async def test_designer_frontend_and_authoritative_skill_are_served(self):
        page = await self.client.get('/')
        html = await page.text()
        self.assertEqual(page.status, 200)
        self.assertIn('<div id="root"></div>', html)
        script = re.search(r'src="(/assets/index-[^"]+\.js)"', html)
        self.assertIsNotNone(script)
        asset = await self.client.get(script.group(1))
        self.assertEqual(asset.status, 200)
        self.assertIn('text/javascript', asset.headers['Content-Type'])
        skill = await self.client.get('/skill.md')
        self.assertEqual(skill.status, 200)
        self.assertIn('SUMMON 设备接入', await skill.text())
        self.assertEqual((await self.client.get('/assets/console.html')).status, 200)

    async def test_only_claiming_agent_can_complete_its_browser_flow(self):
        self.assertEqual((await self.client.post('/v1/onboarding/intents', json={})).status, 403)
        response = await self.client.post('/v1/onboarding/intents', headers=self.headers, json={})
        self.assertEqual(response.status, 201)
        token = (await response.json())['token']
        status = await self.client.post('/v1/onboarding/status', headers=self.headers, json={'token': token})
        self.assertEqual((await status.json())['state'], 'WAITING')
        self.assertEqual((await self.client.post('/v1/onboarding/claim', json={'token': token})).status, 401)
        self.assertEqual((await self.client.post('/v1/onboarding/claim',
                         headers={'Authorization': 'Bearer ga'}, json={'token': token})).status, 403)
        aid, agent, gateway, _, _ = await self.raw_clients()
        registration = self.app['hub'].idem['public-registration:/v1/agents:'+REG_RAW]['result']
        claim = await self.client.post('/v1/onboarding/claim',
            headers={'Authorization': 'Bearer ' + registration['agent_token']},
            json={'token': token})
        self.assertEqual(claim.status, 200)
        code = (await claim.json())['nameplate']
        status = await self.client.post('/v1/onboarding/status', headers=self.headers, json={'token': token})
        result = await status.json()
        self.assertEqual((result['state'], result['agent']['agent_id'], result['nameplate']),
                         ('ONLINE', aid, code))
        public = await self.client.get('/v1/nameplates/public/' + code)
        self.assertEqual((await public.json())['agent']['agent_id'], aid)
        await agent.close()
        await gateway.close()
        status = await self.client.post('/v1/onboarding/status', headers=self.headers, json={'token': token})
        self.assertEqual((await status.json())['state'], 'CLAIMED')

    async def test_expired_or_invalid_intent_cannot_claim(self):
        token = (await (await self.client.post('/v1/onboarding/intents',
                 headers=self.headers, json={})).json())['token']
        self.assertEqual((await self.client.post('/v1/onboarding/status', headers=self.headers,
                         json={'token': 'wrong'})).status, 404)
        self.app['hub'].onboarding.intents[token]['expires'] = 0
        self.assertEqual((await self.client.post('/v1/onboarding/status', headers=self.headers,
                         json={'token': token})).status, 404)
        self.assertEqual((await self.client.get('/v1/nameplates/public/SMN-0000-0000')).status, 404)


if __name__ == '__main__':
    unittest.main()
