# Лаунчер Карла (консольный, для отладки; обычный запуск — ярлык «Карл»): перезапускает процесс, когда Карл обновил свой код (код выхода 3).
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "Karl"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"          # создаёт install.ps1
if (-not (Test-Path $py)) { $py = Join-Path $PSScriptRoot "..\venv313\Scripts\python.exe" }
$env:PYTHONUTF8 = "1"
$first = $true
while ($true) {
    if ($first) { & $py -m jarvis @args } else { & $py -m jarvis --no-browser @args }
    $code = $LASTEXITCODE
    $first = $false
    if ($code -ne 3) { Write-Host "Карл остановлен (код $code)"; break }
    Write-Host "Ill be back. Карл перезапускается после самообновления..." -ForegroundColor Cyan
    Start-Sleep -Seconds 1
}

