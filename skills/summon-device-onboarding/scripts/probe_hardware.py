"""Read-only local discovery; never opens serial ports or writes hardware."""
import argparse
import json
import platform
import shutil
import subprocess
import re
from datetime import datetime, timezone
from pathlib import Path


def run(command):
    if not shutil.which(command[0]):
        return {'status':'unavailable','command':command[0]}
    try:
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='backslashreplace',timeout=20)
        return {'status':'ok' if result.returncode==0 else 'error',
                'output':result.stdout, 'error':result.stderr[:2000],
                'truncated':False}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'status':'error','error':type(exc).__name__}


def probe():
    system=platform.system()
    if system=='Windows':
        devices=run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); $OutputEncoding = [Console]::OutputEncoding; Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match '^(USB|BTH)' -or $_.PNPClass -eq 'Ports' } | Select-Object Name,PNPClass,PNPDeviceID,Status | ConvertTo-Json -Depth 3 -Compress"])
        try:
            entries=json.loads(devices.get('output','') or '[]')
            devices['devices']=entries if isinstance(entries,list) else [entries]
            devices['serial_candidates']=sorted(set(re.findall(r'\bCOM\d+\b',devices.get('output',''))))
        except ValueError:
            devices['parse_error']='Device enumeration was not valid JSON; inspect output.'
    elif system=='Linux':
        devices=run(['lsusb'])
        devices['serial_candidates']=[str(p) for pattern in ('ttyUSB*','ttyACM*') for p in Path('/dev').glob(pattern)]
    elif system=='Darwin':
        devices=run(['system_profiler','SPUSBDataType','-json'])
        devices['serial_candidates']=[str(p) for p in Path('/dev').glob('cu.*')]
    else:
        devices={'status':'unsupported','output':''}
    devices.setdefault('serial_candidates',[])
    devices.setdefault('devices',[])
    return {'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
            'host':{'os':system,'release':platform.release(),'architecture':platform.machine()},
            'tools':{tool:shutil.which(tool) for tool in ('python','git','cmake','idf.py','esptool','arduino-cli','pio','cargo','adb','bluetoothctl')},
            'discovery':devices,'admission':'NOT_ASSESSED',
            'notes':['Discovery is not proof of SDK support or connectivity.',
                     'Local device IDs may contain serial numbers; review before sharing.',
                     'No serial/BLE connection, flashing, credentials, or network upload performed.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--force',action='store_true',help='Explicitly replace an existing output file')
    parser.add_argument('--diff',nargs=2,type=Path,metavar=('BEFORE','AFTER'))
    args=parser.parse_args()
    if args.diff:
        try:
            before,after=[json.loads(p.read_text(encoding='utf-8'))['discovery'] for p in args.diff]
            def keys(value):
                return set(value.get('serial_candidates',[])) | {str(d.get('PNPDeviceID')) for d in value.get('devices',[]) if isinstance(d,dict)}
            report={'added':sorted(keys(after)-keys(before)),'removed':sorted(keys(before)-keys(after))}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            parser.error(str(exc))
    else:
        report=probe()
    if args.output:
        try:
            with args.output.open('w' if args.force else 'x',encoding='utf-8') as out:
                json.dump(report,out,ensure_ascii=False,indent=2)
        except FileExistsError:
            parser.error('Output exists: choose a new filename or explicitly use --force.')
        except OSError as exc:
            parser.error('Cannot write output; create the parent directory and check permissions: '+str(exc))
        print('Saved local discovery/diff; admission NOT_ASSESSED:',args.output)
    else:
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
