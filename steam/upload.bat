@echo off
rem Uploads steam\content\ to Steam with SteamPipe.
rem 1. Unzip the "Steamcorder-steam" build artifact into steam\content\
rem 2. Put steamcmd.exe (from the Steamworks SDK) in steam\steamcmd\
rem 3. Run: upload.bat your_steam_username
cd /d "%~dp0"
if "%~1"=="" ( echo Usage: upload.bat STEAM_USERNAME & exit /b 1 )
if not exist content\Steamcorder.exe ( echo Unzip the Steamcorder-steam artifact into steam\content first. & exit /b 1 )
steamcmd\steamcmd.exe +login %1 +run_app_build "%~dp0app_build.vdf" +quit
