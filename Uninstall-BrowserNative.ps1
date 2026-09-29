# SPDX-License-Identifier: GPL-3.0-only
$ErrorActionPreference = 'Stop'
$registryPath = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.airenamer.browser'
if (Test-Path -LiteralPath $registryPath) {
    Remove-Item -LiteralPath $registryPath
}
$hostDirectory = Join-Path $env:LOCALAPPDATA 'MediaRenamer\Browser\Host'
$manifestPath = Join-Path $hostDirectory 'com.airenamer.browser.json'
if (Test-Path -LiteralPath $manifestPath) {
    Remove-Item -LiteralPath $manifestPath
}
if (Test-Path -LiteralPath $hostDirectory) {
    Get-ChildItem -LiteralPath $hostDirectory -File -Filter 'MediaRenamerBrowserNative*.exe' |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName }
}
Write-Output 'AIRenamer Browser Native host unregistered. Remove the extension in Chrome separately.'
