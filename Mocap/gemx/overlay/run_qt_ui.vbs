Option Explicit

Dim fso, shell, repoDir, pythonwExe, launcherPy, command

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repoDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonwExe = fso.BuildPath(repoDir, ".venv\Scripts\pythonw.exe")
launcherPy = fso.BuildPath(repoDir, "scripts\ui\gem_qt_launcher.py")

If Not fso.FileExists(pythonwExe) Then
    MsgBox ".venv was not found. Run setup.bat first.", vbCritical, "GEM-X Qt UI"
    WScript.Quit 1
End If

If Not fso.FileExists(launcherPy) Then
    MsgBox "Qt launcher was not found: " & launcherPy, vbCritical, "GEM-X Qt UI"
    WScript.Quit 1
End If

shell.CurrentDirectory = repoDir
command = """" & pythonwExe & """ """ & launcherPy & """"
shell.Run command, 0, False
