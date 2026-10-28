param(
    [Parameter(Mandatory = $true)]
    [string]$FfmpegPath
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$ffmpeg = Resolve-Path -LiteralPath $FfmpegPath
if ((Split-Path -Leaf $ffmpeg.Path) -ne "ffmpeg.exe") {
    throw "FfmpegPath debe apuntar al archivo ffmpeg.exe."
}

Push-Location $root
try {
    py -m venv .build-venv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno virtual de compilación." }
    & .\.build-venv\Scripts\python.exe -m pip install --upgrade pip pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar PyInstaller." }
    & .\.build-venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile `
        --name Sonora --add-data "static;static" app.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller no pudo crear Sonora.exe." }
    Copy-Item -LiteralPath $ffmpeg.Path -Destination .\dist\ffmpeg.exe -Force
    Get-ChildItem -LiteralPath (Split-Path -Parent $ffmpeg.Path) -Filter "*.dll" -File |
        Copy-Item -Destination .\dist -Force
    Write-Host "Versión portátil creada en $root\dist"
    Write-Host "Distribuye Sonora.exe y ffmpeg.exe juntos; no hace falta ejecutar instalador."
}
finally {
    Pop-Location
}
