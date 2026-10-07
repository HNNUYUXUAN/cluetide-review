param([int]$Port = 5186)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$env:PYDANTIC_AI_NO_BANNER = '1'
$env:CLUETIDE_PREVIEW_ONLY = '1'
& "$ProjectRoot\.venv\Scripts\python.exe" -X utf8 -m uvicorn cluetide.app:app --host 127.0.0.1 --port $Port --no-access-log
