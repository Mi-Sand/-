@echo off
rem ===========================================================================
rem  Proverka ustanovki skladskoy sistemy OOO "LEKO".
rem
rem  Dvoynoy klik proveryaet vse sostavnye chasti i nichego ne menyaet:
rem  Python, okruzhenie, biblioteki, nastroyki, baza, sotrudniki,
rem  oformlenie, papki, kopii, port, sertifikat.
rem
rem  Zapuskat mozhno v lyuboy moment, v tom chisle na rabotayushchey sisteme.
rem ===========================================================================
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Check %*

echo.
pause
