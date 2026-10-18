"""Create a reproducible, explicitly enumerated local source distribution."""
from __future__ import annotations
import hashlib
import json
import zipfile
from pathlib import Path

ROOT_FILES = ['README.md','LICENSE','.python-version','.gitignore','pyproject.toml','uv.lock']
PATTERNS = [
    'python/sse50_lab/*.py','crates/core/Cargo.toml','crates/core/Cargo.lock','crates/core/src/*.rs',
    'configs/*.yaml','scripts/*.py','scripts/*.sh','tests/*.py','docs/*.md','docs/*.json',
    'frontend/package.json','frontend/package-lock.json','frontend/tsconfig.json','frontend/vite.config.ts',
    'frontend/playwright.config.ts','frontend/index.html','frontend/src/*','frontend/e2e/*.ts','frontend/dist/**/*',
    'reports/*.json','reports/*.parquet','reports/*.html','reports/*.png','reports/*.svg',
    'evidence/*.json','evidence/*.txt','evidence/*.csv','evidence/*.parquet','evidence/*.png',
]

def file_list(root:Path):
    root=root.resolve(); candidates={root/name for name in ROOT_FILES}
    for pattern in PATTERNS:candidates.update(root.glob(pattern))
    files=[]
    for path in sorted(candidates):
        if path.is_symlink():raise ValueError(f'Package inputs must be regular local files: {path.name}')
        if not path.is_file():continue
        path.resolve().relative_to(root)
        if '__pycache__' in path.parts:continue
        files.append(path)
    missing=[name for name in ROOT_FILES if not (root/name).is_file()]
    if missing:raise ValueError('Required distribution files: '+', '.join(missing))
    return files

def package_submission(root:Path,output:Path):
    root=root.resolve();output.mkdir(parents=True,exist_ok=True)
    files=file_list(root)
    entries=[{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    source_fingerprint=hashlib.sha256('\n'.join(e['path']+':'+e['sha256'] for e in entries if e['path'].startswith(('python/','crates/','frontend/src/','configs/'))).encode()).hexdigest()
    manifest={'format':1,'project':'sse50-market-lab','version':'0.1.0','source_fingerprint':source_fingerprint,'files':entries}
    manifest_bytes=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    archive=output/'sse50-market-lab-candidate.zip';temp=archive.with_suffix('.zip.part')
    with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for relative,payload in [(e['path'],(root/e['path']).read_bytes()) for e in entries]+[('MANIFEST.json',manifest_bytes)]:
            info=zipfile.ZipInfo('sse50-market-lab/'+relative,date_time=(2026,10,17,0,0,0))
            executable=relative.startswith('scripts/') and relative.endswith('.sh')
            info.external_attr=(0o100755 if executable else 0o100644)<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,payload)
    temp.replace(archive)
    (output/'MANIFEST.json').write_bytes(manifest_bytes)
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS').write_text(sha+'  '+archive.name+'\n')
    return {'archive':str(archive),'sha256':sha,'files':len(entries),'bytes':archive.stat().st_size,'source_fingerprint':source_fingerprint}
