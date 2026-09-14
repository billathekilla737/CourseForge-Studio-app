' CourseForge Studio - double-click launcher.
'
' Starts the GUI with pythonw.exe so no console window ever appears.
' Window style 0 = hidden; the tkinter window is the only thing you see.

Option Explicit

Dim fso, sh, here, exe, candidates, i, ok, style

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here

If fso.FileExists(here & "\config.example.json") And Not fso.FileExists(here & "\config.json") Then
    fso.CopyFile here & "\config.example.json", here & "\config.json", True
End If

' Prefer pythonw.exe (no console). Fall back to the py launcher's windowed form,
' then a visible python so an ImportError is not swallowed by a hidden window.
candidates = Array("pythonw.exe", "pyw.exe", "python.exe", "py.exe")

ok = False
For i = 0 To UBound(candidates)
    exe = candidates(i)
    style = 0
    If InStr(1, exe, "python.exe", vbTextCompare) > 0 Or InStr(1, exe, "py.exe", vbTextCompare) > 0 Then
        style = 1
    End If
    On Error Resume Next
    sh.Run """" & exe & """ -m courseforge.launcher", style, False
    If Err.Number = 0 Then
        ok = True
        Err.Clear
        Exit For
    End If
    Err.Clear
    On Error GoTo 0
Next

If Not ok Then
    MsgBox "Could not find Python on your PATH." & vbCrLf & vbCrLf & _
           "Install Python 3.10 or newer from python.org and tick " & _
           """Add Python to PATH"" during setup. Then, in this folder, run:" & vbCrLf & _
           "    pip install -e .[pdf]" & vbCrLf & vbCrLf & _
           "You can still start the tool from a terminal with:" & vbCrLf & _
           "    python -m courseforge gui", _
           vbExclamation, "CourseForge Studio"
End If
