[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$ControllerPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$controller = (Resolve-Path -LiteralPath $ControllerPath).Path
$tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$testRoot = Join-Path $tempParent ("finalkit-windows-claude-controller-" + [guid]::NewGuid().ToString("N"))
$localAppData = Join-Path $testRoot "LocalAppData"
$plain = $null
$cipher = $null
$secureKey = $null
$generatedKey = "offline-" + [guid]::NewGuid().ToString("N")
$previousLocalAppData = $env:LOCALAPPDATA
$previousCodexHome = $env:CODEX_HOME

try {
  New-Item -ItemType Directory -Path $localAppData -Force | Out-Null
  $codexHome = Join-Path $testRoot "CodexHome"
  New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
  $env:LOCALAPPDATA = $localAppData
  $env:CODEX_HOME = $codexHome
  . $controller
  Write-AtomicJson -Path (Join-Path $codexHome "auth.json") -Value ([pscustomobject]@{
    auth_mode = "chatgpt"
    OPENAI_API_KEY = $null
    tokens = [pscustomobject]@{
      access_token = "offline-access"
      refresh_token = "offline-refresh"
      id_token = "offline-id"
      account_id = "offline-account"
    }
  })
  Write-AtomicJson -Path (Join-Path $codexHome "models_cache.json") -Value ([pscustomobject]@{
    models = @(
      [pscustomobject]@{
        slug = "gpt-5.6-sol"; visibility = "list"; default_reasoning_level = "low"
        supported_reasoning_levels = @(
          [pscustomobject]@{ effort = "low"; description = "Fast responses with light reasoning" },
          [pscustomobject]@{ effort = "medium"; description = "Balanced reasoning" },
          [pscustomobject]@{ effort = "high"; description = "Deeper reasoning" },
          [pscustomobject]@{ effort = "xhigh"; description = "Very deep reasoning" },
          [pscustomobject]@{ effort = "max"; description = "Maximum standard reasoning" },
          [pscustomobject]@{ effort = "ultra"; description = "Ultra reasoning" }
        )
      },
      [pscustomobject]@{
        slug = "gpt-5.6-terra"; visibility = "list"; default_reasoning_level = "medium"
        supported_reasoning_levels = @(
          [pscustomobject]@{ effort = "low"; description = "Fast" },
          [pscustomobject]@{ effort = "medium"; description = "Balanced" },
          [pscustomobject]@{ effort = "high"; description = "Deep" },
          [pscustomobject]@{ effort = "xhigh"; description = "Very deep" },
          [pscustomobject]@{ effort = "max"; description = "Maximum" },
          [pscustomobject]@{ effort = "ultra"; description = "Ultra" }
        )
      },
      [pscustomobject]@{
        slug = "gpt-5.6-luna"; visibility = "list"; default_reasoning_level = "medium"
        supported_reasoning_levels = @(
          [pscustomobject]@{ effort = "low"; description = "Fast" },
          [pscustomobject]@{ effort = "medium"; description = "Balanced" },
          [pscustomobject]@{ effort = "high"; description = "Deep" },
          [pscustomobject]@{ effort = "xhigh"; description = "Very deep" },
          [pscustomobject]@{ effort = "max"; description = "Maximum" }
        )
      },
      [pscustomobject]@{ slug = "hidden-model"; visibility = "hide" }
    )
  })
  $codexConfigBytes = [Text.Encoding]::UTF8.GetBytes("model = `"gpt-5.6-sol`"`nmodel_reasoning_effort = `"max`"`n")
  try {
    Write-AtomicBytes -Path (Join-Path $codexHome "config.toml") -Bytes $codexConfigBytes
  } finally {
    [Array]::Clear($codexConfigBytes, 0, $codexConfigBytes.Length)
  }
  $state = Initialize-WindowsClaudeState
  $legacyCodex = Get-ModeProfile -State $state -ProfileMode codex
  Set-JsonProperty -Object $legacyCodex -Name "model_default" -Value "gpt-5.6-sol"
  Set-JsonProperty -Object $legacyCodex -Name "model_fast" -Value "gpt-5.6-luna"
  Set-JsonProperty -Object $legacyCodex -Name "reasoning_effort" -Value "max"
  foreach ($name in @(
    "model_opus", "model_sonnet", "model_haiku",
    "reasoning_opus", "reasoning_sonnet", "reasoning_haiku"
  )) {
    Remove-JsonProperty -Object $legacyCodex -Name $name
  }
  Set-JsonProperty -Object $state -Name "schema_version" -Value 1
  Write-AtomicJson -Path $ProfilesPath -Value $state
  $state = Get-WindowsClaudeState
  $migratedCodex = Get-ModeProfile -State $state -ProfileMode codex
  if ([int]$state.schema_version -ne 3) { throw "legacy profile schema was not migrated" }
  if ([string]$migratedCodex.model_opus -ne "gpt-5.6-sol") { throw "legacy Opus route was not preserved" }
  if ([string]$migratedCodex.model_sonnet -ne "gpt-5.6-terra") { throw "legacy shared Sonnet route was not split to Terra" }
  if ([string]$migratedCodex.model_haiku -ne "gpt-5.6-luna") { throw "legacy Haiku route was not preserved" }
  foreach ($legacyName in @(
    "model_default", "model_fast", "reasoning_effort",
    "reasoning_effort_opus", "reasoning_effort_sonnet", "reasoning_effort_haiku"
  )) {
    if ($null -ne $migratedCodex.PSObject.Properties[$legacyName]) { throw "legacy Codex field survived: $legacyName" }
  }
  $profile = Get-ModeProfile -State $state -ProfileMode deepseek
  Set-JsonProperty -Object $profile -Name "model_opus" -Value "offline-opus"
  Set-JsonProperty -Object $profile -Name "reasoning_opus" -Value "max"
  Set-JsonProperty -Object $profile -Name "model_sonnet" -Value "offline-sonnet"
  Set-JsonProperty -Object $profile -Name "reasoning_sonnet" -Value "high"
  Set-JsonProperty -Object $profile -Name "model_haiku" -Value "offline-haiku"
  Set-JsonProperty -Object $profile -Name "reasoning_haiku" -Value "none"
  $secureKey = ConvertTo-SecureString -String $generatedKey -AsPlainText -Force
  $secretPath = Get-SecretPath -ProfileMode deepseek
  Protect-ApiKey -SecureKey $secureKey -Path $secretPath
  Assert-StateShape -State $state
  Write-AtomicJson -Path $ProfilesPath -Value $state
  Protect-StateRootAcl

  $script:codexConfigureAnswers = [Collections.Generic.Queue[string]]::new()
  $script:codexConfigureAnswers.Enqueue("")
  $script:codexConfigureAnswers.Enqueue("")
  $script:codexConfigureAnswers.Enqueue("")
  $script:codexConfigureAnswers.Enqueue("")
  $script:codexConfigureAnswers.Enqueue("")
  $script:codexConfigureAnswers.Enqueue("")
  function Invoke-WindowsCodexLoginStatus { }
  function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    if ($AsSecureString) { throw "Codex configure unexpectedly requested an API key" }
    if ($script:codexConfigureAnswers.Count -eq 0) { throw "Codex configure requested an unexpected value: $Prompt" }
    return $script:codexConfigureAnswers.Dequeue()
  }
  try {
    Configure-WindowsClaudeProfile -ProfileMode codex
  } finally {
    Remove-Item -LiteralPath Function:\Read-Host -Force
  }
  if ($script:codexConfigureAnswers.Count -ne 0) { throw "Codex configure did not consume the model/effort prompts" }
  $state = Read-JsonObject -Path $ProfilesPath
  $configuredCodex = Get-ModeProfile -State $state -ProfileMode codex
  if ([string]$configuredCodex.model_opus -ne "gpt-5.6-sol") { throw "Codex Opus model did not accept its Windows-local default" }
  if ([string]$configuredCodex.model_sonnet -ne "gpt-5.6-terra") { throw "Codex Sonnet model is not independently selectable" }
  if ([string]$configuredCodex.model_haiku -ne "gpt-5.6-luna") { throw "Codex Haiku model did not accept its Windows-local default" }
  foreach ($name in @("reasoning_opus", "reasoning_sonnet", "reasoning_haiku")) {
    if ([string]$configuredCodex.$name -ne "max") { throw "Codex per-role effort did not follow the supported Windows setting: $name" }
  }
  $catalog = Get-WindowsCodexModelDefaults
  $luna = Get-CodexModelCatalogEntry -Catalog $catalog.Catalog -Model "gpt-5.6-luna"
  if (@($luna.SupportedReasoning | ForEach-Object { $_.Effort }) -contains "ultra") {
    throw "Luna fixture unexpectedly exposes ultra"
  }
  function Read-Host { param([string]$Prompt); return "ultra" }
  try {
    $unsupportedRejected = $false
    try {
      [void](Read-CodexReasoningEffort -Role "Haiku" -Model "gpt-5.6-luna" -Preferred "max" -Catalog $catalog.Catalog)
    } catch {
      if ($_.Exception.Message -match "does not support Reasoning=ultra") { $unsupportedRejected = $true } else { throw }
    }
    if (-not $unsupportedRejected) { throw "unsupported Luna reasoning was accepted" }
  } finally {
    Remove-Item -LiteralPath Function:\Read-Host -Force
  }
  if (-not (Test-ProfileConfigured -State $state -ProfileMode codex)) {
    throw "Codex profile did not accept the isolated Windows Codex auth fixture"
  }
  $portProbe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  try {
    $portProbe.Start()
    $testPort = [int]$portProbe.LocalEndpoint.Port
  } finally {
    $portProbe.Stop()
  }
  Set-JsonProperty -Object $state -Name "port" -Value $testPort
  $codexRuntime = Start-WindowsClaudeGateway -State $state -ProfileMode codex
  if ($codexRuntime.auth_style -ne "codex-cli") { throw "Codex runtime auth style mismatch" }
  if ([int]$codexRuntime.schema_version -ne 3) { throw "Codex runtime schema mismatch" }
  if ($codexRuntime.model_sonnet -ne "gpt-5.6-terra") { throw "Codex runtime collapsed Sonnet into Opus" }
  if ($codexRuntime.reasoning_haiku -ne "max") { throw "Codex runtime lost Haiku reasoning" }
  if ($codexRuntime.codex_auth_file -ne (Join-Path $codexHome "auth.json")) {
    throw "Codex runtime did not use CODEX_HOME"
  }
  $codexRuntimeText = Get-Content -LiteralPath $RuntimeConfigPath -Raw -Encoding UTF8
  if ($codexRuntimeText.Contains("offline-access") -or $codexRuntimeText.Contains("offline-refresh")) {
    throw "Codex token leaked into runtime JSON"
  }
  $codexHealth = Get-GatewayHealth -RuntimeConfig $codexRuntime
  if (-not $codexHealth -or $codexHealth.auth_owner -ne "windows-codex-cli") {
    throw "Codex gateway auth owner mismatch"
  }
  Stop-WindowsClaudeGateway

  if (-not (Test-Path -LiteralPath $ProfilesPath -PathType Leaf)) { throw "profiles.json was not created" }
  if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) { throw "DPAPI file was not created" }
  $readback = Get-Content -LiteralPath $ProfilesPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($readback.port -ne 18987) { throw "unexpected Windows-only port" }
  if ($readback.profiles.deepseek.model_opus -ne "offline-opus") { throw "Opus Model was not saved" }
  if ($readback.profiles.deepseek.model_sonnet -ne "offline-sonnet") { throw "Sonnet Model was not saved" }
  if ($readback.profiles.deepseek.model_haiku -ne "offline-haiku") { throw "Haiku Model was not saved" }
  if ($readback.profiles.deepseek.reasoning_sonnet -ne "high") { throw "Sonnet Reasoning was not saved" }
  if ($null -ne $readback.profiles.deepseek.PSObject.Properties["api_key"]) { throw "API key field leaked into JSON" }
  $stateText = Get-Content -LiteralPath $ProfilesPath -Raw -Encoding UTF8
  if ($stateText.Contains($generatedKey)) { throw "API key leaked into profiles.json" }
  if ($stateText -match '(?i)(\\\\wsl|/mnt/|/home/)') { throw "Windows profile state references WSL" }

  Add-Type -AssemblyName System.Security -ErrorAction SilentlyContinue
  $cipher = [IO.File]::ReadAllBytes($secretPath)
  if ([Text.Encoding]::UTF8.GetString($cipher).Contains($generatedKey)) { throw "DPAPI file contains plaintext" }
  $entropy = [Text.Encoding]::UTF8.GetBytes("ScienceCodexFinalKit/WindowsClaude/DPAPI/v1")
  $plain = Unprotect-ApiKeyBytes -Path $secretPath
  if ([Text.Encoding]::UTF8.GetString($plain) -ne $generatedKey) { throw "DPAPI roundtrip mismatch" }
  $runtime = Start-WindowsClaudeGateway -State $state -ProfileMode deepseek
  if ($runtime.profile -ne "deepseek" -or $runtime.port -ne $testPort) { throw "gateway runtime mismatch" }
  if (-not (Test-Path -LiteralPath $GatewayStatePath -PathType Leaf)) { throw "gateway state missing" }
  $health = Get-GatewayHealth -RuntimeConfig $runtime
  if (-not $health -or $health.owner -ne "ScienceCodexFinalKit-WindowsClaude") {
    throw "gateway health identity mismatch"
  }
  Stop-WindowsClaudeGateway
  if (Test-Path -LiteralPath $GatewayStatePath -PathType Leaf) { throw "gateway state survived stop" }
  Write-Output "WINDOWS_CLAUDE_CONTROLLER_CONTRACT_OK dpapi=roundtrip codex=CODEX_HOME+three-routes+reasoning-catalog+migration+no-copy json=no-key port=18987 process=start-stop wsl=none"
} finally {
  if (Get-Command Stop-WindowsClaudeGateway -ErrorAction SilentlyContinue) {
    try { Stop-WindowsClaudeGateway } catch { }
  }
  $env:LOCALAPPDATA = $previousLocalAppData
  $env:CODEX_HOME = $previousCodexHome
  if ($secureKey) { $secureKey.Dispose() }
  if ($plain) { [Array]::Clear($plain, 0, $plain.Length) }
  if ($cipher) { [Array]::Clear($cipher, 0, $cipher.Length) }
  if (Test-Path -LiteralPath $testRoot -PathType Container) {
    $resolved = (Resolve-Path -LiteralPath $testRoot).Path
    $expectedPrefix = $tempParent + "\finalkit-windows-claude-controller-"
    if (-not $resolved.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
      throw "refusing to remove unexpected controller test path: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
  }
}
