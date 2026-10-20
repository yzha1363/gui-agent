$projectDirectory = $PSScriptRoot
$env:VIRTUAL_ENV = Join-Path $projectDirectory '.venv'
$env:PATH = (Join-Path $env:VIRTUAL_ENV 'Scripts') + ';' + $env:PATH
Write-Host "CUDA Python environment: $env:VIRTUAL_ENV"
python -c "import torch; print('PyTorch:', torch.__version__, 'CUDA:', torch.version.cuda, 'Available:', torch.cuda.is_available())"
