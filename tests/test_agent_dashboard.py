import unittest

from tests.test_hub import HubTests


class AgentDashboardTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = HubTests.asyncSetUp
    asyncTearDown = HubTests.asyncTearDown
    raw_clients = HubTests.raw_clients
    post = HubTests.post

    async def test_online_agent_issues_one_time_code_for_its_own_dashboard(self):
        self.client.session.cookie_jar.clear()
        aid, agent, gateway, _, _ = await self.raw_clients()
        registration = self.app['hub'].idem['invite:/v1/agents:raw']['result']
        auth = {'Authorization': 'Bearer ' + registration['agent_token']}

        issue = await self.client.post('/v1/agent-login/codes', json={}, headers=auth)
        self.assertEqual(issue.status, 201)
        code = (await issue.json())['code']
        self.assertRegex(code, r'^[0-9A-HJKMNP-TV-Z]{5}-[0-9A-HJKMNP-TV-Z]{5}$')
        self.assertEqual((await self.client.get('/v1/agent-dashboard/me')).status, 401)
        self.assertEqual((await self.client.post('/v1/agent-login/redeem',
                         json={'code': code})).status, 403)
        self.assertEqual((await self.client.post('/v1/agent-login/redeem',
                         json={'code': code, 'nameplate': 'SMN-0000-0000'},
                         headers=self.headers)).status, 403)

        login = await self.client.post('/v1/agent-login/redeem',
                                       json={'code': code}, headers=self.headers)
        self.assertEqual(login.status, 200)
        profile = await login.json()
        self.assertEqual(profile['agent']['agent_id'], aid)
        self.assertRegex(profile['nameplate'], r'^SMN-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$')
        self.assertIn('summon_agent_session=', login.headers['Set-Cookie'])
        own = await self.client.get('/v1/agent-dashboard/me')
        self.assertEqual(await own.json(), profile)
        self.assertEqual((await self.client.post('/v1/agent-login/redeem',
                         json={'code': code}, headers=self.headers)).status, 401)

        second = await self.client.post('/v1/agent-login/codes', json={}, headers=auth)
        second_code = (await second.json())['code']
        matched = await self.client.post('/v1/agent-login/redeem',
                                         json={'code': second_code, 'nameplate': profile['nameplate']},
                                         headers=self.headers)
        self.assertEqual(matched.status, 200)
        self.assertEqual((await matched.json())['nameplate'], profile['nameplate'])

        await agent.close()
        await gateway.close()

    async def test_code_requires_agent_auth_and_online_handshake(self):
        self.client.session.cookie_jar.clear()
        self.assertEqual((await self.client.post('/v1/agent-login/codes', json={})).status, 401)
        self.assertEqual((await self.client.post('/v1/agent-login/codes', json={},
                         headers={'Authorization': 'Bearer ga'})).status, 403)
        self.assertEqual((await self.client.post('/v1/agent-login/redeem',
                         json={'code': 'AAAAA-AAAAA'}, headers=self.headers)).status, 401)

    async def test_dashboard_session_can_approve_a_device_only_for_its_agent(self):
        self.client.session.cookie_jar.clear()
        aid, agent, gateway, _, _ = await self.raw_clients()
        registration = self.app['hub'].idem['invite:/v1/agents:raw']['result']
        auth = {'Authorization': 'Bearer ' + registration['agent_token']}
        issued = await self.client.post('/v1/agent-login/codes', json={}, headers=auth)
        code = (await issued.json())['code']
        signed_in = await self.client.post('/v1/agent-login/redeem',
                                           json={'code': code}, headers=self.headers)
        self.assertEqual(signed_in.status, 200)
        own_plate = (await signed_in.json())['nameplate']

        other = await self.client.post('/v1/agents',
            headers={'Authorization': 'Bearer test-invite'},
            json={'request_id': 'other-agent', 'name': 'Other agent',
                  'bio': '', 'capabilities': ['display.text']})
        self.assertEqual(other.status, 201)
        other_id = (await other.json())['agent']['agent_id']
        other_plate = self.app['hub'].nameplates.ensure(other_id)
        listing = await self.client.get('/v1/nameplates')
        self.assertEqual([p['code'] for p in (await listing.json())['items']], [own_plate])
        self.assertEqual((await self.client.get('/v1/nameplates/' + other_plate)).status, 403)

        pair = await self.client.post('/v1/gateway/pairings',
            headers={'Authorization': 'Bearer ga'}, json={'request_id': 'pair-agent'})
        self.assertEqual(pair.status, 201)
        pairing = await pair.json()
        preview = await self.client.post('/v1/device-pairings/preview', headers=self.headers,
            json={'request_id': 'preview-agent', 'user_code': pairing['user_code']})
        self.assertEqual(preview.status, 200)
        approval = await self.client.post('/v1/device-pairings/approve', headers=self.headers,
            json={'request_id': 'approve-agent', 'user_code': pairing['user_code'],
                  'shell_id': 'shell_a', 'capabilities': ['display.text']})
        self.assertEqual(approval.status, 200)
        claim = await self.client.post('/v1/gateway/pairings/' + pairing['pairing_id'] + '/claim',
            headers={'Authorization': 'Bearer ga'}, json={'request_id': 'claim-agent'})
        grant = (await claim.json())['device_grant']
        wrong = await self.client.post('/v1/gateway/sessions',
            headers={'Authorization': 'Bearer ga', 'X-Summon-Device-Grant': grant},
            json={'request_id': 'wrong-agent', 'code': other_plate, 'agent_id': other_id})
        self.assertEqual(wrong.status, 403)
        self.assertEqual(self.app['hub'].nameplates.grants[(await approval.json())['grant_id']]['agent_id'], aid)
        await agent.close()
        await gateway.close()


if __name__ == '__main__':
    unittest.main()
