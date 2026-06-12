@echo off
setlocal
title Connect your Canvas to CourseForge
echo.
echo  ==================================================
echo    Connect your Canvas to CourseForge
echo.
echo    You will paste two things:
echo      1. your Canvas course web address
echo      2. your Canvas access token
echo    Then you are done. Nothing else to set up.
echo  ==================================================
echo.

set "HERE=%~dp0"

rem  Find Setup-Canvas.ps1 wherever it lives (downloaded repo, installed plugin,
rem  or hand-copied skill), then run it. It writes your token + settings into
rem  Documents\canvas-work -- the standard folder the CourseForge skill checks.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $cands=@((Join-Path $env:HERE 'plugins\courseforge\skills\courseforge\scripts\Setup-Canvas.ps1'),(Join-Path $env:USERPROFILE '.claude\skills\courseforge\scripts\Setup-Canvas.ps1')); $found=$cands | Where-Object { Test-Path $_ } | Select-Object -First 1; if(-not $found){ $found=Get-ChildItem (Join-Path $env:USERPROFILE '.claude\plugins') -Recurse -Filter 'Setup-Canvas.ps1' -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName }; if(-not $found){ Write-Host ''; Write-Host 'Could not find the CourseForge setup script.' -ForegroundColor Red; Write-Host 'Install the courseforge plugin first (see the README), or run this file from the downloaded repo folder.' -ForegroundColor Yellow; exit 1 }; $work=Join-Path $env:USERPROFILE 'Documents\canvas-work'; & $found -WorkingDir $work"

echo.
echo  When this window says you are connected, you can close it and open Claude Code.
echo.
pause
endlocal
