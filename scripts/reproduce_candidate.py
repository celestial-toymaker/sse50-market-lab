"""Build and run an extracted candidate in an independent directory."""
from __future__ import annotations
import argparse,hashlib,json,os,platform,shutil,subprocess,time,zipfile
from pathlib import Path

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('destination',type=Path);p.add_argument('--data-archive',type=Path);p.add_argument('--cache',type=Path);args=p.parse_args()
    destination=args.destination.resolve();destination.mkdir(parents=True,exist_ok=True)
    root=destination/'sse50-market-lab'
    if root.exists():raise ValueError('Choose a fresh destination for candidate verification')
    with zipfile.ZipFile(args.archive) as z:
        for name in z.namelist():
            target=(destination/name).resolve();target.relative_to(destination)
        z.extractall(destination)
    manifest=json.loads((root/'MANIFEST.json').read_text())
    for e in manifest['files']:
        if sha(root/e['path'])!=e['sha256']:raise ValueError('Candidate content hash mismatch: '+e['path'])
    work=root/'work';work.mkdir();env=os.environ.copy();env.pop('CONDA_PREFIX',None);env.pop('VIRTUAL_ENV',None)
    cache=(args.cache.resolve() if args.cache else work/'dependency-cache');env['UV_CACHE_DIR']=str(cache/'uv');env['CARGO_HOME']=str(cache/'cargo');env['MPLCONFIGDIR']=str(work/'matplotlib');env['CARGO_TARGET_DIR']=str(work/'native-target')
    receipt={'archive_sha256':sha(args.archive),'source_fingerprint':manifest['source_fingerprint'],'manifest_files_verified':len(manifest['files']),'platform':platform.platform(),'steps':[],'cuda_validation':'未测试'}
    def run(name,command):
        began=time.perf_counter();log=work/(name+'.log')
        with log.open('w') as f:result=subprocess.run(command,cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT)
        receipt['steps'].append({'name':name,'command':command,'exit_code':result.returncode,'seconds':time.perf_counter()-began,'log_sha256':sha(log)})
        (destination/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
        if result.returncode:raise RuntimeError(f'{name}: inspect {log}')
    run('fresh-environment',['uv','sync','--frozen'])
    python=str(root/'.venv/bin/python');env['PYO3_PYTHON']=python
    # uv sync compiles the local PyO3 project into this fresh environment.
    if args.data_archive:
        (root/'data').mkdir();shutil.copy2(args.data_archive,root/'data/sse50_20260923.zip')
    expected=json.loads((root/'reports/results.json').read_text())
    run('tests',[python,'-m','pytest','-q'])
    run('prepare',[python,'-m','sse50_lab.cli','prepare'])
    run('audit',[python,'-m','sse50_lab.cli','audit'])
    run('research',[python,'-m','sse50_lab.cli','research','--device','mps','--refresh'])
    actual=json.loads((root/'reports/results.json').read_text());audit=json.loads((root/'evidence/audit.json').read_text())
    keys=['studies','execution','execution_sensitivity','models'];receipt['results_equal']={k:expected[k]==actual[k] for k in keys};receipt['actual_backend']=actual['backend'];receipt['audit']={k:audit[k] for k in ['symbols','total_events','structural_pass','closing_depth_pass','opening_pass','closing_pass','volume_pass','after_volume_pass','same_second_rate']}
    receipt['status']='passed' if all(receipt['results_equal'].values()) and audit['structural_pass'] and all(audit[k]==50 for k in ['closing_depth_pass','opening_pass','closing_pass','volume_pass','after_volume_pass']) else 'review'
    receipt['research_seconds']=actual['run']['seconds'];receipt['research_peak_rss_bytes']=actual['run']['peak_rss_bytes']
    (destination/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps(receipt,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
