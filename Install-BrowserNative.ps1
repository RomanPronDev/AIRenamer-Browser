# SPDX-License-Identifier: GPL-3.0-only
param(
    [string] $ExtensionId,
    [string] $ExtensionFolder,
    [string] $HostExe = "$PSScriptRoot\dist\MediaRenamerBrowserNative.exe",
    [switch] $Plan,
    [switch] $OpenChromeExtensions
)
$ErrorActionPreference = 'Stop'

function Start-OptionalFFmpegSetup([string] $Executable, [string] $InstallDirectory) {
    try {
        Start-Process -FilePath $Executable -ArgumentList '--install-ffmpeg' -WindowStyle Hidden -ErrorAction Stop
        Write-Output 'FFmpeg background setup started. Conversion will verify readiness when needed.'
    } catch {
        $detail = "Optional FFmpeg setup could not start. Native host: $Executable`r`n$($_.Exception.ToString())"
        $diagnostic = Join-Path $InstallDirectory 'setup-ffmpeg-launch.log'
        try { [System.IO.File]::WriteAllText($diagnostic, $detail, [System.Text.UTF8Encoding]::new($false)) } catch { }
        Write-Warning 'AIRenamer installation completed, but Windows declined optional FFmpeg setup. Conversion will retry on first use.'
        Write-Output "Launch diagnostics: $diagnostic"
        Write-Output 'If the Chrome panel cannot connect either, the local companion may also be blocked by Windows or company policy. Ask IT to review the executable and the launch log.'
    }
}

function Get-UnpackedExtensionId([string] $Folder) {
    # Chromium hashes the Windows FilePath UTF-16 bytes and maps the first
    # 16 SHA-256 bytes from hexadecimal to the a-p extension ID alphabet.
    $absolute = [System.IO.Path]::GetFullPath($Folder)
    if ($absolute.Length -ge 2 -and $absolute[1] -eq ':') {
        $absolute = $absolute.Substring(0, 1).ToUpperInvariant() + $absolute.Substring(1)
    }
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $hash.ComputeHash([System.Text.Encoding]::Unicode.GetBytes($absolute))
    } finally {
        $hash.Dispose()
    }
    $letters = 'abcdefghijklmnop'
    $id = [System.Text.StringBuilder]::new(32)
    for ($index = 0; $index -lt 16; $index++) {
        [void] $id.Append($letters[$digest[$index] -shr 4])
        [void] $id.Append($letters[$digest[$index] -band 15])
    }
    return $id.ToString()
}

if ($ExtensionId -and $ExtensionId -cnotmatch '^[a-p]{32}$') {
    throw 'ExtensionId must be a 32-character Chrome extension ID.'
}
$sourceExtension = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'browser') -ErrorAction Stop).Path
$resolvedExe = (Resolve-Path -LiteralPath $HostExe -ErrorAction Stop).Path
$version = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'version.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Invalid browser version.'
}
$installBase = Join-Path $env:LOCALAPPDATA 'MediaRenamer\Browser'
$installedExtension = if ($ExtensionFolder) { [System.IO.Path]::GetFullPath($ExtensionFolder) } else { [System.IO.Path]::GetFullPath((Join-Path $installBase 'Extension')) }
if ($installedExtension -ieq $sourceExtension) { throw 'Install destination must differ from the package source folder.' }
if ((Test-Path -LiteralPath $installedExtension) -and (Test-Path -LiteralPath (Join-Path $installedExtension 'manifest.json'))) {
    $existingName = (Get-Content -LiteralPath (Join-Path $installedExtension 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json).name
    if ($existingName -notin @('AIRenamer · Browser', 'AIRenamer · Browser Beta')) { throw 'Target folder contains a different extension.' }
}
$previousManifest = Join-Path $installedExtension 'manifest.json'
$previousVersion = $null
if (Test-Path -LiteralPath $previousManifest) {
    try {
        $previousVersion = (Get-Content -LiteralPath $previousManifest -Raw -Encoding UTF8 | ConvertFrom-Json).version
    } catch {
        $previousVersion = $null
    }
}
$manualChromeSetup = -not $previousVersion -or $previousVersion -notmatch '^\d+\.\d+\.\d+$'
if (-not $manualChromeSetup) {
    $manualChromeSetup = [version]$previousVersion -lt [version]'0.26.18'
}
$hostDirectory = Join-Path $installBase 'Host'
$installedExe = Join-Path $hostDirectory "MediaRenamerBrowserNative-$version.exe"
$manifestPath = Join-Path $hostDirectory 'com.airenamer.browser.json'
$computedId = Get-UnpackedExtensionId $installedExtension
$origins = @("chrome-extension://$computedId/")
if ($ExtensionId -and $ExtensionId -cne $computedId) {
    $origins += "chrome-extension://$ExtensionId/"
}

Write-Output "Extension folder: $installedExtension"
Write-Output "Extension ID: $computedId"
Write-Output "Native host: $installedExe"
if ($Plan) { return }

New-Item -ItemType Directory -Force -Path $installedExtension, $hostDirectory | Out-Null
Get-ChildItem -LiteralPath $sourceExtension -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $installedExtension -Recurse -Force
}
$oldSiteScript = Join-Path $installedExtension 'site-download.js'
if (Test-Path -LiteralPath $oldSiteScript) {
    Remove-Item -LiteralPath $oldSiteScript -Force
}
foreach ($legacyFile in @('app.js', 'index.html', 'model.js', 'model.test.cjs', 'styles.css', 'verify.cjs')) {
    $legacyPath = Join-Path $installedExtension $legacyFile
    if (Test-Path -LiteralPath $legacyPath) {
        Remove-Item -LiteralPath $legacyPath -Force
    }
}
Copy-Item -LiteralPath $resolvedExe -Destination $installedExe -Force
$manifest = @{
    name = 'com.airenamer.browser'
    description = 'AIRenamer local project and media access'
    path = $installedExe
    type = 'stdio'
    allowed_origins = $origins
}
[System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
$registryPath = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.airenamer.browser'
New-Item -Path $registryPath -Force | Out-Null
Set-Item -Path $registryPath -Value $manifestPath
$legacySharedSettings = $false
if ($previousVersion -match '^\d+\.\d+\.\d+$') {
    $legacySharedSettings = [version]$previousVersion -lt [version]'0.27.7'
}
# Preserve a pending migration across updates before the Browser first opens.
$installationPath = Join-Path $installBase 'installation.json'
if (Test-Path -LiteralPath $installationPath) {
    try {
        $oldInstallation = Get-Content -LiteralPath $installationPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($oldInstallation.legacySharedSettings -eq $true) { $legacySharedSettings = $true }
    } catch { }
}
$installation = @{ extensionFolder = $installedExtension; version = $version; legacySharedSettings = $legacySharedSettings }
$installationPath = Join-Path $installBase 'installation.json'
[System.IO.File]::WriteAllText($installationPath, ($installation | ConvertTo-Json -Depth 3), [System.Text.UTF8Encoding]::new($false))
$installedFFmpeg = Join-Path (Split-Path -Parent $installBase) 'Tools\ffmpeg.exe'
$ffmpegReady = $false
if (Test-Path -LiteralPath $installedFFmpeg) {
    try {
        $ffmpegVersion = @(& $installedFFmpeg -version 2>$null)
        $ffmpegReady = $LASTEXITCODE -eq 0 -and $ffmpegVersion[0] -match '^ffmpeg version '
    } catch { $ffmpegReady = $false }
}
if (-not $ffmpegReady) {
    Start-OptionalFFmpegSetup -Executable $installedExe -InstallDirectory $installBase
}
Write-Output 'Local companion installed. Chrome starts it automatically when AIRenamer needs a file.'
if (-not $previousVersion) {
    Write-Output "First-time Chrome setup: enable Developer mode, then Load unpacked -> $installedExtension"
} elseif ($manualChromeSetup) {
    Write-Output 'One-time upgrade: click Reload on the AIRenamer card in Chrome extensions.'
} else {
    Write-Output 'Update installed. Chrome activates it at next startup or when AIRenamer opens.'
}

if ($OpenChromeExtensions -and $manualChromeSetup) {
    $chromeCandidates = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    $chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($chrome) {
        try {
            Start-Process -FilePath $chrome -ArgumentList 'chrome://extensions' -ErrorAction Stop
        } catch {
            Write-Warning 'Installation completed, but Chrome could not be opened automatically. Open chrome://extensions manually.'
        }
    } else {
        Write-Output 'Open chrome://extensions in Chrome to load the extension folder.'
    }
}
