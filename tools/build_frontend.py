"""Install the designer's Vite build into the Hub's existing /assets mapping.

The Hub owns its API/Skill markdown. Never replace those contract documents
with copies bundled in the design ZIP.
"""

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
DIST = WEB / 'frontend' / 'dist'


def main():
    if not (DIST / 'index.html').is_file():
        raise SystemExit('Run npm run build in web/frontend first.')
    assets = DIST / 'assets'
    if not assets.is_dir() or not list(assets.glob('index-*.js')):
        raise SystemExit('Missing Vite assets.')
    if not (WEB / 'console.html').exists():
        shutil.copyfile(WEB / 'index.html', WEB / 'console.html')
    for source in assets.rglob('*'):
        if not source.is_file() or source.suffix.lower() == '.md':
            continue
        target = WEB / source.relative_to(assets)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(DIST / 'index.html', WEB / 'index.html')
    print('Installed designer frontend without replacing Hub API/Skill docs.')


if __name__ == '__main__':
    main()
