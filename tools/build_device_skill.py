"""Build the public, deterministic SUMMON skill archive and checksum."""
import hashlib
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'skills/summon-device-onboarding'


def build():
    files=sorted(p for p in SOURCE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
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
