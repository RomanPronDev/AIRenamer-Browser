# SPDX-License-Identifier: GPL-3.0-only
param(
    [switch] $Plan,
    [switch] $SkipChromeRestart
)
$ErrorActionPreference = 'Stop'
$stable = Join-Path $env:LOCALAPPDATA 'MediaRenamer\Browser\Extension'
$extensionFolder = $null
$profiles = Join-Path $env:LOCALAPPDATA 'Google\Chrome\User Data'
$loadedFolders = @()
foreach ($folder in @(Get-Item -LiteralPath (Join-Path $profiles 'Default') -ErrorAction SilentlyContinue) +
                     @(Get-ChildItem -LiteralPath $profiles -Directory -Filter 'Profile *' -ErrorAction SilentlyContinue)) {
    foreach ($filename in @('Preferences', 'Secure Preferences')) {
        $preferences = Join-Path $folder.FullName $filename
        if (-not (Test-Path -LiteralPath $preferences)) { continue }
        try {
            $settings = (Get-Content -LiteralPath $preferences -Raw -Encoding UTF8 | ConvertFrom-Json).extensions.settings
            foreach ($property in $settings.PSObject.Properties) {
                $entry = $property.Value
                if ($entry.manifest.name -notin @('AIRenamer · Browser', 'AIRenamer · Browser Beta') -or -not $entry.path) { continue }
                $candidate = [System.IO.Path]::GetFullPath($entry.path)
                $manifest = Join-Path $candidate 'manifest.json'
                if (-not (Test-Path -LiteralPath $manifest) -or
                    -not (Test-Path -LiteralPath (Join-Path $candidate 'live.js'))) { continue }
                if ((Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json).name -in @('AIRenamer · Browser', 'AIRenamer · Browser Beta')) {
                    $loadedFolders += $candidate
                }
            }
        } catch { continue }
    }
}
$loadedFolders = @($loadedFolders | Select-Object -Unique)
if ($loadedFolders.Count -gt 1) {
    throw 'Several AIRenamer extension folders are loaded. Remove duplicates in chrome://extensions and run Update again.'
}
$loadedInChrome = $loadedFolders.Count -eq 1
if ($loadedInChrome) {
    $extensionFolder = $loadedFolders[0]
} elseif (Test-Path -LiteralPath (Join-Path $stable 'manifest.json')) {
    $extensionFolder = $stable
} else {
    $extensionFolder = $stable
    Write-Output 'No loaded AIRenamer extension was found in Chrome profiles; installing to the standard folder.'
}
Write-Output "Updating extension folder: $extensionFolder"
$installArgs = @{ ExtensionFolder = $extensionFolder }
if ($Plan) { $installArgs.Plan = $true }
$lines = @(& (Join-Path $PSScriptRoot 'Install-BrowserNative.ps1') @installArgs)
$lines | ForEach-Object { Write-Output $_ }
if ($Plan) { return }
$idLine = $lines | Where-Object { $_ -match '^Extension ID: ([a-p]{32})$' } | Select-Object -First 1
if (-not $idLine) { throw 'Could not determine the updated extension ID.' }
$id = [regex]::Match($idLine, '([a-p]{32})$').Value
$chrome = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Output 'Update installed. Open Chrome and click the AIRenamer icon.'
    return
}
if (-not $loadedInChrome) {
    Start-Process -FilePath $chrome -ArgumentList 'chrome://extensions'
    Write-Output "If this is the first installation, use Load unpacked and select $extensionFolder."
    return
}
if (-not $SkipChromeRestart -and @(Get-Process chrome -ErrorAction SilentlyContinue).Count) {
    Write-Output 'Restarting Chrome to load the updated extension...'
    Start-Process -FilePath $chrome -ArgumentList 'chrome://restart'
    Start-Sleep -Seconds 6
}
# Chrome only allows opening an actual side panel after a browser user gesture.
# Reopen the updated UI as a tab so the update is immediately visible.
Start-Process -FilePath $chrome -ArgumentList "chrome-extension://$id/live.html"
Write-Output 'Updated AIRenamer opened. Use the pinned AIRenamer icon to place it in the side panel.'
