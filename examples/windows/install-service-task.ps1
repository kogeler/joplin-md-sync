[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,

    [Parameter(Mandatory = $true)]
    [string]$JoplinTokenFile,

    [Parameter(Mandatory = $true)]
    [string]$GptActionsTokenFile,

    [string]$McpAuthTokenFile = "",
    [int]$Port = 8765,
    [string]$TaskName = "joplin-md-sync"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Executable not found: $Executable"
}
if (-not (Test-Path -LiteralPath $JoplinTokenFile -PathType Leaf)) {
    throw "Joplin token file not found: $JoplinTokenFile"
}
if (-not (Test-Path -LiteralPath $GptActionsTokenFile -PathType Leaf)) {
    throw "GPT Actions token file not found: $GptActionsTokenFile"
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535"
}
if ($McpAuthTokenFile -and -not (Test-Path -LiteralPath $McpAuthTokenFile -PathType Leaf)) {
    throw "MCP auth token file not found: $McpAuthTokenFile"
}

function Quote-TaskArgument([string]$Value) {
    return '"' + $Value.Replace('"', '\"') + '"'
}

$arguments = @(
    "mcp",
    "serve",
    "--token-file",
    (Quote-TaskArgument $JoplinTokenFile),
    "--mcp-port",
    $Port,
    "--gpt-actions",
    "--gpt-actions-token-file",
    (Quote-TaskArgument $GptActionsTokenFile)
)
if ($McpAuthTokenFile) {
    $arguments += @("--auth-token-file", (Quote-TaskArgument $McpAuthTokenFile))
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction `
    -Execute $Executable `
    -Argument ($arguments -join " ") `
    -WorkingDirectory $env:USERPROFILE
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

if ($PSCmdlet.ShouldProcess($TaskName, "Register per-user MCP and GPT Actions service task")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Joplin Markdown Sync MCP and GPT Actions service" `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Registered and started task '$TaskName'."
}
