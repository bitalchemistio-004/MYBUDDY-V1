# MBUDDY one-command setup for the PC (Windows). Run it from the repository folder:  .\setup.bat
# Safe to run again: every step checks first and skips what is already done.
#   -Cpu        speech on the CPU (no NVIDIA GPU)
#   -SkipModel  don't download the local AI model (about 2.5 GB)
#   -WithApp    also install the desktop window's packages (needs Node)
param([switch]$Cpu, [switch]$SkipModel, [switch]$WithApp)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   ok: $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   note: $text" -ForegroundColor Yellow }

Step "1/6 Python 3.10"
$py = $null
foreach ($c in @(@("py", "-3.10"), @("python"))) {
    try {
        $v = & $c[0] $c[1..9] --version 2>&1
        if ("$v" -match "Python 3\.10\.") { $py = $c; break }
    } catch { }
}
if (-not $py) { throw "Python 3.10 is needed: https://www.python.org/downloads/release/python-31011/ (tick 'Add to PATH'), then run setup again." }
Ok "$(& $py[0] $py[1..9] --version)"

Step "2/6 Python environment and packages (about 3 GB the first time)"
# A running MBUDDY holds its DLLs open, and pip then stops halfway
if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) {
    throw "MBUDDY is running (port 5000). Close it (and its window), then run setup again."
}
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py[0] $py[1..9] -m venv .venv }
$venv = ".venv\Scripts\python.exe"
& $venv -m pip install --upgrade pip --quiet
& $venv -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip couldn't install the packages (see above)." }
# kokoro-onnx asks for the CPU 'onnxruntime', which overwrites the GPU build's files: keep exactly one of them
$ortGpu = (Select-String -Path requirements.txt -Pattern "^onnxruntime-gpu==[\d.]+" | Select-Object -First 1).Matches.Value
if ($Cpu) {
    & $venv -m pip uninstall -y onnxruntime-gpu --quiet 2>$null
    & $venv -m pip install --force-reinstall --no-deps ($ortGpu -replace "onnxruntime-gpu", "onnxruntime") --quiet
} else {
    & $venv -m pip uninstall -y onnxruntime --quiet 2>$null
    & $venv -m pip install --force-reinstall --no-deps $ortGpu --quiet
}
if ($LASTEXITCODE -ne 0) { throw "couldn't set up onnxruntime (see above)." }
Ok "packages installed in .venv ($(if ($Cpu) { 'CPU' } else { 'GPU' }) speech runtime)"

Step "3/6 Settings (.env)"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Ok "made .env from .env.example (every value is optional)" }
else { Ok ".env already exists; left as it is" }
if ($Cpu) {
    $envText = Get-Content ".env" -Raw
    $envText = $envText -replace "(?m)^STT_DEVICE=.*$", "STT_DEVICE=cpu" -replace "(?m)^TTS_DEVICE=.*$", "TTS_DEVICE=cpu"
    Set-Content ".env" $envText -Encoding utf8 -NoNewline
    Ok "speech set to the CPU"
}

Step "4/6 Voice (Kokoro, about 330 MB)"
$release = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
New-Item -ItemType Directory -Force "models\kokoro" | Out-Null
foreach ($f in "kokoro-v1.0.onnx", "voices-v1.0.bin") {
    $dest = "models\kokoro\$f"
    if (Test-Path $dest) { Ok "$f already here"; continue }
    Write-Host "   downloading $f ..."
    curl.exe -L --fail --progress-bar -o "$dest.part" "$release/$f"
    if ($LASTEXITCODE -ne 0) { throw "Couldn't download $f from $release" }
    Move-Item "$dest.part" $dest
    Ok "$f"
}
Warn "speech recognition (Whisper small, about 460 MB) downloads by itself the first time MBUDDY listens"

Step "5/6 Local AI model (Ollama + qwen3:4b-instruct, about 2.5 GB)"
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    if ($SkipModel) { Warn "skipped (-SkipModel); later: ollama pull qwen3:4b-instruct" }
    else { ollama pull qwen3:4b-instruct; Ok "model ready" }
} else {
    Warn "Ollama isn't installed: get it from https://ollama.com, then run setup again (or press Download on the app's Setup page)"
}

Step "6/6 Desktop window (optional)"
if ($WithApp) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Warn "Node isn't installed: https://nodejs.org (LTS)" }
    else { Push-Location shell; npm install; Pop-Location; Ok "window packages installed (run: cd shell; npm run dev)" }
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { Warn "the window also needs Rust: https://rustup.rs" }
} else { Warn "skipped; add -WithApp to set up the desktop window too (needs Node and Rust)" }

Step "Check"
& $venv check_system.py
Write-Host "`nDone. Start the brain with:  .venv\Scripts\python.exe main.py   (or run.bat)" -ForegroundColor Green
Write-Host "Then set your name in the app's Settings. Nobody's name is built in." -ForegroundColor Green
Write-Host "Optional: let MBUDDY learn in Settings, General, Learning memory (your Obsidian vault or a new one)." -ForegroundColor Green
