' CourseForge Studio - double-click launcher.
'
' Starts the GUI with pythonw.exe so no console window ever appears.
' Window style 0 = hidden; the tkinter window is the only thing you see.

Option Explicit

Dim fso, sh, here, exe, candidates, i, ok

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here

' Prefer pythonw.exe (no console). Fall back to the py launcher's windowed form.
candidates = Array("pythonw.exe", "pyw.exe")

ok = False
For i = 0 To UBound(candidates)
    exe = candidates(i)
    On Error Resume Next
    sh.Run """" & exe & """ -m courseforge.launcher", 0, False
    If Err.Number = 0 Then
        ok = True
        Err.Clear
        Exit For
    End If
    Err.Clear
    On Error GoTo 0
Next

If Not ok Then
    MsgBox "Could not find pythonw.exe on your PATH." & vbCrLf & vbCrLf & _
           "Install Python 3.10 or newer from python.org and tick " & _
           """Add Python to PATH"" during setup, then try again." & vbCrLf & vbCrLf & _
           "You can still run the tool from a terminal with:" & vbCrLf & _
           "    python -m courseforge serve", _
           vbExclamation, "CourseForge Studio"
End If
