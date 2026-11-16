' Start YinYue from the directory containing this script.
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
python = fs.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fs.FileExists(python) Then
    MsgBox "Create the Python environment by following README.md first.", 48, "YinYue"
    WScript.Quit 1
End If
sh.CurrentDirectory = root
sh.Run """" & python & """ """ & fs.BuildPath(root, "assistant.py") & """", 0, False
