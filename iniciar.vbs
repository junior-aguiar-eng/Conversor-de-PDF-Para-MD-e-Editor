Option Explicit

Dim shell, folder, command
Set shell = CreateObject("WScript.Shell")
folder = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
command = Chr(34) & folder & ".venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & folder & "app.py" & Chr(34)

' 0 executa oculto; False permite que o aplicativo gráfico continue independente.
shell.Run command, 0, False
