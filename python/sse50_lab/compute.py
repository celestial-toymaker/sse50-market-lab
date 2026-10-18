"""Accelerated pure batch calculations with an atomic CPU retry boundary."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import platform
import numpy as np
import torch

@dataclass
class BackendStatus:
    requested_device: str
    effective_device: str
    dtype: str = 'float32'
    fallback_reason: str | None = None
    cuda_validation: str = '未测试'

class ComputeBackend:
    def __init__(self,device: str='auto',seed: int=20260923):
        if device not in {'auto','mps','cuda','cpu'}: raise ValueError('device 可选 auto、mps、cuda 或 cpu')
        requested=device
        if device=='auto':
            if platform.system()=='Darwin' and platform.machine()=='arm64': device='mps'
            else: device='cuda' if torch.cuda.is_available() else 'cpu'
        self.status=BackendStatus(requested,device); self.seed=seed
        try:
            if device=='mps' and not torch.backends.mps.is_available(): raise RuntimeError('MPS device unavailable')
            if device=='cuda' and not torch.cuda.is_available(): raise RuntimeError('CUDA device unavailable')
            torch.ones(1,device=device,dtype=torch.float32)
        except (RuntimeError,NotImplementedError,AssertionError) as exc:
            if not self._device_error(exc): raise
            self._fallback(exc)
    @staticmethod
    def _device_error(exc):
        message=str(exc).lower()
        device_context=any(x in message for x in ['mps','cuda','device','nvidia','accelerator'])
        failure=any(x in message for x in ['unavailable','out of memory','not implemented','not currently implemented','not compiled with','not linked with support','initialization error','initialization failed','cannot initialize','driver version is insufficient','no nvidia driver','invalid device ordinal'])
        unsupported_operator='could not run' in message and 'backend' in message
        return device_context and (failure or unsupported_operator)
    def _fallback(self,exc):
        self.status.fallback_reason=f'{type(exc).__name__}: {exc}'
        self.status.effective_device='cpu'
    def run(self,operation):
        """An operation receives a device and returns host-owned results before publication."""
        try:
            return operation(self.status.effective_device)
        except (RuntimeError,NotImplementedError,AssertionError) as exc:
            if self.status.effective_device=='cpu' or not self._device_error(exc): raise
            self._fallback(exc)
        # Leave the exception scope to release the failed operation's traceback and tensors.
        if self.status.requested_device=='mps' or (platform.system()=='Darwin' and torch.backends.mps.is_available()):
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()
        return operation('cpu')
    def describe(self): return asdict(self.status)|{'torch':torch.__version__,'seed':self.seed}
    def ridge(self,x,y,alpha=1.0):
        x=np.asarray(x,dtype=np.float32); y=np.asarray(y,dtype=np.float32)
        def operation(device):
            a=torch.as_tensor(x,device=device); b=torch.as_tensor(y,device=device)
            gram=a.T@a+alpha*torch.eye(a.shape[1],device=device)
            # Conjugate gradients keep positive-definite solves on the selected accelerator.
            rhs=a.T@b; w=torch.zeros_like(rhs); r=rhs.clone(); p=r.clone(); rr=(r*r).sum()
            for _ in range(max(32,a.shape[1]*4)):
                gp=gram@p; scale=rr/(p*gp).sum().clamp_min(1e-20);w=w+scale*p;r=r-scale*gp
                nr=(r*r).sum()
                if float(nr.cpu())<1e-12: break
                p=r+(nr/rr.clamp_min(1e-20))*p;rr=nr
            return w.detach().cpu().numpy()
        return self.run(operation)
    def predict(self,x,w):
        return self.run(lambda d:(torch.as_tensor(np.asarray(x,dtype=np.float32),device=d)@torch.as_tensor(np.asarray(w,dtype=np.float32),device=d)).cpu().numpy())
    def bootstrap(self,block_values,repetitions=1000):
        values=np.asarray(block_values,dtype=np.float32)
        if len(values)==0: return [None,None]
        rng=np.random.default_rng(self.seed); indices=rng.integers(0,len(values),size=(repetitions,len(values)))
        def operation(d):
            a=torch.as_tensor(values,device=d); chunks=[]
            for start in range(0,repetitions,100):
                idx=torch.as_tensor(indices[start:start+100],device=d)
                chunks.append(a[idx].mean(dim=1).cpu().numpy())
            return np.quantile(np.concatenate(chunks),[0.025,0.975]).tolist()
        return self.run(operation)
