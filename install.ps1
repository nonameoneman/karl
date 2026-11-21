# Установка Рассела одной командой (Windows 10/11).
#   powershell -ExecutionPolicy Bypass -File install.ps1             — всё нужное для разговора
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Library    — плюс офлайн-библиотека «Канон» (~20 ГБ)
# Можно запускать повторно: уже установленное пропускается.
param(
    [string]$Model = "gemma4:e4b",
    [switch]$Library,
    [switch]$NoShortcut
)
$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = $PSScriptRoot
Set-Location $root

function Step($n, $text) { Write-Host "`n[$n/6] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  ✓ $text" -ForegroundColor Green }

# 1. uv — ставит Python 3.13 и пакеты (Python из Microsoft Store не подходит: ему не дают микрофон)
Step 1 "uv (менеджер Python)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Ok (uv --version)

# 2. Python 3.13 + зависимости в .venv
Step 2 "Python 3.13 и зависимости"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { uv venv .venv --python 3.13 }
uv pip install --python $py -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Не удалось поставить зависимости" }
Ok "зависимости установлены"

# 3. Ollama — локальная языковая модель
Step 3 "Ollama"
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $guess = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (-not (Test-Path $guess)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw "Поставьте Ollama вручную: https://ollama.com/download — и запустите установщик ещё раз"
        }
        winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
    }
    $ollama = $guess
}
try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null }
catch {
    Start-Process $ollama "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null; break } catch {}
    }
}
Ok "Ollama работает"

# 4. Модели
Step 4 "Модель $Model (несколько ГБ, первый раз — долго)"
& $ollama pull $Model
if ($Library) { & $ollama pull bge-m3 }
Ok "модели скачаны"

# 5. Голоса (Piper, 8 языков) и, по желанию, офлайн-библиотека
Step 5 "Голоса"
$env:PYTHONUTF8 = "1"
& $py -m russel.voices_get
if ($Library) { & $py -m russel.library.cli get canon }
Ok "голоса на месте (Whisper для распознавания скачается при первом запуске)"

# 6. Ярлык на рабочем столе
Step 6 "Ярлык"
if (-not $NoShortcut) {
    $ico = & $py -c "from russel.desktop import ensure_icon; print(ensure_icon())"
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut(
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Рассел.lnk"))
    $lnk.TargetPath = Join-Path $root ".venv\Scripts\pythonw.exe"
    $lnk.Arguments = "-m russel.desktop"
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = $ico
    $lnk.Save()
    Ok "ярлык «Рассел» на рабочем столе"
}

Write-Host "`nГотово. Запустите «Рассел» с рабочего стола и скажите: «Рассел, привет»." -ForegroundColor Green
