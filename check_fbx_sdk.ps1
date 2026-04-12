[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [switch]$Json,
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Check {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Output $Message
    }
}

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

$resolvedPython = Resolve-PythonExe -Preferred $PythonExe
if (-not $resolvedPython) {
    Write-Error "[fbx-check] ERROR: Python runtime not found. Pass -PythonExe or run setup first."
    exit 2
}

$probe = @'
import ctypes
import importlib.util
import json
import os
import struct
import sys
import sysconfig

def mod_info(name):
    spec = importlib.util.find_spec(name)
    if spec is None:
        return {"found": False, "origin": ""}
    return {"found": True, "origin": str(spec.origin or "")}

paths = sysconfig.get_paths()
site_packages = paths.get("purelib") or paths.get("platlib") or ""
scripts_dir = os.path.join(sys.prefix, "Scripts")

expected = {
    "fbx_pyd": os.path.join(site_packages, "fbx.pyd") if site_packages else "",
    "fbxcommon_py": os.path.join(site_packages, "FbxCommon.py") if site_packages else "",
    "libfbxsdk_dll": os.path.join(scripts_dir, "libfbxsdk.dll") if scripts_dir else "",
}
exists = {k: (os.path.exists(v) if v else False) for k, v in expected.items()}

dll = {"loadable": False, "error": ""}
for candidate in ("libfbxsdk.dll", expected["libfbxsdk_dll"]):
    if not candidate:
        continue
    try:
        ctypes.CDLL(candidate)
        dll["loadable"] = True
        dll["error"] = ""
        break
    except Exception as exc:
        dll["error"] = str(exc)

module_fbx = mod_info("fbx")
module_fbxcommon = mod_info("FbxCommon")

fbx_runtime = {"import_ok": False, "manager_ok": False, "error": ""}
try:
    import fbx  # type: ignore
    fbx_runtime["import_ok"] = True
    if hasattr(fbx, "FbxManager"):
        mgr = None
        try:
            mgr = fbx.FbxManager.Create()
            fbx_runtime["manager_ok"] = bool(mgr is not None)
        finally:
            if mgr is not None:
                try:
                    mgr.Destroy()
                except Exception:
                    pass
    else:
        fbx_runtime["manager_ok"] = True
except Exception as exc:
    fbx_runtime["error"] = str(exc)

fbxcommon_import = {"import_ok": False, "error": ""}
if module_fbxcommon["found"]:
    try:
        import FbxCommon  # type: ignore
        _ = FbxCommon
        fbxcommon_import["import_ok"] = True
    except Exception as exc:
        fbxcommon_import["error"] = str(exc)

ok = bool(fbx_runtime["import_ok"] and fbx_runtime["manager_ok"])

out = {
    "ok": ok,
    "python_executable": sys.executable,
    "python_version": sys.version.split()[0],
    "python_bits": struct.calcsize("P") * 8,
    "site_packages": site_packages,
    "scripts_dir": scripts_dir,
    "expected": expected,
    "exists": exists,
    "module_fbx": module_fbx,
    "module_fbxcommon": module_fbxcommon,
    "fbx_runtime": fbx_runtime,
    "fbxcommon_import": fbxcommon_import,
    "dll": dll,
}

print(json.dumps(out))
'@

$raw = $probe | & $resolvedPython - 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[fbx-check] ERROR: Probe failed in $resolvedPython`n$($raw | Out-String)"
    exit 3
}

$jsonText = ($raw | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($jsonText)) {
    Write-Error "[fbx-check] ERROR: Probe returned empty output."
    exit 3
}

try {
    $result = $jsonText | ConvertFrom-Json
} catch {
    Write-Error "[fbx-check] ERROR: Could not parse probe output as JSON.`n$jsonText"
    exit 3
}

if ($Json) {
    $result | ConvertTo-Json -Depth 6
}

Write-Check "[fbx-check] Python: $($result.python_version) ($($result.python_bits)-bit)"
Write-Check "[fbx-check] Executable: $($result.python_executable)"
Write-Check "[fbx-check] Site-packages: $($result.site_packages)"
Write-Check "[fbx-check] Scripts dir: $($result.scripts_dir)"
Write-Check "[fbx-check] fbx module found: $($result.module_fbx.found)"
if ($result.module_fbx.origin) {
    Write-Check "[fbx-check] fbx module origin: $($result.module_fbx.origin)"
}
Write-Check "[fbx-check] fbx runtime import: $($result.fbx_runtime.import_ok)"
Write-Check "[fbx-check] fbx manager create: $($result.fbx_runtime.manager_ok)"
if ($result.fbx_runtime.error) {
    Write-Check "[fbx-check] fbx runtime error: $($result.fbx_runtime.error)"
}
Write-Check "[fbx-check] FbxCommon module found: $($result.module_fbxcommon.found)"
if ($result.module_fbxcommon.origin) {
    Write-Check "[fbx-check] FbxCommon origin: $($result.module_fbxcommon.origin)"
}
if ($result.module_fbxcommon.found) {
    Write-Check "[fbx-check] FbxCommon import: $($result.fbxcommon_import.import_ok)"
    if ($result.fbxcommon_import.error) {
        Write-Check "[fbx-check] FbxCommon import error: $($result.fbxcommon_import.error)"
    }
}
Write-Check "[fbx-check] libfbxsdk.dll loadable: $($result.dll.loadable)"

if ($result.ok) {
    Write-Check "[fbx-check] PASS: Autodesk FBX SDK runtime is ready."
    if (-not $result.module_fbxcommon.found) {
        Write-Check "[fbx-check] NOTE: FbxCommon.py not found. Core FBX runtime is still usable."
    }
    exit 0
}

Write-Check "[fbx-check] FAIL: Autodesk FBX SDK runtime is not ready."

if (-not $result.fbx_runtime.import_ok) {
    Write-Check "[fbx-check] Missing/invalid runtime: fbx"
}
if (-not $result.module_fbxcommon.found) {
    Write-Check "[fbx-check] Missing helper module: FbxCommon (optional but recommended)"
}
if (-not $result.dll.loadable) {
    Write-Check "[fbx-check] DLL load note: $($result.dll.error)"
}

Write-Check "[fbx-check] Runtime targets:"
Write-Check "  - Site-packages: $($result.site_packages)"
Write-Check "  - Scripts dir:   $($result.scripts_dir)"
Write-Check "[fbx-check] Supported source layouts:"
Write-Check "  - fbx-*.whl + FbxCommon.py"
Write-Check "  - fbx*.pyd (+ optional libfbxsdk.dll) + FbxCommon.py"
Write-Check "[fbx-check] Install helper: .\install_fbx_sdk.ps1 -SourceDir <path_to_fbx_sdk_folder> -PythonExe `"$resolvedPython`""
exit 1
