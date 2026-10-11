param([ValidateRange(1024, 65535)][int]$Port = 8787)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYDANTIC_AI_NO_BANNER = '1'
$env:CLUETIDE_PREVIEW_ONLY = '1'
$env:CLUETIDE_DATA_DIR = Join-Path $ProjectRoot 'local-data'
& "$ProjectRoot\.venv\Scripts\python.exe" -X utf8 -m uvicorn cluetide.release_server:app --host 127.0.0.1 --port $Port --no-access-log
