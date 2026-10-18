import numpy as np
import pytest
from sse50_lab.compute import ComputeBackend

def test_cpu_regression_and_prediction():
    rng=np.random.default_rng(7);x=rng.normal(size=(300,8)).astype(np.float32);y=x@np.arange(8,dtype=np.float32)
    b=ComputeBackend('cpu');w=b.ridge(x,y,1);expected=np.linalg.solve(x.T@x+np.eye(8,dtype=np.float32),x.T@y)
    np.testing.assert_allclose(w,expected,rtol=1e-4,atol=1e-5)

def test_accelerator_failure_retries_atomically_on_cpu():
    b=ComputeBackend('cpu');b.status.effective_device='mps';calls=[];published=[]
    def op(device):
        calls.append(device)
        if device=='mps':raise RuntimeError('MPS device unavailable during batch')
        return np.arange(4,dtype=np.float32)
    published.append(b.run(op));assert calls==['mps','cpu'] and len(published)==1 and b.status.effective_device=='cpu'

def test_program_error_remains_visible():
    b=ComputeBackend('cpu')
    with pytest.raises(RuntimeError,match='shape mismatch'):b.run(lambda d:(_ for _ in ()).throw(RuntimeError('shape mismatch')))

@pytest.mark.mps
def test_mps_cpu_fixed_weight_agreement():
    import torch
    if not torch.backends.mps.is_available():pytest.skip('MPS硬件验证需在Apple Silicon执行')
    rng=np.random.default_rng(11);x=rng.normal(size=(1024,12)).astype(np.float32);w=rng.normal(size=12).astype(np.float32)
    cpu=ComputeBackend('cpu');mps=ComputeBackend('mps');assert mps.status.effective_device=='mps'
    np.testing.assert_allclose(mps.predict(x,w),cpu.predict(x,w),rtol=1e-4,atol=1e-5)
    np.testing.assert_allclose(mps.bootstrap(np.arange(30,dtype=np.float32)),cpu.bootstrap(np.arange(30,dtype=np.float32)),rtol=1e-4,atol=1e-5)


def test_mps_program_errors_keep_their_original_state():
    backend=ComputeBackend('cpu');backend.status.effective_device='mps'
    with pytest.raises(RuntimeError,match='shape mismatch'):
        backend.run(lambda device:(_ for _ in ()).throw(RuntimeError('MPS tensor shape mismatch')))
    assert backend.status.effective_device=='mps' and backend.status.fallback_reason is None
    with pytest.raises(NotImplementedError,match='strategy'):
        backend.run(lambda device:(_ for _ in ()).throw(NotImplementedError('strategy feature')))
