import asyncio
import os
from pathlib import Path
import unittest

from gateway.commands import CommandRunner,CommandFailure


@unittest.skipUnless(os.name=='nt','Real PowerShell execution requires Windows')
class CommandTests(unittest.IsolatedAsyncioTestCase):
    def runner(self,timeout=8):return CommandRunner({'command_directory':str(Path.cwd()),'command_timeout_seconds':timeout})

    async def test_output_exit_and_nonzero(self):
        runner=self.runner()
        result=await runner.execute('Write-Output "SUMMON_COMMAND_TEST"')
        self.assertIn('SUMMON_COMMAND_TEST',result['stdout']);self.assertEqual(result['exit_code'],0)
        with self.assertRaises(CommandFailure) as caught:await runner.execute('Write-Error "expected failure"')
        self.assertNotEqual(caught.exception.result['exit_code'],0)
        self.assertIsNone(runner.process)

    async def test_timeout_and_cancel_stop_process(self):
        runner=self.runner(.3)
        with self.assertRaises(CommandFailure) as caught:await runner.execute('Start-Sleep -Seconds 20')
        self.assertTrue(caught.exception.result['timed_out']);self.assertIsNone(runner.process)
        runner=self.runner();task=asyncio.create_task(runner.execute('Start-Sleep -Seconds 20'))
        await asyncio.sleep(.4);task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        self.assertIsNone(runner.process)

    async def test_output_is_bounded_and_credentials_not_inherited(self):
        runner=self.runner()
        result=await runner.execute('Write-Output ("a" * 10000)')
        self.assertEqual(len(result['stdout']),500);self.assertTrue(result['truncated'])
        old=os.environ.get('SUMMON_TEST_SECRET');os.environ['SUMMON_TEST_SECRET']='private-marker'
        try:
            result=await runner.execute('if ($env:SUMMON_TEST_SECRET) { throw "credential inherited" }; Write-Output "clean"')
            self.assertIn('clean',result['stdout'])
        finally:
            if old is None:os.environ.pop('SUMMON_TEST_SECRET',None)
            else:os.environ['SUMMON_TEST_SECRET']=old

    async def test_background_child_is_terminated_when_command_returns(self):
        import subprocess
        runner=self.runner()
        result=await runner.execute("$p=Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 30' -PassThru; Write-Output $p.Id")
        pid=int(result['stdout'].strip())
        check=subprocess.run([runner.executable,'-NoProfile','-NonInteractive','-Command',f'if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 1 }}'],capture_output=True,timeout=5)
        self.assertEqual(check.returncode,0,'Child process outlived its owning command')
