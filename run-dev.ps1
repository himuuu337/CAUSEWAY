# Two processes, two windows: the API, and the Vite dev server that proxies to it.
# For the demo itself prefer the built bundle - see README, "Running it".
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "cd '$root\backend'; python -m causeway.api"
)
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList @(
  "-NoExit", "-Command",
  "cd '$root\frontend'; npm run dev"
)
Write-Host "API      http://127.0.0.1:8000/api/health"
Write-Host "Frontend http://127.0.0.1:5173"
