@echo off
rem ===========================================================================
rem  Ustanovka skladskoy sistemy OOO "LEKO".
rem
rem  Dvoynoy klik po etomu faylu stavit sistemu s nulya: Python, okruzhenie,
rem  biblioteki, nastroyki, baza, administrator, oformlenie, papki. V konce
rem  proveryaet vse sostavnye chasti i govorit, chto ostalos sdelat.
rem
rem  Povtornyy zapusk bezopasen: gotovoe ne peredelyvaetsya.
rem
rem  Kommentarii latinitsey namerenno: .bat Windows chitaet v kodirovke
rem  cp866, i kirillitsa v nih prevrashchaetsya v musor. Ves russkiy tekst
rem  vyvodit PowerShell, kotoryy s kodirovkami spravlyaetsya.
rem ===========================================================================
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*

echo.
pause
