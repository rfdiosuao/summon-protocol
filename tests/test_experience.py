import tempfile
import unittest
from pathlib import Path

from hub.app import Store
from hub.experience import ExperienceLedger


class ExperienceTests(unittest.TestCase):
    def test_persistence_dedup_privacy_and_profile_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'data.db'
            store=Store(path)
            ledger=ExperienceLedger(store)
            session={'operator_id':'alice','agent_id':'a','shell_id':'screen','session_id':'s'}
            shell={'shell_id':'screen','capabilities':['display.text'],'allowed_actions':['display.text']}
            cfg={'mode':'SIMULATED'}
            ctx=ledger.context(session,shell,cfg)
            ledger.contexts['cmd']=ctx
            store.save(experience_contexts={'cmd':ctx})
            command={'request':{'command_id':'cmd','session_id':'s','action':{'capability':'display.text','args':{'text':'SECRET INPUT'}}},
                     'outcome':{'status':'COMPLETED','evidence':'device_ack','result':'SECRET OUTPUT'}}
            ledger.finish(command,'gateway')
            ledger.finish(command,'gateway')
            self.assertEqual(ledger.query('alice')['total'],1)
            self.assertNotIn('SECRET',str(ledger.query('alice')))
            self.assertEqual(ledger.query('bob')['total'],0)
            self.assertEqual(ledger.query('alice',mode='LIVE')['total'],0)
            changed=ledger.context(session,shell,dict(cfg,device_profiles={'screen':{'firmware':'v2'}}))
            self.assertEqual(ledger.query('alice',profile_id=changed['profile_id'])['total'],0)
            ledger.retrieved(session,1)
            store.db.close()
            store=Store(path)
            recovered=ExperienceLedger(store)
            self.assertEqual(recovered.query('alice')['total'],1)
            self.assertEqual(recovered.query('alice')['retrievals'],1)
            self.assertEqual(recovered.query('alice')['groups'][0]['validation'],'simulated')
            store.db.close()
