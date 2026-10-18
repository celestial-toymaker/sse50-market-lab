import hashlib,json,zipfile
import pytest
from sse50_lab.release import ROOT_FILES,package_submission

def test_distribution_manifest_and_boundary(tmp_path):
    root=tmp_path/'project';root.mkdir()
    for name in ROOT_FILES:(root/name).write_text('release fixture')
    (root/'python/sse50_lab').mkdir(parents=True);(root/'python/sse50_lab/main.py').write_text('print(1)')
    (root/'work').mkdir();(root/'work/private.txt').write_text('local state')
    (root/'data').mkdir();(root/'data/raw.bin').write_bytes(b'raw')
    result=package_submission(root,tmp_path/'output')
    with zipfile.ZipFile(result['archive']) as archive:
        manifest=json.loads(archive.read('sse50-market-lab/MANIFEST.json'))
        assert set(archive.namelist())=={'sse50-market-lab/'+e['path'] for e in manifest['files']}|{'sse50-market-lab/MANIFEST.json'}
        for e in manifest['files']:assert hashlib.sha256(archive.read('sse50-market-lab/'+e['path'])).hexdigest()==e['sha256']
        assert all(e['path'].split('/')[0] in {'README.md','LICENSE','.python-version','.gitignore','pyproject.toml','uv.lock','python'} for e in manifest['files'])

def test_symlink_input_requires_regular_local_file(tmp_path):
    root=tmp_path/'project';root.mkdir()
    for name in ROOT_FILES:(root/name).write_text('fixture')
    (root/'README.md').unlink();(root/'README.md').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError,match='regular local files'):package_submission(root,tmp_path/'out')
