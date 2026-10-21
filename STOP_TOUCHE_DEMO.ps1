param([string]$Root = "")
$ErrorActionPreference = "Stop"
if (-not $Root) { $Root = Get-ChildItem (Join-Path $env:TEMP "touche-finans-demo-*") -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName }
$pidFile = Join-Path $Root "demo.pid"
if(-not (Test-Path $pidFile)){ Write-Output "Çalışan demo PID kaydı bulunamadı."; exit 0 }
$id = [int](Get-Content -LiteralPath $pidFile)
$p = Get-Process -Id $id -ErrorAction SilentlyContinue
if($p){ Stop-Process -Id $id -Force; Write-Output "Sentetik demo durduruldu: PID $id" } else { Write-Output "Demo zaten durmuş: PID $id" }
