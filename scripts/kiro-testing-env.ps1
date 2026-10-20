<#
  Agentic SDLC - testing environment for a Kiro window (Windows / PowerShell).

  Usage (from the repo root, in PowerShell - dot-source it so the vars persist):
      . .\scripts\kiro-testing-env.ps1
  Then launch Kiro FROM THIS SAME PowerShell session so child processes inherit
  these variables, and make sure the AIDLC_* names are approved under
  Settings -> "Mcp Approved Env Vars" (see docs/USER_GUIDE.md).

  Pulls all config + the six persona test-user credentials live from the
  CloudFormation stack outputs, so per-deploy values stay current.
  Nothing sensitive is hard-coded in this file.

  Requires: AWS CLI v2, Python 3.10+ (for the MCP venv), Windows PowerShell 5+ or
  PowerShell 7 on Windows 11 + Windows Terminal.
#>

# Uses whatever AWS_PROFILE is already set in your environment; if unset, the
# default AWS credential chain is used. If you use named profiles, run
# `$env:AWS_PROFILE = "<your-profile>"` before dot-sourcing this script.
if (-not $env:AWS_REGION)  { $env:AWS_REGION  = "us-east-1" }
$env:CDK_DEFAULT_REGION = $env:AWS_REGION
$env:AIDLC_AWS_REGION = $env:AWS_REGION   # required by the MCP server config

$RepoRoot = if ($env:AIDLC_REPO_ROOT) { $env:AIDLC_REPO_ROOT } else { (Get-Location).Path }
if (-not (Test-Path (Join-Path $RepoRoot "code\kiro-mcp"))) {
    Write-Error "Run this from the repo root (no code\kiro-mcp under $RepoRoot). cd into the repo, or set `$env:AIDLC_REPO_ROOT."
    return
}

$env:AIDLC_MCP_HOME   = Join-Path $RepoRoot "code\kiro-mcp"
$env:AIDLC_MCP_PYTHON = Join-Path $env:AIDLC_MCP_HOME ".venv\Scripts\python.exe"
$env:AIDLC_AUTH_MODE  = "password"
if (-not $env:AIDLC_STATE_DIR) { $env:AIDLC_STATE_DIR = Join-Path $RepoRoot ".kiro\.aidlc-state" }
New-Item -ItemType Directory -Force -Path $env:AIDLC_STATE_DIR | Out-Null

if (-not (Test-Path $env:AIDLC_MCP_PYTHON)) {
    Write-Warning "MCP venv missing at $($env:AIDLC_MCP_PYTHON)"
    Write-Warning "Install:  cd `"$($env:AIDLC_MCP_HOME)`"; python -m venv .venv; .venv\Scripts\python.exe -m pip install -e ."
}

Write-Host "Fetching stack outputs from AIDLCPlatformWorkshop ($($env:AWS_PROFILE) / $($env:AWS_REGION))..."
$raw = aws cloudformation describe-stacks --stack-name AIDLCPlatformWorkshop `
        --query "Stacks[0].Outputs" --output json --no-cli-pager 2>$null
if (-not $raw) {
    Write-Error "Could not read stack outputs. Refresh your AWS credentials and check AWS_PROFILE=$($env:AWS_PROFILE)."
    return
}

$o = @{}
($raw | ConvertFrom-Json) | ForEach-Object { $o[$_.OutputKey] = $_.OutputValue }

function Set-FromOut($var, $key) {
    if ($o.ContainsKey($key)) { Set-Item -Path "Env:$var" -Value $o[$key] }
}
Set-FromOut "AIDLC_PROJECT_API_URL"      "KiroProjectApiUrl"
Set-FromOut "AIDLC_COGNITO_USER_POOL_ID" "KiroCognitoUserPoolId"
Set-FromOut "AIDLC_COGNITO_CLIENT_ID"    "KiroCognitoClientId"
Set-FromOut "AIDLC_FILES_BUCKET"         "KiroFilesBucket"
Set-FromOut "AIDLC_BA_USERNAME"     "BATestUserEmail";     Set-FromOut "AIDLC_BA_PASSWORD"     "BATestUserPassword"
Set-FromOut "AIDLC_SA_USERNAME"     "SATestUserEmail";     Set-FromOut "AIDLC_SA_PASSWORD"     "SATestUserPassword"
Set-FromOut "AIDLC_DEV_USERNAME"    "TestUserEmail";       Set-FromOut "AIDLC_DEV_PASSWORD"    "TestUserPassword"
Set-FromOut "AIDLC_QA_USERNAME"     "QATestUserEmail";     Set-FromOut "AIDLC_QA_PASSWORD"     "QATestUserPassword"
Set-FromOut "AIDLC_DEVOPS_USERNAME" "DevOpsTestUserEmail"; Set-FromOut "AIDLC_DEVOPS_PASSWORD" "DevOpsTestUserPassword"
Set-FromOut "AIDLC_MAINT_USERNAME"  "MaintTestUserEmail";  Set-FromOut "AIDLC_MAINT_PASSWORD"  "MaintTestUserPassword"

Write-Host ""
Write-Host "Environment ready:"
Write-Host "  API      = $($env:AIDLC_PROJECT_API_URL)"
Write-Host "  POOL     = $($env:AIDLC_COGNITO_USER_POOL_ID)   CLIENT = $($env:AIDLC_COGNITO_CLIENT_ID)"
Write-Host "  BUCKET   = $($env:AIDLC_FILES_BUCKET)"
Write-Host "  personas = BA:$($env:AIDLC_BA_USERNAME) SA:$($env:AIDLC_SA_USERNAME) DEV:$($env:AIDLC_DEV_USERNAME) QA:$($env:AIDLC_QA_USERNAME) DEVOPS:$($env:AIDLC_DEVOPS_USERNAME) MAINT:$($env:AIDLC_MAINT_USERNAME)"
Write-Host "  (passwords set in env but not printed)"
Write-Host ""
Write-Host "Next:"
Write-Host "  1) Approve the AIDLC_* names under Settings -> 'Mcp Approved Env Vars'."
Write-Host "  2) Launch Kiro on this repo FROM THIS SAME PowerShell session:  kiro `"$RepoRoot`""
