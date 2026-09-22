"""Scrolling terminal UI; persistent logs contain lifecycle metadata only."""
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import webbrowser


def setup(config,log_file=None):
    logger=logging.getLogger('summon.gateway')
    logger.setLevel(logging.INFO)
    logger.propagate=False
    formatter=logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s',datefmt='%H:%M:%S')
    handler=logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if log_file:
        path=Path(log_file)
        path.parent.mkdir(parents=True,exist_ok=True)
        handler=RotatingFileHandler(path,maxBytes=1_000_000,backupCount=3,encoding='utf-8')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    print('\n'+'='*64)
    print('  SUMMON Gateway | 设备接入终端')
    print('  设备：'+config['shell_id']+'   模式：'+config['mode'])
    print('  Q 退出并停止设备   B 打开云端控制台   Ctrl+C 退出')
    print('  经验声明：执行结果上传云端，默认在当前账号内共享。')
    print('  日志不记录凭证或动作正文；终端任务内容只显示在窗口。')
    if log_file:print('  日志：'+str(log_file))
    print('='*64,flush=True)
    return logger


async def run(gateway):
    import os
    if os.name!='nt' or not sys.stdin.isatty():
        return await gateway.run()
    import msvcrt
    task=asyncio.create_task(gateway.run())
    try:
        while not task.done():
            if msvcrt.kbhit():
                key=msvcrt.getwch().lower()
                if key=='q':
                    logging.getLogger('summon.gateway').info('正在停止设备并退出；未上传结果保留在本地。')
                    return
                if key=='b':webbrowser.open(gateway.base)
            await asyncio.sleep(.1)
        return await task
    finally:
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
