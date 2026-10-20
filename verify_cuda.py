import json
from pathlib import Path
import sys
import torch
import torchvision

assert torch.cuda.is_available(), 'CUDA is unavailable'
device = torch.device('cuda:0')
torch.manual_seed(42)
x = torch.randn(256, 256, device=device, requires_grad=True)
y = x @ x.T
loss = y.square().mean()
loss.backward()
assert torch.isfinite(y).all() and torch.isfinite(x.grad).all()
torch.testing.assert_close(y.detach().cpu(), x.detach().cpu() @ x.detach().cpu().T, rtol=1e-4, atol=1e-4)
conv = torch.nn.Conv2d(3, 8, 3).to(device)
image = torch.randn(2, 3, 32, 32, device=device)
conv(image).mean().backward()
boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 9., 9.]], device=device)
scores = torch.tensor([0.9, 0.8], device=device)
assert torchvision.ops.nms(boxes, scores, 0.5).tolist() == [0]
torch.cuda.synchronize()
result = {
    'python': sys.version.split()[0],
    'python_executable': sys.executable,
    'torch': torch.__version__,
    'torchvision': torchvision.__version__,
    'cuda_runtime': torch.version.cuda,
    'cudnn': torch.backends.cudnn.version(),
    'gpu': torch.cuda.get_device_name(0),
    'vram_gb': round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2),
    'compute_capability': torch.cuda.get_device_capability(0),
    'compiled_architectures': torch.cuda.get_arch_list(),
    'checks': ['GPU matrix multiply matches CPU', 'autograd', 'cuDNN convolution', 'torchvision CUDA NMS'],
    'status': 'PASS',
}
Path('cuda-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
