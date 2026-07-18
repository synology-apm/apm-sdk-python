param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$WheelPath
)

$ErrorActionPreference = "Stop"

$VENV = ".venv-build"
$ENTRY = "freeze_entry.py"
$DIST_DIR = "dist/binaries/windows"

function Cleanup {
    if (Test-Path $VENV) {
        Remove-Item -Recurse -Force $VENV
    }
    if (Test-Path $ENTRY) {
        Remove-Item -Force $ENTRY
    }
    if ($script:SPEC_FILE -and (Test-Path $script:SPEC_FILE)) {
        Remove-Item -Force $script:SPEC_FILE
    }
    if (Test-Path "build") {
        Remove-Item -Recurse -Force "build"
    }
}

try {
    if (-not (Test-Path $WheelPath)) {
        throw "Wheel not found: $WheelPath"
    }

    $WheelFile = Split-Path $WheelPath -Leaf

    if ($WheelFile -notmatch '^[^-]+-(?<version>[^-]+)-') {
        throw "Failed to parse version from wheel filename: $WheelFile"
    }

    $Version = $Matches.version

    switch ($env:PROCESSOR_ARCHITECTURE) {
        "AMD64" { $Arch = "x86_64" }
        "ARM64" { $Arch = "arm64" }
        default { $Arch = $env:PROCESSOR_ARCHITECTURE.ToLower() }
    }

    $APP_NAME = "synology-apm-cli-$Version-windows-$Arch"
    $APP_DIR = Join-Path $DIST_DIR $APP_NAME
    $ZIP_PATH = Join-Path $DIST_DIR "$APP_NAME.zip"
    $script:SPEC_FILE = "$APP_NAME.spec"

    # synology-apm-cli depends on synology-apm-sdk; point pip at the sibling wheel directory so its
    # resolver picks up the matching synology-apm-sdk==X.Y.Z wheel (expects the
    # dist/synology-apm-sdk/ + dist/synology-apm-cli/ layout produced by `make build`).
    $SdkWheelDir = Join-Path (Split-Path (Split-Path $WheelPath -Parent) -Parent) "synology-apm-sdk"

    New-Item -ItemType Directory -Force -Path $DIST_DIR | Out-Null

    py -3 -m venv $VENV

    $PYTHON = Join-Path $VENV "Scripts\python.exe"
    $PYINSTALLER = Join-Path $VENV "Scripts\pyinstaller.exe"

    & $PYTHON -m pip install --upgrade pip
    & $PYTHON -m pip install pyinstaller
    & $PYTHON -m pip install --find-links $SdkWheelDir "$WheelPath"

@'
from synology_apm.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
'@ | Set-Content -Path $ENTRY -Encoding UTF8

    & $PYINSTALLER `
        --noconfirm `
        --onedir `
        --clean `
        --name $APP_NAME `
        --distpath $DIST_DIR `
        --collect-all synology_apm `
        $ENTRY

    if (Test-Path $ZIP_PATH) {
        Remove-Item -Force $ZIP_PATH
    }

    Compress-Archive -Path $APP_DIR -DestinationPath $ZIP_PATH -Force

    if (Test-Path $APP_DIR) {
        Remove-Item -Recurse -Force $APP_DIR
    }

    Write-Host "Built: $ZIP_PATH"
}
finally {
    Cleanup
}
