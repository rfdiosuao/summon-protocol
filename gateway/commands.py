"""Opt-in PowerShell runner. Job ownership covers cancellation and child processes."""
import asyncio
import codecs
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import time


class CommandFailure(Exception):
    def __init__(self,result):
        super().__init__('Command timed out or exited with a nonzero status')
        self.result=result


class WindowsJob:
    def __init__(self):
        class Basic(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),
                ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
                ('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];self.api.CreateJobObjectW.restype=wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];self.api.OpenProcess.restype=wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Extended();limits.BasicLimitInformation.LimitFlags=0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            self.close();raise ctypes.WinError(ctypes.get_last_error())

    def attach(self,pid):
        process=self.api.OpenProcess(0x0101,False,pid)  # SET_QUOTA | TERMINATE
        if not process:raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle,process):raise ctypes.WinError(ctypes.get_last_error())
        finally:self.api.CloseHandle(process)

    def close(self):
        if self.handle:self.api.CloseHandle(self.handle);self.handle=None


class CommandRunner:
    def __init__(self,config):
        if os.name!='nt':raise ValueError('command.exec currently requires Windows PowerShell')
        self.cwd=Path(config['command_directory']).resolve()
        if not self.cwd.is_dir():raise ValueError('Configured command_directory must exist')
        self.executable=shutil.which('powershell.exe')
        if not self.executable:raise ValueError('Windows PowerShell is unavailable')
        self.timeout=float(config.get('command_timeout_seconds',8))
        if not 0<self.timeout<=8:raise ValueError('Command timeout must be between 0 and 8 seconds')
        self.process=None;self.job=None

    async def stop(self):
        if self.job:self.job.close()
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()

    async def execute(self,command):
        if not isinstance(command,str) or not 0<len(command)<=1000 or '\x00' in command:
            raise ValueError('Command must contain 1-1000 characters and no NUL')
        if self.process is not None:raise ValueError('A command is already running')
        started=time.monotonic();timed_out=False
        wrapper="[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false); [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); $ErrorActionPreference='Stop'; try { $script=[Console]::In.ReadToEnd(); & ([ScriptBlock]::Create($script)); if (-not $?) { exit 1 }; if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
        buffers={'stdout':'','stderr':''};truncated=False;readers=[]
        async def drain(stream,key):
            nonlocal truncated
            decoder=codecs.getincrementaldecoder('utf-8')('replace')
            while True:
                chunk=await stream.read(4096)
                text=decoder.decode(chunk,final=not chunk)
                remaining=500-len(buffers[key])
                buffers[key]+=text[:remaining]
                if len(text)>remaining:truncated=True
                if not chunk:return
        self.job=WindowsJob()
        try:
            env={k:v for k,v in os.environ.items() if not k.upper().startswith('SUMMON_')}
            self.process=await asyncio.create_subprocess_exec(self.executable,'-NoLogo','-NoProfile','-NonInteractive','-Command',wrapper,
                cwd=str(self.cwd),env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW)
            # PowerShell waits for stdin; no user command runs before job ownership.
            self.job.attach(self.process.pid)
            readers=[asyncio.create_task(drain(self.process.stdout,'stdout')),asyncio.create_task(drain(self.process.stderr,'stderr'))]
            self.process.stdin.write(command.encode('utf-8'))
            await self.process.stdin.drain();self.process.stdin.close()
            try:await asyncio.wait_for(self.process.wait(),self.timeout)
            except asyncio.TimeoutError:timed_out=True;await self.stop()
            self.job.close()  # Do not leave spawned background children behind.
            await asyncio.wait_for(asyncio.gather(*readers),1)
            result=dict(buffers,exit_code=self.process.returncode,timed_out=timed_out,truncated=truncated,
                        duration_ms=round((time.monotonic()-started)*1000))
            if timed_out or self.process.returncode!=0:raise CommandFailure(result)
            return result
        finally:
            await self.stop()
            for task in readers:
                if not task.done():task.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
            self.process=None;self.job=None
