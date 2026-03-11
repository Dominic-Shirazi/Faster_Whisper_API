#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = $scriptDir
$PythonPath = Join-Path $BaseDir ".venv\Scripts\pythonw.exe"
$ScriptPath = Join-Path $BaseDir "transcribe_file.py"

Write-Host "--- Installing Right-Click 'Transcribe' Context Menu ---"

if (-not (Test-Path $PythonPath)) {
    Write-Error "Could not find Python virtual environment at $PythonPath. Please run setup first."
    pause
    exit
}

if (-not (Test-Path $ScriptPath)) {
    Write-Error "Could not find script at $ScriptPath"
    pause
    exit
}

Write-Host "Creating Registry Keys..."
$Command = "`"$PythonPath`" `"$ScriptPath`" `"%1`""
try {
    # Using .NET Registry classes directly safely bypasses PowerShell's '*' wildcard path parsing bugs
    $baseKey = [Microsoft.Win32.Registry]::ClassesRoot.CreateSubKey("*\shell\TranscribeToClipboard")
    $baseKey.SetValue("MUIVerb", "Transcribe to Clipboard")
    $baseKey.SetValue("Icon", "shell32.dll,301")
    $baseKey.Close()

    $cmdKey = [Microsoft.Win32.Registry]::ClassesRoot.CreateSubKey("*\shell\TranscribeToClipboard\command")
    $cmdKey.SetValue("", $Command) # empty string sets the (default) value
    $cmdKey.Close()
} catch {
    Write-Error "Failed to write registry keys: $_"
    pause
    exit
}

Write-Host "--- Installation Complete! ---"
Write-Host "You can now right click any audio file and select 'Transcribe to Clipboard'."
pause
