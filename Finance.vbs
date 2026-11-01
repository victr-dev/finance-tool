' Starts the Finance app in the background (no console window) and opens it in your browser.
' Double-click this file, or the "Finance" shortcut on your desktop.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "pythonw """ & here & "\app\server.py"" --open", 0, False
