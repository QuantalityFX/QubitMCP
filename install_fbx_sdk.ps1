[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceDir,
    [string]$PythonExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-PythonExe {
    param([string]$Preferred)

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Preferred)) {
        $candidates += $Preferred
    }

    $repoRoot = Split-Path -Path $PSCommandPath -Parent
    $candidates += (Join-Path $repoRoot ".venv\Scripts\python.exe")
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates += (Join-Path $env:LOCALAPPDATA "QubitMCP\.venv\Scripts\python.exe")
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function Get-PythonLayout {
    param([string]$PythonPath)

    $probe = @'
import json
import os
import struct
import sys
import sysconfig

paths = sysconfig.get_paths()
site = paths.get("purelib") or paths.get("platlib") or ""
scripts = os.path.join(sys.prefix, "Scripts")
out = {
    "executable": sys.executable,
    "version": sys.version.split()[0],
    "bits": struct.calcsize("P") * 8,
    "site_packages": site,
    "scripts_dir": scripts,
}
print(json.dumps(out))
'@

    $raw = $probe | & $PythonPath - 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to probe Python layout.`n$($raw | Out-String)"
    }

    $jsonText = ($raw | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($jsonText)) {
        throw "Python layout probe returned empty output."
    }

    return ($jsonText | ConvertFrom-Json)
}

function Find-SourceFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    return Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Name -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Find-SourcePatternFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Pattern
    )

    return Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like $Pattern } |
        Select-Object -First 1
}

try {
    if (-not (Test-Path -LiteralPath $SourceDir)) {
        throw "SourceDir does not exist: $SourceDir"
    }

    $resolvedPython = Resolve-PythonExe -Preferred $PythonExe
    if (-not $resolvedPython) {
        throw "Could not resolve target Python runtime. Pass -PythonExe explicitly."
    }

    $layout = Get-PythonLayout -PythonPath $resolvedPython
    if ([string]::IsNullOrWhiteSpace($layout.site_packages) -or [string]::IsNullOrWhiteSpace($layout.scripts_dir)) {
        throw "Could not resolve site-packages/scripts directory for runtime: $resolvedPython"
    }

    $sourceRoot = (Resolve-Path -LiteralPath $SourceDir).Path
    $fbxPyd = Find-SourcePatternFile -Root $sourceRoot -Pattern "fbx*.pyd"
    $fbxWheel = Find-SourcePatternFile -Root $sourceRoot -Pattern "fbx-*.whl"
    $fbxCommon = Find-SourceFile -Root $sourceRoot -Name "FbxCommon.py"
    $libFbxDll = Find-SourceFile -Root $sourceRoot -Name "libfbxsdk.dll"

    $missing = @()
    if (-not $fbxPyd -and -not $fbxWheel) { $missing += "fbx binding (fbx*.pyd or fbx-*.whl)" }
    if (-not $fbxCommon) { $missing += "FbxCommon.py" }
    if ($missing.Count -gt 0) {
        throw "SourceDir is missing required file(s): $($missing -join ', ')"
    }

    if (-not (Test-Path -LiteralPath $layout.site_packages)) {
        New-Item -ItemType Directory -Path $layout.site_packages -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $layout.scripts_dir)) {
        New-Item -ItemType Directory -Path $layout.scripts_dir -Force | Out-Null
    }

    $destFbxCommon = Join-Path $layout.site_packages "FbxCommon.py"
    $destFbxModule = ""
    $destDll = ""

    if ($fbxWheel) {
        & $resolvedPython -m pip install --force-reinstall $fbxWheel.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install FBX wheel: $($fbxWheel.FullName)"
        }
    } elseif ($fbxPyd) {
        $destFbxModule = Join-Path $layout.site_packages $fbxPyd.Name
        Copy-Item -LiteralPath $fbxPyd.FullName -Destination $destFbxModule -Force
    }

    Copy-Item -LiteralPath $fbxCommon.FullName -Destination $destFbxCommon -Force
    if ($libFbxDll) {
        $destDll = Join-Path $layout.scripts_dir "libfbxsdk.dll"
        Copy-Item -LiteralPath $libFbxDll.FullName -Destination $destDll -Force
    }

    $unblockTargets = @()
    if ($destFbxModule) { $unblockTargets += $destFbxModule }
    if ($destFbxCommon) { $unblockTargets += $destFbxCommon }
    if ($destDll) { $unblockTargets += $destDll }
    foreach ($path in $unblockTargets) {
        try {
            Unblock-File -LiteralPath $path -ErrorAction Stop
        } catch {
            # Best effort only.
        }
    }

    Write-Output "[fbx-install] Installed Autodesk FBX runtime files."
    Write-Output "[fbx-install] Python: $($layout.version) ($($layout.bits)-bit)"
    Write-Output "[fbx-install] Executable: $($layout.executable)"
    Write-Output "[fbx-install] Installed:"
    if ($fbxWheel) {
        Write-Output "  - wheel installed: $($fbxWheel.FullName)"
    } elseif ($destFbxModule) {
        Write-Output "  - $destFbxModule"
    }
    Write-Output "  - $destFbxCommon"
    if ($destDll) {
        Write-Output "  - $destDll"
    } else {
        Write-Output "  - libfbxsdk.dll not found in source (optional for some Autodesk wheel builds)"
    }
    Write-Output "[fbx-install] Reminder: review Autodesk FBX SDK license terms for redistribution/commercial packaging."

    $checkScript = Join-Path (Split-Path -Path $PSCommandPath -Parent) "check_fbx_sdk.ps1"
    if (Test-Path -LiteralPath $checkScript) {
        & $checkScript -PythonExe $resolvedPython
        exit $LASTEXITCODE
    }

    exit 0
} catch {
    Write-Output "[fbx-install] ERROR: $($_.Exception.Message)"
    exit 1
}
