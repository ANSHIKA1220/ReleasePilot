$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed ($LASTEXITCODE)" }
}
function Kube([string[]]$Arguments) {
    Invoke-Checked kubectl (@('--context','kind-releasepilot','-n','releasepilot-demo') + $Arguments)
}
function New-SecretValue {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}
foreach ($tool in 'docker','kind','kubectl') { Get-Command $tool -ErrorAction Stop | Out-Null }
$clusters = @(Invoke-Checked kind @('get','clusters'))
if ($clusters -notcontains 'releasepilot') { Invoke-Checked kind @('create','cluster','--name','releasepilot') }
Invoke-Checked docker @('build','-t','releasepilot:local','.')
Invoke-Checked docker @('build','-t','releasepilot-demo:bootstrap','demo')
Invoke-Checked kind @('load','docker-image','releasepilot:local','releasepilot-demo:bootstrap','--name','releasepilot')
# Preserve the live workload template when running setup again.
$ns = Invoke-Checked kubectl @('--context','kind-releasepilot','get','ns','releasepilot-demo','--ignore-not-found','-o','name')
if (!$ns) { Invoke-Checked kubectl @('--context','kind-releasepilot','create','ns','releasepilot-demo') }
$target = Kube @('get','deployment','demo-api','--ignore-not-found','-o','name')
if (!$target) { Kube @('apply','-f','infra/targets.yaml') }
$config = Kube @('get','secret','pilot-config','--ignore-not-found','-o','name')
if (!$config) {
    $password = New-SecretValue
    $values = @{ POSTGRES_PASSWORD=$password; DATABASE_URL="postgresql+psycopg://pilot:$password@postgres/pilot"; GRAFANA_PASSWORD=(New-SecretValue); GITHUB_READ_TOKEN=[string]$env:GITHUB_READ_TOKEN }
    @{apiVersion='v1';kind='Secret';metadata=@{name='pilot-config'};stringData=$values} | ConvertTo-Json -Depth 5 | kubectl --context kind-releasepilot -n releasepilot-demo create -f -
    if ($LASTEXITCODE -ne 0) { throw 'Secret provisioning failed' }
}
foreach ($file in 'control','metrics','grafana') { Kube @('apply','-f',"infra/$file.yaml") }
foreach ($app in 'postgres','pilot-api','pilot-worker','demo-api','prometheus','grafana','traffic') {
    Kube @('rollout','status',"deployment/$app",'--timeout=240s')
}
Write-Output 'Ready. Provision operator accounts and port-forward the console as described in README.md.'
