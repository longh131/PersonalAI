$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 Python。请先安装 Python 3.11+。"
    exit 1
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[1/3] 创建虚拟环境 .venv ..."
    python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
Write-Host "[2/3] 安装依赖..."
python -m pip install -U pip
python -m pip install -r requirements.txt
Write-Host "[3/3] 进入待机"
python main.py @args
