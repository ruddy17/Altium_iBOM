[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$InstallRoot
)

$ErrorActionPreference = 'Stop'

$pluginSource = $PSScriptRoot
$repoRoot = Split-Path -Parent $pluginSource
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $pluginSource 'installed'
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$converter = Join-Path $repoRoot 'altium_to_ibom.py'

if (-not (Test-Path -LiteralPath $converter -PathType Leaf)) {
    throw "Converter not found: $converter"
}

if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $repoRoot '.venv\Scripts\python.exe'),
        (Join-Path (Split-Path -Parent $repoRoot) '.venv\Scripts\python.exe')
    )
    $PythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

if (-not $PythonPath) {
    throw 'Python was not found. Create .venv or pass -PythonPath C:\path\to\python.exe.'
}

$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$converter = (Resolve-Path -LiteralPath $converter).Path

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $pluginSource 'Altium_iBOM.PrjScr') -Destination $InstallRoot -Force
Copy-Item -LiteralPath (Join-Path $pluginSource 'generate_ibom.cmd') -Destination $InstallRoot -Force

$launcher = Join-Path $InstallRoot 'generate_ibom.cmd'
$pasSource = Get-Content -LiteralPath (Join-Path $pluginSource 'Altium_iBOM.pas') -Raw
$pasSource = $pasSource.Replace('__ALTIUM_IBOM_LAUNCHER__', $launcher.Replace("'", "''"))
Set-Content -LiteralPath (Join-Path $InstallRoot 'Altium_iBOM.pas') -Value $pasSource -Encoding Ascii

$config = @(
    '@echo off',
    ('set "ALTIUM_IBOM_PYTHON={0}"' -f $PythonPath),
    ('set "ALTIUM_IBOM_CONVERTER={0}"' -f $converter)
)
Set-Content -LiteralPath (Join-Path $InstallRoot 'config.cmd') -Value $config -Encoding Ascii

Write-Host 'Altium iBOM installed.'
Write-Host "Global project: $(Join-Path $InstallRoot 'Altium_iBOM.PrjScr')"
Write-Host 'Altium procedure: GenerateInteractiveBom'
