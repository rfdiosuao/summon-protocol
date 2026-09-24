import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator,FormatChecker
from gateway.runtime import Gateway,ROOT
from gateway.adapters import TerminalAdapter,SerialDisplayAdapter,DesktopAdapter


def main():
    parser=argparse.ArgumentParser(description='SUMMON Gateway: local hardware, authorized remote actions, cloud execution evidence.')
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--tui',action='store_true',help='Show scrolling status UI; Windows: Q quit, B browser')
    parser.add_argument('--log-file',type=Path,help='Rotating lifecycle log (no credentials or action text)')
    args=parser.parse_args()
    try:
        cfg=json.loads(args.config.read_text(encoding='utf-8'))
        url=urlparse(cfg['hub_url'])
        if url.username or url.password or url.query or url.fragment or (url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('127.0.0.1','localhost','::1'))):
            raise ValueError('Use HTTPS Hub URL without credentials; HTTP is allowed only for local tests')
        if cfg.get('experience_upload') is not True:
            raise ValueError('This network requires execution evidence upload; read docs/GATEWAY.md and set experience_upload=true to join')
        if type(cfg.get('enabled')) is not bool or cfg.get('mode') not in ('LIVE','SIMULATED'):
            raise ValueError('Specify enabled boolean and LIVE/SIMULATED mode')
        token=os.environ.get(cfg['token_env'],'')
        if not token:
            raise ValueError('Required Gateway token environment variable is empty')
        cfg['database']=str((args.config.resolve().parent/cfg['database']).resolve())
        kind=cfg['adapter']['kind']
        if kind=='terminal':
            adapter=TerminalAdapter(cfg['adapter'])
        elif kind=='desktop':
            adapter=DesktopAdapter(cfg['adapter'])
        elif kind=='serial-display':
            schema=json.loads((ROOT/'protocol/summon.schema.json').read_text(encoding='utf-8'))
            validator=Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/BridgeMessage'},format_checker=FormatChecker())
            adapter=SerialDisplayAdapter(dict(cfg['adapter'],shell_id=cfg['shell_id']),validator)
        elif kind=='arm-console':
            from gateway.arm_console import ArmConsoleAdapter
            adapter=ArmConsoleAdapter(cfg['adapter'])
        else:
            raise ValueError('Unknown adapter; implemented kinds: terminal, desktop, serial-display, arm-console')
        gateway=Gateway(cfg,token,adapter)
    except (KeyError,OSError,ValueError,TypeError) as exc:
        parser.error(str(exc))
    print('SUMMON Gateway: execution results upload to the configured cloud Hub; account-scoped evidence, no automatic public sharing.',flush=True)
    from gateway.console import setup,run
    logger=setup(cfg,args.log_file)
    try:
        asyncio.run(run(gateway) if args.tui else gateway.run())
    except KeyboardInterrupt:
        logger.info('已请求退出。')
    except Exception as exc:
        detail=str(exc).replace(token,'[REDACTED]')
        logger.error('启动或运行失败 [%s]：%s',type(exc).__name__,detail)
        raise SystemExit(1)
    finally:
        gateway.close()
        logger.info('Gateway 已停止。')


if __name__=='__main__':
    main()
