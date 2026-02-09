@echo off
start "ERR" powershell -NoExit -Command "Get-Content -Path 'C:\tenant_management_system\logs\server.err.log' -Tail 50 -Wait"
start "OUT" powershell -NoExit -Command "Get-Content -Path 'C:\tenant_management_system\logs\server.out.log' -Tail 50 -Wait"
