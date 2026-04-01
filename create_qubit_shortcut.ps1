[CmdletBinding()]
param(
    [ValidateSet("Desktop")]
    [string]$Scope = "Desktop"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$appUserModelId = "QuantalityFX.EchoGraph"
$repoRoot = Split-Path -Path $PSCommandPath -Parent
$constantsPath = Join-Path $repoRoot "echograph\constants.py"

function Get-SafeShortcutNameFromAppTitle {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConstantsFile,
        [Parameter(Mandatory = $true)]
        [string]$FallbackName
    )

    if (-not (Test-Path -LiteralPath $ConstantsFile)) {
        return $FallbackName
    }

    $pattern = '^\s*APP_TITLE\s*=\s*["''](.+?)["'']\s*$'
    $line = Get-Content -LiteralPath $ConstantsFile | Where-Object { $_ -match $pattern } | Select-Object -First 1
    if (-not $line) {
        return $FallbackName
    }

    $match = [regex]::Match($line, $pattern)
    if (-not $match.Success) {
        return $FallbackName
    }

    $candidate = ($match.Groups[1].Value).Trim()
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return $FallbackName
    }

    $invalidChars = [System.IO.Path]::GetInvalidFileNameChars()
    $safeName = -join ($candidate.ToCharArray() | ForEach-Object {
        if ($invalidChars -contains $_) { "_" } else { $_ }
    })

    $safeName = $safeName.Trim()
    if ([string]::IsNullOrWhiteSpace($safeName)) {
        return $FallbackName
    }

    return $safeName
}

$shortcutBaseName = Get-SafeShortcutNameFromAppTitle -ConstantsFile $constantsPath -FallbackName "QubitMCP"

$launcherPythonW = Join-Path $repoRoot ".venv\Scripts\pythonw.exe"
$launcherPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$entryScript = Join-Path $repoRoot "echograph_app.py"
$iconPath = Join-Path $repoRoot "icons\QubitMCP_Icon.ico"
$desktopShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) ($shortcutBaseName + ".lnk")

if (-not (Test-Path -LiteralPath $entryScript)) {
    throw "Missing app entry script: $entryScript"
}

$launcherPath = ""
if (Test-Path -LiteralPath $launcherPythonW) {
    $launcherPath = $launcherPythonW
} elseif (Test-Path -LiteralPath $launcherPython) {
    $launcherPath = $launcherPython
} else {
    throw "Missing launcher python executable. Expected one of: $launcherPythonW or $launcherPython"
}

if (-not ("ShortcutAppIdNative" -as [type])) {
    Add-Type -Language CSharp -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
internal class ShellLink
{
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
internal interface IShellLinkW
{
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMaxPath, IntPtr pfd, int fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMaxName);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMaxPath);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMaxPath);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchIconPath, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
    void Resolve(IntPtr hwnd, int fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
internal interface IPropertyStore
{
    int GetCount(out uint cProps);
    int GetAt(uint iProp, out PROPERTYKEY pkey);
    int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
    int SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
    int Commit();
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("0000010b-0000-0000-C000-000000000046")]
internal interface IPersistFile
{
    void GetClassID(out Guid pClassID);
    void IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct PROPERTYKEY
{
    public Guid fmtid;
    public uint pid;
}

[StructLayout(LayoutKind.Sequential)]
internal struct PROPVARIANT
{
    public ushort vt;
    public ushort wReserved1;
    public ushort wReserved2;
    public ushort wReserved3;
    public IntPtr pwszVal;
    public int cVal;

    public static PROPVARIANT FromString(string value)
    {
        PROPVARIANT pv = new PROPVARIANT();
        pv.vt = 31; // VT_LPWSTR
        pv.pwszVal = Marshal.StringToCoTaskMemUni(value);
        return pv;
    }

    public void Clear()
    {
        PropVariantClear(ref this);
    }

    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref PROPVARIANT pvar);
}

public static class ShortcutAppIdNative
{
    private static readonly PROPERTYKEY PKEY_AppUserModel_ID = new PROPERTYKEY
    {
        fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
        pid = 5
    };

    public static void SetShortcutAppId(string shortcutPath, string appId)
    {
        IShellLinkW shellLink = (IShellLinkW)new ShellLink();
        ((IPersistFile)shellLink).Load(shortcutPath, 2); // STGM_READWRITE
        IPropertyStore store = (IPropertyStore)shellLink;
        PROPERTYKEY key = PKEY_AppUserModel_ID;

        PROPVARIANT pv = PROPVARIANT.FromString(appId);
        try
        {
            int hr = store.SetValue(ref key, ref pv);
            if (hr != 0)
            {
                Marshal.ThrowExceptionForHR(hr);
            }
            hr = store.Commit();
            if (hr != 0)
            {
                Marshal.ThrowExceptionForHR(hr);
            }
            ((IPersistFile)shellLink).Save(shortcutPath, true);
        }
        finally
        {
            pv.Clear();
        }
    }

    public static string GetShortcutAppId(string shortcutPath)
    {
        IShellLinkW shellLink = (IShellLinkW)new ShellLink();
        ((IPersistFile)shellLink).Load(shortcutPath, 0);
        IPropertyStore store = (IPropertyStore)shellLink;
        PROPERTYKEY key = PKEY_AppUserModel_ID;

        PROPVARIANT pv;
        int hr = store.GetValue(ref key, out pv);
        if (hr != 0)
        {
            Marshal.ThrowExceptionForHR(hr);
        }

        try
        {
            if (pv.vt == 31 && pv.pwszVal != IntPtr.Zero)
            {
                return Marshal.PtrToStringUni(pv.pwszVal);
            }
            return string.Empty;
        }
        finally
        {
            pv.Clear();
        }
    }
}
"@
}

function New-QubitShortcut {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ShortcutPath
    )

    $shortcutDir = Split-Path -Path $ShortcutPath -Parent
    if (-not (Test-Path -LiteralPath $shortcutDir)) {
        New-Item -ItemType Directory -Path $shortcutDir -Force | Out-Null
    }

    $wsh = New-Object -ComObject WScript.Shell
    $shortcut = $wsh.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $launcherPath
    $shortcut.Arguments = '"' + $entryScript + '"'
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.Description = "Launch $shortcutBaseName"
    if (Test-Path -LiteralPath $iconPath) {
        $shortcut.IconLocation = "$iconPath,0"
    }
    $shortcut.Save()

    [ShortcutAppIdNative]::SetShortcutAppId($ShortcutPath, $appUserModelId)
    $readBack = [ShortcutAppIdNative]::GetShortcutAppId($ShortcutPath)
    if ($readBack -ne $appUserModelId) {
        throw "Failed to verify AppUserModelID for shortcut: $ShortcutPath"
    }

    Write-Output "[shortcut] Updated: $ShortcutPath"
}

$legacyRepoShortcutPath = Join-Path $repoRoot "QubitMCP Launcher.lnk"
if (Test-Path -LiteralPath $legacyRepoShortcutPath) {
    try {
        Remove-Item -LiteralPath $legacyRepoShortcutPath -Force
    } catch {
        Write-Warning "[shortcut] Failed to remove legacy repo shortcut: $legacyRepoShortcutPath"
    }
}

$legacyDesktopShortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "QubitMCP.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "QubitMCP Launcher.lnk")
)
foreach ($legacyPath in $legacyDesktopShortcutPaths) {
    if ($legacyPath -ne $desktopShortcutPath -and (Test-Path -LiteralPath $legacyPath)) {
        try {
            Remove-Item -LiteralPath $legacyPath -Force
        } catch {
            Write-Warning "[shortcut] Failed to remove legacy desktop shortcut: $legacyPath"
        }
    }
}

if ($Scope -ne "Desktop") {
    throw "Unsupported scope: $Scope"
}

try {
    New-QubitShortcut -ShortcutPath $desktopShortcutPath
} catch {
    throw
}

Write-Output "[shortcut] Launcher target: $launcherPath"
Write-Output "[shortcut] AppUserModelID: $appUserModelId"
Write-Output "[shortcut] Shortcut name: $shortcutBaseName"
