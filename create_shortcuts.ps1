$desktop = [Environment]::GetFolderPath("Desktop")
$project = "C:\Users\ShiChang\gaming_news_v2"

@(
    @{Name="周刊-1采集处理"; Script="run_pipeline.bat"},
    @{Name="周刊-2审核过滤"; Script="run_review.bat"},
    @{Name="周刊-3回捞生成"; Script="run_recover.bat"}
) | ForEach-Object {
    $shortcut = Join-Path $desktop "$($_.Name).lnk"
    $WshShell = New-Object -ComObject WScript.Shell
    $lnk = $WshShell.CreateShortcut($shortcut)
    $lnk.TargetPath = Join-Path $project $_.Script
    $lnk.WorkingDirectory = $project
    $lnk.IconLocation = "shell32.dll,13"
    $lnk.Save()
    Write-Host "Created: $shortcut"
}
Write-Host ""
Write-Host "3 desktop shortcuts created. Double-click in order 1-2-3."
