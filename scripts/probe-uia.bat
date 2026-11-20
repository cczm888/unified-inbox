@echo off
chcp 65001 >nul
cd /d "C:\Users\75090\Desktop\DEEP SEEK\unified-inbox"
echo 正在探查钉钉的 UIA 控件树，约 20 秒...
echo.
".\backend\.venv\Scripts\python.exe" ".\scripts\probe-dingtalk-uia.py" > ".\logs\uia-probe.txt" 2>&1
".\backend\.venv\Scripts\python.exe" ".\scripts\probe-uia-env.py" >> ".\logs\uia-probe.txt" 2>&1
echo 结果已写入 logs\uia-probe.txt
echo.
type ".\logs\uia-probe.txt"
echo.
pause
