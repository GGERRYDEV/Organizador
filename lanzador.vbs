Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")

' Obtener la ruta del directorio donde se ejecuta este script VBS
strFolder = objFSO.GetParentFolderName(WScript.ScriptFullName)
strPythonScript = strFolder & "\app_visual.py"

' Obtener todos los argumentos pasados a este script lanzador
strArgs = ""
For Each arg In WScript.Arguments
    strArgs = strArgs & " " & arg
Next

' Ejecutar el script de Python con los mismos argumentos recibidos
objShell.Run "pythonw """ & strPythonScript & """" & strArgs, 0, False
