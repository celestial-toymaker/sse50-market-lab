from __future__ import annotations
import json
from pathlib import Path
import typer
from .settings import ROOT,load_config

app=typer.Typer(help='SSE 50 数据、回放、模拟交易与研究工具',no_args_is_help=True)
def context(data_dir,config):
    settings=load_config(config)
    p=Path(data_dir or settings['data_dir'])
    return (p if p.is_absolute() else ROOT/p),settings

@app.command()
def fetch(data_dir:Path|None=None,config:Path|None=None):
    from .data import fetch as download
    directory,_=context(data_dir,config);typer.echo(json.dumps(download(directory),ensure_ascii=False,indent=2))
@app.command()
def prepare(data_dir:Path|None=None,force:bool=False,config:Path|None=None):
    from .data import prepare as build
    directory,_=context(data_dir,config);result=build(directory,force);typer.echo(f"{len(result['symbols'])} 股，{result['total_events']:,} 条事件，准备完成")
@app.command()
def audit(data_dir:Path|None=None,output:Path=typer.Option(ROOT/'evidence'),config:Path|None=None):
    from .audit import run_audit
    directory,_=context(data_dir,config);result=run_audit(directory,output);typer.echo(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False,indent=2))
@app.command()
def serve(data_dir:Path|None=None,port:int=18765,config:Path|None=None):
    import uvicorn
    from .api import create_app
    directory,_=context(data_dir,config);uvicorn.run(create_app(directory,config_path=config),host='127.0.0.1',port=port)
@app.command()
def research(data_dir:Path|None=None,device:str|None=None,seed:int|None=None,output:Path=typer.Option(ROOT/'reports'),refresh:bool=False,config:Path|None=None):
    from .research import run_research
    directory,settings=context(data_dir,config)
    result=run_research(directory,output,device or settings['device'],seed if seed is not None else settings['seed'],refresh,settings)
    typer.echo(json.dumps({'status':result['status'],'backend':result['backend'],'studies':len(result['studies'])},ensure_ascii=False))
@app.command()
def reproduce(data_dir:Path|None=None,device:str|None=None,seed:int|None=None,config:Path|None=None):
    from .data import prepare as build
    from .audit import run_audit
    from .research import run_research
    directory,settings=context(data_dir,config)
    build(directory);run_audit(directory,ROOT/'evidence');run_research(directory,ROOT/'reports',device or settings['device'],seed if seed is not None else settings['seed'],config=settings)
@app.command()
def package(output:Path=typer.Option(ROOT/'dist')):
    from .release import package_submission
    typer.echo(json.dumps(package_submission(ROOT,output),ensure_ascii=False,indent=2))
if __name__=='__main__':app()
