$BaseDir = $PSScriptRoot
$Pythonw = Join-Path $BaseDir ".venv\Scripts\pythonw.exe"
$Watchdog = Join-Path $BaseDir "listener_watchdog.py"

if (-Not (Test-Path $Pythonw)) {
    Write-Host "Error: Could not find pythonw.exe at $Pythonw" -ForegroundColor Red
    Write-Host "Make sure your virtual environment is set up correctly."
    Pause
    exit
}

$StartupFolder = [Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupFolder "FasterWhisperListener.lnk"

Try {
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $Pythonw
    $Shortcut.Arguments = "`"$Watchdog`""
    $Shortcut.WorkingDirectory = $BaseDir
    $Shortcut.IconLocation = "$Pythonw, 0"
    $Shortcut.Save()

    Write-Host "--- Success! ---" -ForegroundColor Green
    Write-Host "A shortcut has been added to your Windows Startup folder:"
    Write-Host $ShortcutPath
    Write-Host "The System Tray Listener will now start automatically when you log into Windows."
    Write-Host ""
} Catch {
    Write-Host "Error creating shortcut: $_" -ForegroundColor Red
}

Pause
