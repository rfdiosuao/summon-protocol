"""Read-only local discovery; never opens serial ports or writes hardware."""
import argparse
import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(command):
    if not shutil.which(command[0]):
        return {'status':'unavailable','command':command[0]}
    try:
        result=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=20)
        return {'status':'ok' if result.returncode==0 else 'error',
                'output':result.stdout[:20000], 'error':result.stderr[:2000],
                'truncated':len(result.stdout)>20000}
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'status':'error','error':type(exc).__name__}


def probe():
    system=platform.system()
    if system=='Windows':
        devices=run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            "Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match '^(USB|BTH)' -or $_.PNPClass -eq 'Ports' } | Select-Object Name,PNPClass,PNPDeviceID,Status | ConvertTo-Json -Depth 3 -Compress"])
    elif system=='Linux':
        devices=run(['lsusb'])
        devices['serial_candidates']=[str(p) for pattern in ('ttyUSB*','ttyACM*') for p in Path('/dev').glob(pattern)]
    elif system=='Darwin':
        devices=run(['system_profiler','SPUSBDataType','-json'])
        devices['serial_candidates']=[str(p) for p in Path('/dev').glob('cu.*')]
    else:
        devices={'status':'unsupported','output':''}
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
    args=parser.parse_args()
    report=probe()
    if args.output:
        with args.output.open('x',encoding='utf-8') as out:
            json.dump(report,out,ensure_ascii=False,indent=2)
        print('Saved local probe; admission NOT_ASSESSED:',args.output)
    else:
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
