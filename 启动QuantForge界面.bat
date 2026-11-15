@echo off
chcp 65001 >nul
title QuantForge Launcher
cd /d %~dp0
rem 新默认入口=Web 控制台（D-012）；GUI 需要 tkinter → 优先 Anaconda 基座
set "PYW=%USERPROFILE%\anaconda3\pythonw.exe"
if exist "%PYW%" (
  start "" "%PYW%" qf_console.py
) else (
  start "" python qf_console.py
)
exit
