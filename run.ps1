# Лаунчер Рассела (консольный, для отладки; обычный запуск — ярлык «Рассел»): перезапускает процесс, когда Рассел обновил свой код (код выхода 3).
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "Russel"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"          # создаёт install.ps1
if (-not (Test-Path $py)) { $py = Join-Path $PSScriptRoot "..\venv313\Scripts\python.exe" }
$env:PYTHONUTF8 = "1"
$first = $true
while ($true) {
    if ($first) { & $py -m russel @args } else { & $py -m russel --no-browser @args }
    $code = $LASTEXITCODE
    $first = $false
    if ($code -ne 3) { Write-Host "Рассел остановлен (код $code)"; break }
    Write-Host "Ill be back. Рассел перезапускается после самообновления..." -ForegroundColor Cyan
    Start-Sleep -Seconds 1
}

