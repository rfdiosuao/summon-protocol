"""Build the public, deterministic SUMMON skill archive and checksum."""
import hashlib
import re
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'skills/summon-device-onboarding'


def build():
    # Publish a self-contained reading entry; local installation stays optional.
    base='https://github.com/rfdiosuao/summon-protocol/blob/main/skills/summon-device-onboarding/'
    skill=(SOURCE/'SKILL.md').read_text(encoding='utf-8')
    skill=re.sub(r'\]\(((?:references|assets)/[^)]+)\)',lambda m:']('+base+m.group(1)+')',skill)
    online=skill+'\n\n## 在线使用说明\n\n'
    online+='直接读取本页即可按流程工作，无需先下载或安装 Skill。下方已内联全部参考规则。需要运行探针或开发时，再获取仓库中的脚本和源码；文中 scripts/ 与 assets/ 路径相对于 skills/summon-device-onboarding/。读取网页不代表已安装或已接入设备。\n\n'
    for name in ('admission','transports','integration','firmware'):
        online+='\n---\n\n'+(SOURCE/('references/'+name+'.md')).read_text(encoding='utf-8')+'\n'
    (ROOT/'web/skill.md').write_bytes(online.encode('utf-8'))
    files=sorted((p for p in SOURCE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'),
                 key=lambda p:p.relative_to(SOURCE).as_posix())
    if not files or any(p.is_symlink() for p in SOURCE.rglob('*')):
        raise ValueError('Missing package or unexpected symlink')
    target=ROOT/'web/summon-device-onboarding.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED) as archive:
        for path in files:
            name='summon-device-onboarding/'+path.relative_to(SOURCE).as_posix()
            info=zipfile.ZipInfo(name,date_time=(2026,9,22,0,0,0))
            info.create_system=3
            info.compress_type=zipfile.ZIP_STORED
            info.external_attr=0o100644 << 16
            if path.suffix not in ('.md','.py','.json','.yaml'):
                raise ValueError('Unexpected package file: '+name)
            archive.writestr(info,path.read_text(encoding='utf-8').encode('utf-8'))
    checksum=hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(checksum+'  '+target.name+'\n',encoding='utf-8')
    print('Packaged',len(files),'files;',checksum)


if __name__=='__main__':
    build()
