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

function Wait-FixtureSignal {
  param([Parameter(Mandatory = $true)][string]$Path, [int]$TimeoutMilliseconds = 10000)
  $deadline = (Get-Date).AddMilliseconds($TimeoutMilliseconds)
  while (-not (Test-Path -LiteralPath $Path -PathType Leaf) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 50
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "concurrency fixture did not reach its locked checkpoint: $Path"
  }
}

function Start-ControllerFixtureProcess {
  param(
    [Parameter(Mandatory = $true)][string]$Runner,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$ProfileMode,
    [Parameter(Mandatory = $true)][string]$SignalPath,
    [string]$StatePath = $ProfilesPath
  )
  $arguments = ConvertTo-NativeArgumentString -Arguments @(
    "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Runner,
    "-ControllerPath", $controller, "-LocalAppData", $localAppData,
    "-CodexHome", $codexHome, "-Operation", $Operation,
    "-ProfileMode", $ProfileMode, "-SignalPath", $SignalPath,
    "-StatePath", $StatePath
  )
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = (Get-Command powershell.exe).Source
  $startInfo.Arguments = $arguments
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  [void]$process.Start()
  return [pscustomobject]@{ Process = $process }
}

function Complete-ControllerFixtureProcess {
  param([Parameter(Mandatory = $true)]$FixtureProcess, [int]$TimeoutMilliseconds = 60000)
  if (-not $FixtureProcess.Process.WaitForExit($TimeoutMilliseconds)) {
    $processId = $FixtureProcess.Process.Id
    Stop-Process -Id $FixtureProcess.Process.Id -Force -ErrorAction SilentlyContinue
    $FixtureProcess.Process.Dispose()
    throw "concurrency fixture timed out: PID $processId"
  }
  $FixtureProcess.Process.Refresh()
  $exitCode = $FixtureProcess.Process.ExitCode
  $FixtureProcess.Process.Dispose()
  if ($exitCode -ne 0) {
    throw "concurrency fixture failed with exit code $exitCode"
  }
}

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
  $legacyDeepSeek = Get-ModeProfile -State $state -ProfileMode deepseek
  Set-JsonProperty -Object $legacyDeepSeek -Name "name" -Value "FinalKit Windows DeepSeek API"
  Set-JsonProperty -Object $legacyCodex -Name "name" -Value "FinalKit Windows Codex Login"
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
  if ([int]$state.schema_version -ne 4) { throw "legacy profile schema was not migrated" }
  if ([string]$migratedCodex.model_opus -ne "gpt-5.6-sol") { throw "legacy Opus route was not preserved" }
  if ([string]$migratedCodex.model_sonnet -ne "gpt-5.6-terra") { throw "legacy shared Sonnet route was not split to Terra" }
  if ([string]$migratedCodex.model_haiku -ne "gpt-5.6-luna") { throw "legacy Haiku route was not preserved" }
  if ([string]$state.profiles.deepseek.name -ne "DeepSeek API") { throw "legacy DeepSeek display name was not shortened" }
  if ([string]$migratedCodex.name -ne "Codex Login") { throw "legacy Codex display name was not shortened" }
  foreach ($legacyName in @(
    "model_default", "model_fast", "reasoning_effort",
    "reasoning_effort_opus", "reasoning_effort_sonnet", "reasoning_effort_haiku"
  )) {
    if ($null -ne $migratedCodex.PSObject.Properties[$legacyName]) { throw "legacy Codex field survived: $legacyName" }
  }
  if (@(Get-ProfileReasoningChoices -ProfileMode kimi -Model "kimi-k3[1m]") -contains "none") {
    throw "Kimi K3 exposed unsupported Reasoning=none"
  }
  if (@(Get-ProfileReasoningChoices -ProfileMode kimi -Model "kimi-k2.6") -notcontains "none") {
    throw "Kimi K2.6 lost supported Reasoning=none"
  }
  if (@(Get-ProfileReasoningChoices -ProfileMode glm -Model "glm-5.3") -contains "none") {
    throw "GLM-5.3 exposed unsupported Reasoning=none"
  }
  if (@(Get-ProfileReasoningChoices -ProfileMode glm -Model "glm-5.3") -notcontains "max") {
    throw "GLM-5.3 lost supported Reasoning=max"
  }

  # Schema 3 used provider-wide lists.  Known incompatible pairs migrate to
  # auto without changing the selected model or unrelated routes.
  $legacyKimi = Get-ModeProfile -State $state -ProfileMode kimi
  $legacyGlm = Get-ModeProfile -State $state -ProfileMode glm
  Set-JsonProperty -Object $legacyKimi -Name "model_sonnet" -Value "kimi-k2.6"
  Set-JsonProperty -Object $legacyKimi -Name "reasoning_sonnet" -Value "high"
  Set-JsonProperty -Object $legacyGlm -Name "model_haiku" -Value "glm-4.7-flash"
  Set-JsonProperty -Object $legacyGlm -Name "reasoning_haiku" -Value "max"
  Set-JsonProperty -Object $state -Name "schema_version" -Value 3
  Write-AtomicJson -Path $ProfilesPath -Value $state
  $state = Get-WindowsClaudeState
  if ([int]$state.schema_version -ne 4) { throw "schema 3 model semantics were not migrated" }
  if ([string]$state.profiles.kimi.model_sonnet -ne "kimi-k2.6" -or [string]$state.profiles.kimi.reasoning_sonnet -ne "auto") {
    throw "schema 3 Kimi model/reasoning migration failed"
  }
  if ([string]$state.profiles.glm.model_haiku -ne "glm-4.7-flash" -or [string]$state.profiles.glm.reasoning_haiku -ne "auto") {
    throw "schema 3 GLM model/reasoning migration failed"
  }
  $kimiProfile = Get-ModeProfile -State $state -ProfileMode kimi
  Set-JsonProperty -Object $kimiProfile -Name "model_sonnet" -Value "kimi-k3[1m]"
  Set-JsonProperty -Object $kimiProfile -Name "reasoning_sonnet" -Value "none"
  $k3Rejected = $false
  try { Assert-StateShape -State $state } catch {
    if ($_.Exception.Message -match "Unsupported kimi sonnet Reasoning=none") {
      $k3Rejected = $true
    } else {
      throw
    }
  }
  if (-not $k3Rejected) { throw "profile validator accepted Kimi K3 Reasoning=none" }
  Set-JsonProperty -Object $kimiProfile -Name "reasoning_sonnet" -Value "auto"

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
  function Read-Host { param([string]$Prompt); return "auto" }
  try {
    $autoEffort = Read-CodexReasoningEffort -Role "Haiku" -Model "gpt-5.6-luna" -Preferred "max" -Catalog $catalog.Catalog
    if ($autoEffort -ne "auto") { throw "Codex auto reasoning was not preserved" }
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
  if (@($codexRuntime.supported_reasoning_haiku) -contains "ultra") {
    throw "Codex runtime advertised unsupported Luna Reasoning=ultra"
  }
  if (@($codexRuntime.supported_reasoning_haiku) -notcontains "xhigh") {
    throw "Codex runtime lost Luna model-specific reasoning capabilities"
  }
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

  # Configure and start are whole-controller transactions. Run real child
  # PowerShell processes against one isolated state root and deliberately hold
  # the first transaction after it has read/written shared state. Without the
  # exclusive controller lock these fixtures deterministically lose a profile
  # update or mix runtime config, credential, PID, and instance ownership.
  $glmSecret = Get-SecretPath -ProfileMode glm
  [IO.File]::Copy($secretPath, $glmSecret, $true)
  $runnerPath = Join-Path $testRoot "controller-concurrency-runner.ps1"
  $runnerText = @'
param(
  [Parameter(Mandatory = $true)][string]$ControllerPath,
  [Parameter(Mandatory = $true)][string]$LocalAppData,
  [Parameter(Mandatory = $true)][string]$CodexHome,
  [Parameter(Mandatory = $true)][ValidateSet("configure", "start")][string]$Operation,
  [Parameter(Mandatory = $true)][ValidateSet("deepseek", "glm")][string]$ProfileMode,
  [Parameter(Mandatory = $true)][string]$SignalPath,
  [Parameter(Mandatory = $true)][string]$StatePath
)
$ErrorActionPreference = "Stop"
$env:LOCALAPPDATA = $LocalAppData
$env:CODEX_HOME = $CodexHome
. $ControllerPath
if ($Operation -eq "configure") {
  $script:FixturePromptSignalled = $false
  function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    if ($AsSecureString) { return (New-Object Security.SecureString) }
    if (-not $script:FixturePromptSignalled) {
      [IO.File]::WriteAllText($SignalPath, "locked")
      $script:FixturePromptSignalled = $true
      if ($ProfileMode -eq "deepseek") { Start-Sleep -Milliseconds 2500 }
    }
    if ($Prompt -match '^(Opus|Sonnet|Haiku) Model') {
      return ("concurrent-{0}-{1}" -f $ProfileMode, $matches[1].ToLowerInvariant())
    }
    if ($Prompt -match 'Reasoning') { return "auto" }
    return ""
  }
  Configure-WindowsClaudeProfile -ProfileMode $ProfileMode
  exit 0
}

$state = Read-JsonObject -Path $StatePath
$script:FixturePythonPath = Get-WindowsPython
$script:FixturePythonSignalled = $false
function Get-WindowsPython {
  if (-not $script:FixturePythonSignalled) {
    [IO.File]::WriteAllText($SignalPath, "locked")
    $script:FixturePythonSignalled = $true
    if ($ProfileMode -eq "deepseek") { Start-Sleep -Milliseconds 2500 }
  }
  return $script:FixturePythonPath
}
[void](Start-WindowsClaudeGateway -State $state -ProfileMode $ProfileMode)
'@
  [IO.File]::WriteAllText($runnerPath, $runnerText, (New-Object Text.UTF8Encoding($false)))

  $configureSignalA = Join-Path $testRoot "configure-deepseek.locked"
  $configureSignalB = Join-Path $testRoot "configure-glm.locked"
  $configureA = Start-ControllerFixtureProcess -Runner $runnerPath -Operation configure -ProfileMode deepseek -SignalPath $configureSignalA
  Wait-FixtureSignal -Path $configureSignalA
  $configureB = Start-ControllerFixtureProcess -Runner $runnerPath -Operation configure -ProfileMode glm -SignalPath $configureSignalB
  Complete-ControllerFixtureProcess -FixtureProcess $configureA
  Complete-ControllerFixtureProcess -FixtureProcess $configureB
  $concurrentState = Get-WindowsClaudeState
  if ([string]$concurrentState.profiles.deepseek.model_opus -ne "concurrent-deepseek-opus") {
    throw "concurrent DeepSeek configure commit was lost"
  }
  if ([string]$concurrentState.profiles.glm.model_opus -ne "concurrent-glm-opus") {
    throw "concurrent GLM configure commit was lost"
  }

  Set-JsonProperty -Object $concurrentState -Name "port" -Value $testPort
  $concurrentStatePath = Join-Path $testRoot "concurrent-runtime-state.json"
  Write-AtomicJson -Path $concurrentStatePath -Value $concurrentState
  $startSignalA = Join-Path $testRoot "start-deepseek.locked"
  $startSignalB = Join-Path $testRoot "start-glm.locked"
  $startA = Start-ControllerFixtureProcess -Runner $runnerPath -Operation start -ProfileMode deepseek -SignalPath $startSignalA -StatePath $concurrentStatePath
  Wait-FixtureSignal -Path $startSignalA
  $startB = Start-ControllerFixtureProcess -Runner $runnerPath -Operation start -ProfileMode glm -SignalPath $startSignalB -StatePath $concurrentStatePath
  Complete-ControllerFixtureProcess -FixtureProcess $startA
  Complete-ControllerFixtureProcess -FixtureProcess $startB
  $concurrentGatewayState = Read-JsonObject -Path $GatewayStatePath
  $concurrentRuntime = Read-JsonObject -Path $RuntimeConfigPath
  if (
    [string]$concurrentGatewayState.profile -ne "glm" -or
    [string]$concurrentRuntime.profile -ne "glm" -or
    [string]$concurrentGatewayState.instance_id -ne [string]$concurrentRuntime.instance_id -or
    -not (Test-GatewayProcessIdentity -ProcessId ([int]$concurrentGatewayState.pid)) -or
    -not (Get-GatewayHealth -RuntimeConfig $concurrentRuntime)
  ) {
    throw "simultaneous gateway starts produced a profile/PID/config/health mismatch"
  }
  $ownedGatewayProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $commandLine = [string]$_.CommandLine
    $commandLine.IndexOf($GatewayScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $commandLine.IndexOf($RuntimeConfigPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
  })
  if ($ownedGatewayProcesses.Count -ne 1) {
    throw "simultaneous gateway starts left $($ownedGatewayProcesses.Count) owned processes instead of one"
  }
  Stop-WindowsClaudeGateway

  # A clean Windows host without Python must fail before init/config mutation,
  # while status remains read-only and prints a deterministic prerequisite
  # diagnosis instead of waiting until gateway start.
  $pythonFunction = (Get-Item -LiteralPath Function:\Get-WindowsPython).ScriptBlock
  $pythonInfoFunction = (Get-Item -LiteralPath Function:\Get-WindowsPythonInfo).ScriptBlock
  $profilesBeforeMissingPython = [IO.File]::ReadAllBytes($ProfilesPath)
  Set-Item -LiteralPath Function:\Get-WindowsPython -Value { throw "fixture: Windows Python 3.10+ was not found" }
  Set-Item -LiteralPath Function:\Get-WindowsPythonInfo -Value { throw "fixture: Windows Python 3.10+ was not found" }
  try {
    $missingPythonRejected = $false
    try { Initialize-WindowsClaude } catch {
      if ($_.Exception.Message -match "Windows Python 3.10\+") { $missingPythonRejected = $true } else { throw }
    }
    if (-not $missingPythonRejected) { throw "Windows Claude init accepted a missing Python runtime" }
    if (-not [Linq.Enumerable]::SequenceEqual($profilesBeforeMissingPython, [IO.File]::ReadAllBytes($ProfilesPath))) {
      throw "missing-Python prerequisite check mutated profiles.json"
    }
    $statusWithoutPython = (& { Show-WindowsClaudeStatus } 6>&1 | Out-String)
    if ($statusWithoutPython -notmatch "Windows Python 3.10\+: missing") {
      throw "Windows Claude status did not expose the missing Python prerequisite"
    }
  } finally {
    Set-Item -LiteralPath Function:\Get-WindowsPython -Value $pythonFunction
    Set-Item -LiteralPath Function:\Get-WindowsPythonInfo -Value $pythonInfoFunction
  }
  Write-Output "WINDOWS_CLAUDE_CONTROLLER_CONTRACT_OK dpapi=roundtrip codex=CODEX_HOME+three-routes+reasoning-catalog+migration+no-copy concurrency=configure+start-serialized json=no-key port=18987 process=start-stop python=early-diagnostic wsl=none"
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
