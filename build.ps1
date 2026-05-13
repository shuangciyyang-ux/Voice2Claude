# Voice2Claude - build script
# Produces dist\Voice2Claude.exe — a standalone single-file executable
# that bundles Python + all dependencies + your .env.
#
# Run from the project root with venv activated:
#   .\venv\Scripts\Activate.ps1
#   .\build.ps1
#
# The first build takes several minutes. Subsequent builds are faster.

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

Write-Host "Project directory: $projectDir" -ForegroundColor Cyan

# ----- 1. Sanity checks -----
if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "venv not found. Create one first:" -ForegroundColor Red
    Write-Host "  python -m venv venv" -ForegroundColor Red
    Write-Host "  .\venv\Scripts\Activate.ps1" -ForegroundColor Red
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".\.env")) {
    Write-Host ".env not found. Cannot bundle without it." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".\index.html")) {
    Write-Host "index.html not found in project root." -ForegroundColor Red
    exit 1
}

# ----- 2. Make sure PyInstaller is installed in this venv -----
Write-Host "Checking PyInstaller..." -ForegroundColor Cyan
$piCheck = & .\venv\Scripts\python.exe -c "import PyInstaller" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..." -ForegroundColor Cyan
    & .\venv\Scripts\python.exe -m pip install pyinstaller
}

# Optional: place a 256x256 static\app.ico to give the .exe a custom icon.
# The PyInstaller spec picks it up automatically if present.

# ----- 3. Clean previous build -----
# Preserve user files in dist (.env, data\, anything not the prior exe) so
# rebuilds don't wipe API keys or saved conversations.
if (Test-Path ".\build") { Remove-Item -Recurse -Force ".\build" }
if (Test-Path ".\dist") {
    $preserved = @()
    foreach ($name in @(".env", "data")) {
        $p = Join-Path ".\dist" $name
        if (Test-Path $p) { $preserved += $name }
    }
    Get-ChildItem -Force ".\dist" | Where-Object { $preserved -notcontains $_.Name } |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
    if ($preserved.Count -gt 0) {
        Write-Host "Preserved across rebuild: $($preserved -join ', ')" -ForegroundColor Yellow
    }
}

# ----- 4. Run PyInstaller -----
Write-Host "Building... (this takes 2-5 minutes the first time)" -ForegroundColor Cyan
& .\venv\Scripts\python.exe -m PyInstaller --clean Voice2Claude.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed." -ForegroundColor Red
    exit 1
}

# ----- 5. Done -----
$exePath = Join-Path $projectDir "dist\Voice2Claude.exe"
if (Test-Path $exePath) {
    $sizeMb = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Build complete." -ForegroundColor Green
    Write-Host "  $exePath ($sizeMb MB)" -ForegroundColor Green
    Write-Host ""
    Write-Host "Your .env (with API keys) is baked into this .exe." -ForegroundColor Yellow
    Write-Host "Anyone who can run the .exe can also extract those keys with simple tools." -ForegroundColor Yellow
    Write-Host "Set monthly spend caps on your API accounts before sharing it." -ForegroundColor Yellow
} else {
    Write-Host "Build appeared to succeed but $exePath was not produced." -ForegroundColor Red
    exit 1
}
