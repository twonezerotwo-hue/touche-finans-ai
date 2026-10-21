param([string]$Root = "", [int]$Port = 0)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Root) { $Root = Join-Path $env:TEMP ("touche-finans-demo-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
New-Item -ItemType Directory -Path $Root -Force | Out-Null
$out = Join-Path $Root "stdout.log"; $err = Join-Path $Root "stderr.log"
$args = @("-3.12", "scripts/demo_local.py", "--root", $Root, "--port", $Port)
$p = Start-Process -FilePath "py" -ArgumentList $args -WorkingDirectory $Repo -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
Set-Content -LiteralPath (Join-Path $Root "demo.pid") -Value $p.Id
for($i=0; $i -lt 60; $i++){
  Start-Sleep -Milliseconds 250
  if(Test-Path $out){
    $line = Select-String -LiteralPath $out -Pattern "^DEMO_URL=" | Select-Object -First 1
    if($line){ $line.Line; Write-Output "Demo kökü: $Root"; exit 0 }
  }
  if($p.HasExited){ Get-Content $err; throw "Demo başlatılamadı (exit $($p.ExitCode))." }
}
throw "Demo URL'si 15 saniye içinde oluşmadı. Log: $out"
