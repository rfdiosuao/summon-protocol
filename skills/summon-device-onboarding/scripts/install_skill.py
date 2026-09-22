"""Install this skill without overwriting an existing installation or running hardware code."""
import argparse
import os
import shutil
from pathlib import Path


def install(source, skills_dir):
    source=Path(source).resolve()
    target=Path(skills_dir).expanduser().resolve()/'summon-device-onboarding'
    if target.exists() or target.is_symlink():
        raise FileExistsError('Skill already exists; review differences before updating: '+str(target))
    for p in source.rglob('*'):
        if p.is_symlink():
            raise ValueError('Refusing symlink in skill package: '+str(p))
    if not (source/'SKILL.md').is_file():
        raise ValueError('Missing SKILL.md')
    if target==source or source in target.parents:
        raise ValueError('Install destination must be outside package source')
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent',choices=('codex','claude'),default='codex')
    parser.add_argument('--skills-dir',type=Path,help='Explicit skill root for another compatible host')
    args=parser.parse_args()
    default=(Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'skills'
             if args.agent=='codex' else Path.home()/'.claude/skills')
    try:
        target=install(Path(__file__).resolve().parents[1],args.skills_dir or default)
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print('Installed:',target)
    print('Reload skills or start a new session if the host needs it. No device was modified.')


if __name__=='__main__':
    main()
