@echo off
rem ===========================================================================
rem  Obnovlenie skladskoy sistemy OOO "LEKO".
rem
rem  Dvoynoy klik po etomu faylu zapuskaet obnovlenie: kopiya bazy,
rem  obnovlenie koda i bibliotek, migratsii, proverka.
rem
rem  Pered zapuskom zakroyte okno s rabotayushchim serverom.
rem
rem  Kommentarii latinitsey namerenno: .bat Windows chitaet v kodirovke
rem  cp866, i kirillitsa v nih prevrashchaetsya v musor. Ves russkiy tekst
rem  vyvodit PowerShell, kotoryy s kodirovkami spravlyaetsya.
rem ===========================================================================
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*

echo.
pause
