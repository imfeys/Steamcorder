@echo off
rem Builds dist\Steamcorder.exe. Needs the Rust toolchain (https://rustup.rs) and the MSVC C++ build tools.
cd /d "%~dp0"
cargo test || exit /b 1
cargo build --release || exit /b 1
if not exist dist mkdir dist
copy /y target\release\Steamcorder.exe dist\Steamcorder.exe >nul || exit /b 1
echo.
echo Done: dist\Steamcorder.exe
