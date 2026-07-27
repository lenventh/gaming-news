' Dashboard launcher - opens in Chrome/Edge app mode (no browser chrome)
' Looks like a desktop floating widget

Dim url, chrome, edge, browser

url = "http://127.0.0.1:8766"

' Try Chrome first
chrome = """C:\Program Files\Google\Chrome\Application\chrome.exe"""
edge = """C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"""

Set objShell = CreateObject("WScript.Shell")

' Check Chrome
If CreateObject("Scripting.FileSystemObject").FileExists(chrome) Then
    objShell.Run """" & chrome & """ --app=" & url & " --window-size=520,700 --window-position=right,top", 0, False
ElseIf CreateObject("Scripting.FileSystemObject").FileExists(edge) Then
    objShell.Run """" & edge & """ --app=" & url & " --window-size=520,700 --window-position=right,top", 0, False
Else
    ' Fallback: open in default browser
    objShell.Run "rundll32 url.dll,FileProtocolHandler " & url
End If
