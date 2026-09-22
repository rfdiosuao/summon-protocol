import copy
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock
from gateway.adapters import TerminalAdapter
from gateway.runtime import Gateway
from hub.app import utc


class TimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_delay_is_not_future_clock_skew_but_expiry_still_applies(self):
        with tempfile.TemporaryDirectory() as folder:
            output=io.StringIO()
            g=Gateway({'hub_url':'http://localhost','shell_id':'pc','database':str(Path(folder)/'state.db')},'unused',TerminalAdapter(output=output))
            try:
                g.active=True;g.session={'session_id':'session_test','shell_id':'pc','lease_epoch':1,'expires_at':utc(time.time()+30)}
                g.deadline=time.monotonic()+30;g.permitted={'display.text'};g.send=AsyncMock()
                p={'session_id':'session_test','input_id':'input_test','lease_epoch':1,'command_id':'command_test','seq':1,
                   'expires_at':utc(time.time()+2),'action':{'capability':'display.text','args':{'text':'delayed but valid'}}}
                message={'v':1,'message_id':'message_test','sent_at':utc(time.time()-1),'type':'action.request','payload':p}
                await g.message(message);await g.work
                self.assertIn('delayed but valid',output.getvalue())
                bad=copy.deepcopy(message);bad['payload'].update(command_id='expired',seq=2,expires_at=utc(time.time()-1))
                with self.assertRaisesRegex(ValueError,'Expired'):await g.message(bad)
                bad=copy.deepcopy(message);bad['sent_at']=utc(time.time()+2);bad['payload'].update(command_id='future',seq=2,expires_at=utc(time.time()+3))
                with self.assertRaisesRegex(ValueError,'clock skew'):await g.message(bad)
            finally:g.close()
