[CmdletBinding()]
param(
  [ValidateSet("help", "menu", "init", "configure", "start", "status", "stop", "official")]
  [string]$Action = "help",

  [ValidateSet("", "deepseek", "kimi", "glm", "codex")]
  [string]$Mode = "",

  [switch]$NoLaunch,
  [switch]$Force,
  [switch]$NoBackup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$KitRoot = Split-Path -Parent $PSScriptRoot
$RuntimeSource = Join-Path $PSScriptRoot "runtime"
$TemplatePath = Join-Path $RuntimeSource "windows_claude_profiles.template.json"
$GatewayScript = Join-Path $RuntimeSource "windows_claude_gateway.py"
$StateRoot = Join-Path $env:LOCALAPPDATA "ScienceCodexFinalKit\WindowsClaude"
$ProfilesPath = Join-Path $StateRoot "profiles.json"
$SecretsRoot = Join-Path $StateRoot "secrets"
$RuntimeRoot = Join-Path $StateRoot "runtime"
$LogsRoot = Join-Path $StateRoot "logs"
$GatewayStatePath = Join-Path $RuntimeRoot "gateway-state.json"
$RuntimeConfigPath = Join-Path $RuntimeRoot "gateway-runtime.json"
$ControllerLockPath = Join-Path $StateRoot "controller.lock"
$script:WindowsClaudeMutationLockDepth = 0
$script:WindowsClaudeMutationLockStream = $null
$script:Modes = @("deepseek", "kimi", "glm", "codex")
$script:Roles = @("opus", "sonnet", "haiku")
$script:ReasoningChoices = @{
  deepseek = @("auto", "none", "high", "max")
  kimi = @("auto", "none", "low", "high", "max")
  glm = @("auto", "none", "low", "medium", "high", "xhigh", "max")
  codex = @("auto", "none", "low", "medium", "high", "xhigh", "max", "ultra")
}

function Get-ProfileReasoningChoices {
  param(
    [Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode,
    [Parameter(Mandatory = $true)][string]$Model
  )
  $modelValue = $Model.Trim().ToLowerInvariant()
  if ($ProfileMode -eq "deepseek") { return @($script:ReasoningChoices.deepseek) }
  if ($ProfileMode -eq "kimi") {
    if ($modelValue -match '^kimi-k3(?:\[[a-z0-9._+-]+\])?$') { return @("auto", "low", "high", "max") }
    if ($modelValue -match '^kimi-k2\.(?:5|6)(?:\[[a-z0-9._+-]+\])?$') { return @("auto", "none") }
    if ($modelValue -match '^kimi-k2\.7-code(?:-highspeed)?(?:\[[a-z0-9._+-]+\])?$') { return @("auto") }
    return @("auto")
  }
  if ($ProfileMode -eq "glm") {
    if ($modelValue -match '^glm-5\.3(?:[-._][a-z0-9]+)*$') {
      return @("auto", "low", "high", "max")
    }
    if ($modelValue -match '^glm-5\.2(?:[-._][a-z0-9]+)*$') {
      return @("auto", "none", "low", "medium", "high", "xhigh", "max")
    }
    if ($modelValue -match '^glm-4\.(?:[5-9])(?:[-._][a-z0-9]+)*$') {
      return @("auto", "none")
    }
    return @("auto")
  }
  return @($script:ReasoningChoices.codex)
}
$script:ProviderModels = @{
  deepseek = @("deepseek-v4-pro", "deepseek-v4-flash")
  kimi = @("kimi-k3[1m]", "kimi-k2.6")
  glm = @("glm-5.2", "glm-4.7-flash")
}
$script:ProfileIds = @{
  deepseek = "c20f9d40-9b8e-5bc3-a3b1-1b326edd8a63"
  kimi = "73af0626-0caa-56fb-87f3-e87eb120235e"
  glm = "84e1ab3e-593c-5fc9-a078-5757f400fba7"
  codex = "642becc7-c4ca-52ec-8c7d-7e66a1c56023"
}
$script:Entropy = [Text.Encoding]::UTF8.GetBytes("ScienceCodexFinalKit/WindowsClaude/DPAPI/v1")

function Invoke-WithWindowsClaudeMutationLock {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][scriptblock]$Body,
    [ValidateRange(1, 300000)][int]$TimeoutMilliseconds = 30000
  )
  if ($script:WindowsClaudeMutationLockDepth -gt 0) {
    $script:WindowsClaudeMutationLockDepth++
    try { & $Body } finally { $script:WindowsClaudeMutationLockDepth-- }
    return
  }

  if (-not (Test-Path -LiteralPath $StateRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
  }
  $stopwatch = [Diagnostics.Stopwatch]::StartNew()
  $lockStream = $null
  while ($null -eq $lockStream) {
    try {
      $lockStream = [IO.File]::Open(
        $ControllerLockPath,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
      )
    } catch [IO.IOException] {
      if ($stopwatch.ElapsedMilliseconds -ge $TimeoutMilliseconds) {
        throw "Another Windows Claude controller mutation is active; wait for it to finish, then retry"
      }
      Start-Sleep -Milliseconds 100
    }
  }
  $script:WindowsClaudeMutationLockStream = $lockStream
  $script:WindowsClaudeMutationLockDepth = 1
  try {
    & $Body
  } finally {
    $script:WindowsClaudeMutationLockDepth = 0
    $script:WindowsClaudeMutationLockStream = $null
    $lockStream.Dispose()
  }
}

function Write-AtomicBytes {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes
  )
  $parent = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  $temporary = Join-Path $parent (".{0}.{1}.tmp" -f (Split-Path -Leaf $Path), [guid]::NewGuid().ToString("N"))
  $replacementBackup = Join-Path $parent (".{0}.{1}.replace-backup" -f (Split-Path -Leaf $Path), [guid]::NewGuid().ToString("N"))
  try {
    [IO.File]::WriteAllBytes($temporary, $Bytes)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      [IO.File]::Replace($temporary, $Path, $replacementBackup, $true)
      Remove-Item -LiteralPath $replacementBackup -Force
    } else {
      [IO.File]::Move($temporary, $Path)
    }
  } finally {
    if (Test-Path -LiteralPath $temporary -PathType Leaf) { Remove-Item -LiteralPath $temporary -Force }
    if (Test-Path -LiteralPath $replacementBackup -PathType Leaf) { Remove-Item -LiteralPath $replacementBackup -Force }
  }
}

function Write-AtomicJson {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  $json = ($Value | ConvertTo-Json -Depth 32) + "`n"
  $encoding = New-Object Text.UTF8Encoding($false)
  Write-AtomicBytes -Path $Path -Bytes $encoding.GetBytes($json)
}

function Read-JsonObject {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$AllowMissing
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    if ($AllowMissing) { return [pscustomobject]@{} }
    throw "JSON file does not exist: $Path"
  }
  try {
    $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    throw "JSON is invalid; refusing to overwrite it: $Path"
  }
  if ($null -eq $value -or $value -isnot [pscustomobject]) {
    throw "JSON root must be an object: $Path"
  }
  return $value
}

function Set-JsonProperty {
  param(
    [Parameter(Mandatory = $true)][pscustomobject]$Object,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyString()]$Value
  )
  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) {
    $Object | Add-Member -MemberType NoteProperty -Name $Name -Value $Value
  } else {
    $property.Value = $Value
  }
}

function Get-OptionalJsonString {
  param(
    [Parameter(Mandatory = $true)]$Object,
    [Parameter(Mandatory = $true)][string]$Name
  )
  if ($null -eq $Object) { return "" }
  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property -or $null -eq $property.Value) { return "" }
  return [string]$property.Value
}

function Remove-JsonProperty {
  param(
    [Parameter(Mandatory = $true)][pscustomobject]$Object,
    [Parameter(Mandatory = $true)][string]$Name
  )
  if ($null -ne $Object.PSObject.Properties[$Name]) {
    $Object.PSObject.Properties.Remove($Name)
  }
}

function Copy-JsonValue {
  param([Parameter(Mandatory = $true)][AllowNull()][AllowEmptyString()]$Value)
  return (($Value | ConvertTo-Json -Depth 32) | ConvertFrom-Json)
}

function New-UrlSecret {
  $bytes = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  try {
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
  } finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
  }
}

function Assert-WindowsOnlyValue {
  param(
    [Parameter(Mandatory = $true)][string]$Value,
    [Parameter(Mandatory = $true)][string]$Label
  )
  if ($Value -match '(?i)(\\\\wsl\$|\\\\wsl\.localhost|/mnt/|/home/|wsl\.exe)') {
    throw "$Label cannot reference WSL"
  }
  try { $uri = [uri]$Value } catch { throw "$Label must be an absolute URL" }
  if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @("http", "https")) {
    throw "$Label must be an absolute HTTP(S) URL"
  }
  if ($uri.UserInfo -or $uri.Query -or $uri.Fragment) {
    throw "$Label cannot contain credentials, query, or fragment"
  }
  if ($uri.Scheme -eq "http" -and $uri.Host -notin @("127.0.0.1", "localhost", "::1")) {
    throw "$Label must use HTTPS unless it is a Windows loopback endpoint"
  }
  if ($uri.Host -in @("127.0.0.1", "localhost", "::1") -and $uri.Port -in @(9876, 18987)) {
    throw "$Label cannot use a reserved Switchboard gateway port"
  }
}

function Get-ModeProfile {
  param(
    [Parameter(Mandatory = $true)]$State,
    [Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode
  )
  $property = $State.profiles.PSObject.Properties[$ProfileMode]
  if ($null -eq $property -or $null -eq $property.Value) { throw "Missing Windows Claude profile: $ProfileMode" }
  return $property.Value
}

function Get-SecretPath {
  param([Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode)
  return Join-Path $SecretsRoot "$ProfileMode.dpapi"
}

function Protect-StateRootAcl {
  if (-not (Test-Path -LiteralPath $StateRoot -PathType Container)) { return }
  try {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    # Apply the inheritable rule to the root itself.  Do not combine /t with
    # /inheritance:r: doing so disables inheritance on every existing file and
    # leaves an (OI)(CI)-only rule that does not grant access to that file.
    & icacls.exe $StateRoot /inheritance:r /grant:r "*$($sid):(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls exit $LASTEXITCODE" }
    $children = Join-Path $StateRoot "*"
    & icacls.exe $children /inheritance:e /t /c | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls child inheritance exit $LASTEXITCODE" }
  } catch {
    Write-Warning "Could not tighten WindowsClaude ACL; DPAPI still protects provider API keys: $($_.Exception.Message)"
  }
}

function Assert-StateShape {
  param([Parameter(Mandatory = $true)]$State)
  if ($State.schema_version -ne 4) { throw "Unsupported Windows Claude profile schema" }
  if ($State.host -ne "127.0.0.1") { throw "Windows Claude host must remain 127.0.0.1" }
  if ([int]$State.port -ne 18987) { throw "Windows Claude port must remain the isolated port 18987" }
  foreach ($profileMode in $script:Modes) {
    $profile = Get-ModeProfile -State $State -ProfileMode $profileMode
    if ([string]$profile.profile_id -ne $script:ProfileIds[$profileMode]) {
      throw "Unexpected profile ID for $profileMode"
    }
    $expectedProtocol = if ($profileMode -eq "codex") { "openai-responses" } else { "anthropic-messages" }
    if ([string]$profile.protocol -ne $expectedProtocol) { throw "Unexpected protocol for $profileMode" }
    $expectedAuth = if ($profileMode -eq "codex") { "codex-cli" } elseif ($profileMode -eq "kimi") { "bearer" } else { "x-api-key" }
    if ([string]$profile.auth_style -ne $expectedAuth) { throw "Unexpected auth style for $profileMode" }
    foreach ($name in @("path_secret", "client_token")) {
      $value = [string]$profile.$name
      if ($value -notmatch '^[A-Za-z0-9_-]{32,256}$') { throw "Malformed $name for $profileMode" }
    }
    Assert-WindowsOnlyValue -Value ([string]$profile.upstream) -Label "$profileMode upstream"
    if ($profileMode -eq "codex" -and [string]$profile.upstream -ne "https://chatgpt.com/backend-api/codex") {
      throw "Windows Codex login must use the official ChatGPT Codex backend"
    }
    foreach ($role in $script:Roles) {
      $model = Get-OptionalJsonString -Object $profile -Name "model_$role"
      $reasoning = Get-OptionalJsonString -Object $profile -Name "reasoning_$role"
      if ([string]::IsNullOrWhiteSpace($model)) { throw "Missing $profileMode $role Model" }
      $reasoningChoices = @(Get-ProfileReasoningChoices -ProfileMode $profileMode -Model $model)
      if ($reasoning -notin $reasoningChoices) {
        throw "Unsupported $profileMode $role Reasoning=$reasoning"
      }
    }
    foreach ($legacyName in @(
      "model_default", "model_fast", "reasoning_effort",
      "reasoning_effort_opus", "reasoning_effort_sonnet", "reasoning_effort_haiku"
    )) {
      if ($null -ne $profile.PSObject.Properties[$legacyName]) {
        throw "Legacy route field survived schema migration: $profileMode $legacyName"
      }
    }
  }
}

function Initialize-WindowsClaudeState {
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body { Initialize-WindowsClaudeState })
  }
  if (-not (Test-Path -LiteralPath $TemplatePath -PathType Leaf)) { throw "Missing profile template: $TemplatePath" }
  if (-not (Test-Path -LiteralPath $GatewayScript -PathType Leaf)) { throw "Missing Windows gateway: $GatewayScript" }
  foreach ($directory in @($StateRoot, $SecretsRoot, $RuntimeRoot, $LogsRoot)) {
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
      New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
  }
  $template = Read-JsonObject -Path $TemplatePath
  $changed = $false
  if (Test-Path -LiteralPath $ProfilesPath -PathType Leaf) {
    $state = Read-JsonObject -Path $ProfilesPath
    if ([int]$state.schema_version -notin @(1, 2, 3, 4)) { throw "Unsupported existing Windows Claude profile schema" }
    if ($null -eq $state.PSObject.Properties["profiles"]) { throw "Existing profiles.json has no profiles object" }
    $existingSchema = [int]$state.schema_version
  } else {
    $state = Copy-JsonValue -Value $template
    $existingSchema = [int]$template.schema_version
    $changed = $true
  }
  foreach ($name in @("schema_version", "host", "port")) {
    if ($null -eq $state.PSObject.Properties[$name]) {
      Set-JsonProperty -Object $state -Name $name -Value $template.$name
      $changed = $true
    }
  }
  if ([int]$state.schema_version -ne [int]$template.schema_version) {
    Set-JsonProperty -Object $state -Name "schema_version" -Value ([int]$template.schema_version)
    $changed = $true
  }
  if ([int]$state.port -ne [int]$template.port) {
    $configuredSecrets = @(Get-ChildItem -LiteralPath $SecretsRoot -File -ErrorAction SilentlyContinue).Count
    if ([int]$state.port -eq 19876 -and $configuredSecrets -eq 0 -and -not (Test-Path -LiteralPath $GatewayStatePath -PathType Leaf)) {
      # 19876 is used by Zotero on some Windows installations.  Only migrate
      # the never-configured preview state; an active/configured owner fails closed.
      Set-JsonProperty -Object $state -Name "port" -Value ([int]$template.port)
      $changed = $true
    } else {
      throw "Existing Windows Claude port differs from the package owner; stop and inspect it before migration"
    }
  }
  foreach ($profileMode in $script:Modes) {
    $templateProfile = Get-ModeProfile -State $template -ProfileMode $profileMode
    $existingProperty = $state.profiles.PSObject.Properties[$profileMode]
    if ($null -eq $existingProperty) {
      Set-JsonProperty -Object $state.profiles -Name $profileMode -Value (Copy-JsonValue -Value $templateProfile)
      $changed = $true
    }
    $profile = Get-ModeProfile -State $state -ProfileMode $profileMode
    $legacyDefault = Get-OptionalJsonString -Object $profile -Name "model_default"
    $legacyFast = Get-OptionalJsonString -Object $profile -Name "model_fast"
    $legacyReasoning = Get-OptionalJsonString -Object $profile -Name "reasoning_effort"
    foreach ($role in $script:Roles) {
      $modelName = "model_$role"
      $reasoningName = "reasoning_$role"
      $longReasoningName = "reasoning_effort_$role"
      $model = Get-OptionalJsonString -Object $profile -Name $modelName
      if (-not $model) {
        $model = if (
          $profileMode -eq "codex" -and $role -eq "sonnet" -and
          $legacyDefault -eq [string]$templateProfile.model_opus
        ) {
          [string]$templateProfile.model_sonnet
        } elseif ($role -eq "haiku" -and $legacyFast) {
          $legacyFast
        } elseif ($legacyDefault) {
          $legacyDefault
        } else {
          [string]$templateProfile.$modelName
        }
      }
      $reasoning = Get-OptionalJsonString -Object $profile -Name $reasoningName
      if (-not $reasoning) { $reasoning = Get-OptionalJsonString -Object $profile -Name $longReasoningName }
      if (-not $reasoning) { $reasoning = $legacyReasoning }
      if (-not $reasoning) { $reasoning = [string]$templateProfile.$reasoningName }
      $modelReasoningChoices = @(Get-ProfileReasoningChoices -ProfileMode $profileMode -Model $model)
      $legacyProviderChoices = @($script:ReasoningChoices[$profileMode])
      if (
        $existingSchema -le 3 -and
        $reasoning -in $legacyProviderChoices -and
        $reasoning -notin $modelReasoningChoices
      ) {
        # Schema 1-3 admitted one provider-wide list.  Preserve the selected
        # model, but move a now-known invalid model/effort pair to safe auto.
        $reasoning = "auto"
        $changed = $true
      }
      Set-JsonProperty -Object $profile -Name $modelName -Value $model
      Set-JsonProperty -Object $profile -Name $reasoningName -Value $reasoning
    }
    foreach ($legacyName in @(
      "model_default", "model_fast", "reasoning_effort",
      "reasoning_effort_opus", "reasoning_effort_sonnet", "reasoning_effort_haiku"
    )) {
      if ($null -ne $profile.PSObject.Properties[$legacyName]) {
        Remove-JsonProperty -Object $profile -Name $legacyName
      }
    }
    if ($existingSchema -ne 4) { $changed = $true }
    foreach ($property in $templateProfile.PSObject.Properties) {
      if ($null -eq $profile.PSObject.Properties[$property.Name]) {
        Set-JsonProperty -Object $profile -Name $property.Name -Value (Copy-JsonValue -Value $property.Value)
        $changed = $true
      }
    }
    $legacyProfileNames = @(
      "FinalKit Windows DeepSeek API", "FinalKit Windows Kimi API",
      "FinalKit Windows GLM API", "FinalKit Windows Codex Login",
      "Switchboard Windows DeepSeek API", "Switchboard Windows Kimi API",
      "Switchboard Windows GLM API", "Switchboard Windows Codex Login"
    )
    if ([string]$profile.name -in $legacyProfileNames -and [string]$profile.name -ne [string]$templateProfile.name) {
      Set-JsonProperty -Object $profile -Name "name" -Value ([string]$templateProfile.name)
      $changed = $true
    }
    if ($profileMode -eq "codex") {
      $legacyApiProfile = (
        [string]$profile.auth_style -eq "bearer" -and
        ([string]$profile.upstream).TrimEnd("/") -eq "https://api.openai.com/v1"
      )
      if ($legacyApiProfile) {
        if (Test-Path -LiteralPath $GatewayStatePath -PathType Leaf) {
          throw "Stop the running Windows Claude gateway before migrating Codex from API key to Windows login"
        }
        foreach ($propertyName in @("name", "protocol", "upstream", "auth_style")) {
          Set-JsonProperty -Object $profile -Name $propertyName -Value (Copy-JsonValue -Value $templateProfile.$propertyName)
        }
        $changed = $true
      } elseif ([string]$profile.auth_style -eq "codex-cli") {
        foreach ($propertyName in @("name", "protocol", "upstream", "auth_style")) {
          if ([string]$profile.$propertyName -ne [string]$templateProfile.$propertyName) {
            Set-JsonProperty -Object $profile -Name $propertyName -Value (Copy-JsonValue -Value $templateProfile.$propertyName)
            $changed = $true
          }
        }
      }
    }
    foreach ($secretName in @("path_secret", "client_token")) {
      if ($null -eq $profile.PSObject.Properties[$secretName] -or -not [string]$profile.$secretName) {
        Set-JsonProperty -Object $profile -Name $secretName -Value (New-UrlSecret)
        $changed = $true
      }
    }
  }
  Assert-StateShape -State $state
  if ($changed) { Write-AtomicJson -Path $ProfilesPath -Value $state }
  Protect-StateRootAcl
  return (Read-JsonObject -Path $ProfilesPath)
}

function Get-WindowsClaudeState {
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body { Get-WindowsClaudeState })
  }
  if (-not (Test-Path -LiteralPath $ProfilesPath -PathType Leaf)) {
    throw "Windows Claude state is not initialized"
  }
  $state = Read-JsonObject -Path $ProfilesPath
  if ([int]$state.schema_version -in @(1, 2, 3)) {
    # Status is also the safe first read after a package update.  Migrate only
    # the known local profile schemas; credentials, gateway state, Claude mode,
    # and WSL are outside this atomic profiles.json rewrite.
    return Initialize-WindowsClaudeState
  }
  Assert-StateShape -State $state
  return $state
}

function Protect-ApiKey {
  param(
    [Parameter(Mandatory = $true)][Security.SecureString]$SecureKey,
    [Parameter(Mandatory = $true)][string]$Path
  )
  Add-Type -AssemblyName System.Security -ErrorAction SilentlyContinue
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
  $plainBytes = $null
  $protectedBytes = $null
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $plainBytes = [Text.Encoding]::UTF8.GetBytes($plain)
    if ($plainBytes.Length -eq 0) { throw "API key cannot be empty" }
    $protectedBytes = [Security.Cryptography.ProtectedData]::Protect(
      $plainBytes, $script:Entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    Write-AtomicBytes -Path $Path -Bytes $protectedBytes
  } finally {
    if ($plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
    if ($protectedBytes) { [Array]::Clear($protectedBytes, 0, $protectedBytes.Length) }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
  }
}

function Unprotect-ApiKeyBytes {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "API key is not configured" }
  Add-Type -AssemblyName System.Security -ErrorAction SilentlyContinue
  $cipher = [IO.File]::ReadAllBytes($Path)
  try {
    return [Security.Cryptography.ProtectedData]::Unprotect(
      $cipher, $script:Entropy, [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
  } catch {
    throw "API key cannot be decrypted by the current Windows user"
  } finally {
    [Array]::Clear($cipher, 0, $cipher.Length)
  }
}

function Get-WindowsCodexHome {
  $codexHome = if (-not [string]::IsNullOrWhiteSpace([string]$env:CODEX_HOME)) {
    [string]$env:CODEX_HOME
  } else {
    Join-Path $env:USERPROFILE ".codex"
  }
  try { $resolvedHome = [IO.Path]::GetFullPath($codexHome) } catch {
    throw "Windows CODEX_HOME is malformed"
  }
  if ($resolvedHome -match '(?i)(\\\\wsl\$|\\\\wsl\.localhost|/mnt/|/home/|wsl\.exe)') {
    throw "Windows Codex auth owner cannot reference WSL"
  }
  return $resolvedHome
}

function Get-WindowsCodexAuthPath {
  return Join-Path (Get-WindowsCodexHome) "auth.json"
}

function Get-WindowsCodexModelDefaults {
  $codexHome = Get-WindowsCodexHome
  $cachePath = Join-Path $codexHome "models_cache.json"
  $configPath = Join-Path $codexHome "config.toml"
  $catalog = @()
  if (Test-Path -LiteralPath $cachePath -PathType Leaf) {
    try {
      $cache = Read-JsonObject -Path $cachePath
      $modelsProperty = $cache.PSObject.Properties["models"]
      if ($null -ne $modelsProperty) {
        $catalog = @(
          foreach ($entry in @($modelsProperty.Value)) {
            if ($null -eq $entry) { continue }
            $slug = Get-OptionalJsonString -Object $entry -Name "slug"
            $visibility = Get-OptionalJsonString -Object $entry -Name "visibility"
            if (
              $visibility -eq "hide" -or
              [string]::IsNullOrWhiteSpace($slug) -or
              $slug.Length -gt 200 -or
              $slug -match '[\r\n]'
            ) {
              continue
            }
            $levels = @()
            $seenEfforts = @{}
            $levelsProperty = $entry.PSObject.Properties["supported_reasoning_levels"]
            if ($null -ne $levelsProperty) {
              $levels = @(
                foreach ($level in @($levelsProperty.Value)) {
                  if ($null -eq $level) { continue }
                  $effort = if ($level -is [string]) {
                    [string]$level
                  } else {
                    Get-OptionalJsonString -Object $level -Name "effort"
                  }
                  $effort = $effort.Trim().ToLowerInvariant()
                  if (-not $effort -or $effort -notmatch '^[A-Za-z0-9_-]{1,32}$' -or $seenEfforts.ContainsKey($effort)) {
                    continue
                  }
                  $seenEfforts[$effort] = $true
                  $description = if ($level -is [string]) { "" } else { Get-OptionalJsonString -Object $level -Name "description" }
                  $description = (($description -replace '[\r\n]+', ' ').Trim())
                  if ($description.Length -gt 500) { $description = $description.Substring(0, 500) }
                  [pscustomobject]@{ Effort = $effort; Description = $description }
                }
              )
            }
            $defaultEffort = (Get-OptionalJsonString -Object $entry -Name "default_reasoning_level").Trim()
            if ($defaultEffort -notmatch '^[A-Za-z0-9_-]{1,32}$') { $defaultEffort = "" }
            if ($defaultEffort -and $levels.Count -gt 0 -and $defaultEffort -notin @($levels | ForEach-Object { $_.Effort })) {
              $defaultEffort = ""
            }
            if (-not $defaultEffort -and $levels.Count -gt 0) { $defaultEffort = [string]$levels[0].Effort }
            [pscustomobject]@{
              Slug = $slug
              DefaultEffort = $defaultEffort
              SupportedReasoning = @($levels)
            }
          }
        )
      }
    } catch {
      $catalog = @()
    }
  }
  $availableModels = @($catalog | ForEach-Object { [string]$_.Slug } | Select-Object -Unique)

  $configuredModel = ""
  $configuredEffort = ""
  if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    try {
      foreach ($line in @(Get-Content -LiteralPath $configPath -Encoding UTF8)) {
        if ($line -match '^\s*\[') { break }
        if (-not $configuredModel -and $line -match '^\s*model\s*=\s*"([^"\r\n]+)"\s*(?:#.*)?$') {
          $configuredModel = $Matches[1].Trim()
          continue
        }
        if (-not $configuredEffort -and $line -match '^\s*model_reasoning_effort\s*=\s*"([^"\r\n]+)"\s*(?:#.*)?$') {
          $candidateEffort = $Matches[1].Trim()
          if ($candidateEffort -match '^[A-Za-z0-9_-]{1,32}$') { $configuredEffort = $candidateEffort }
        }
      }
    } catch {
      $configuredModel = ""
      $configuredEffort = ""
    }
  }

  $opusModel = if (
    $configuredModel -and ($availableModels.Count -eq 0 -or $availableModels -contains $configuredModel)
  ) {
    $configuredModel
  } elseif ($availableModels -contains "gpt-5.6-sol") {
    "gpt-5.6-sol"
  } elseif ($availableModels.Count -gt 0) {
    [string]$availableModels[0]
  } else {
    "gpt-5.6-sol"
  }
  $sonnetModel = if ($availableModels -contains "gpt-5.6-terra") {
    "gpt-5.6-terra"
  } elseif ($availableModels.Count -gt 0) {
    $alternative = @($availableModels | Where-Object { $_ -ne $opusModel } | Select-Object -First 1)
    if ($alternative.Count -gt 0) { [string]$alternative[0] } else { $opusModel }
  } else {
    "gpt-5.6-terra"
  }
  $haikuModel = if ($availableModels -contains "gpt-5.6-luna") {
    "gpt-5.6-luna"
  } elseif ($availableModels -contains "gpt-5.4-mini") {
    "gpt-5.4-mini"
  } elseif ($availableModels.Count -gt 0) {
    $opusModel
  } else {
    "gpt-5.6-luna"
  }
  $opusEntry = Get-CodexModelCatalogEntry -Catalog $catalog -Model $opusModel
  $sonnetEntry = Get-CodexModelCatalogEntry -Catalog $catalog -Model $sonnetModel
  $haikuEntry = Get-CodexModelCatalogEntry -Catalog $catalog -Model $haikuModel
  $preferredEffort = if ($configuredEffort) { $configuredEffort } else { "max" }
  return [pscustomobject]@{
    OpusModel = $opusModel
    SonnetModel = $sonnetModel
    HaikuModel = $haikuModel
    OpusEffort = Get-CodexPreferredEffort -CatalogEntry $opusEntry -Preferred $preferredEffort -Fallback "max"
    SonnetEffort = Get-CodexPreferredEffort -CatalogEntry $sonnetEntry -Preferred $preferredEffort -Fallback "max"
    HaikuEffort = Get-CodexPreferredEffort -CatalogEntry $haikuEntry -Preferred $preferredEffort -Fallback "max"
    AvailableModels = @($availableModels)
    Catalog = @($catalog)
    CachePath = $cachePath
    ConfigPath = $configPath
  }
}

function Get-CodexModelCatalogEntry {
  param(
    [Parameter(Mandatory = $true)][AllowEmptyCollection()]$Catalog,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Model
  )
  foreach ($entry in @($Catalog)) {
    if ([string]$entry.Slug -eq $Model) { return $entry }
  }
  return $null
}

function Get-CodexPreferredEffort {
  param(
    [AllowNull()]$CatalogEntry,
    [AllowEmptyString()][string]$Preferred = "",
    [AllowEmptyString()][string]$Fallback = "max"
  )
  if ($Preferred -eq "auto") { return "auto" }
  if ($null -eq $CatalogEntry) {
    if ($Preferred -match '^[A-Za-z0-9_-]{1,32}$') { return $Preferred }
    if ($Fallback -match '^[A-Za-z0-9_-]{1,32}$') { return $Fallback }
    return "max"
  }
  $supported = @($CatalogEntry.SupportedReasoning | ForEach-Object { [string]$_.Effort })
  if ($supported.Count -eq 0) {
    if ($Preferred -match '^[A-Za-z0-9_-]{1,32}$') { return $Preferred }
    if ([string]$CatalogEntry.DefaultEffort) { return [string]$CatalogEntry.DefaultEffort }
    if ($Fallback -match '^[A-Za-z0-9_-]{1,32}$') { return $Fallback }
    return "max"
  }
  foreach ($candidate in @($Preferred, $Fallback, [string]$CatalogEntry.DefaultEffort, "max", "high", "medium", "low")) {
    if ($candidate -and $candidate -in $supported) { return $candidate }
  }
  return [string]$supported[0]
}

function Show-CodexReasoningCapabilities {
  param(
    [Parameter(Mandatory = $true)][string]$Role,
    [Parameter(Mandatory = $true)][string]$Model,
    [AllowNull()]$CatalogEntry
  )
  if ($null -eq $CatalogEntry -or @($CatalogEntry.SupportedReasoning).Count -eq 0) {
    Write-Host "$Role ($Model) Reasoning: cache did not declare"
    Write-Host "  auto - pass through an incoming role effort; otherwise use the model default"
    return
  }
  Write-Host "$Role ($Model) Reasoning:"
  Write-Host "  auto - pass through an incoming role effort; otherwise use the model default"
  foreach ($level in @($CatalogEntry.SupportedReasoning)) {
    $defaultMarker = if ([string]$level.Effort -eq [string]$CatalogEntry.DefaultEffort) { " [Codex default]" } else { "" }
    $description = if ([string]$level.Description) { " - $([string]$level.Description)" } else { "" }
    Write-Host "  $([string]$level.Effort)$defaultMarker$description"
  }
}

function Read-CodexReasoningEffort {
  param(
    [Parameter(Mandatory = $true)][string]$Role,
    [Parameter(Mandatory = $true)][string]$Model,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Preferred,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()]$Catalog
  )
  $entry = Get-CodexModelCatalogEntry -Catalog $Catalog -Model $Model
  $seed = Get-CodexPreferredEffort -CatalogEntry $entry -Preferred $Preferred -Fallback "max"
  Show-CodexReasoningCapabilities -Role $Role -Model $Model -CatalogEntry $entry
  $value = Read-Host "$Role Reasoning [$seed]"
  if ([string]::IsNullOrWhiteSpace([string]$value)) { $value = $seed }
  $value = ([string]$value).Trim().ToLowerInvariant()
  if ($value -notmatch '^[A-Za-z0-9_-]{1,32}$') { throw "$Role Reasoning is malformed" }
  if ($null -ne $entry) {
    $supported = @($entry.SupportedReasoning | ForEach-Object { [string]$_.Effort })
    if ($supported.Count -gt 0 -and $value -ne "auto" -and $value -notin $supported) {
      throw "$Role Model $Model does not support Reasoning=$value; choose: auto, $($supported -join ', ')"
    }
  } else {
    Write-Warning "$Role Model $Model is not in the local Codex cache; Reasoning=$value cannot be capability-validated."
  }
  return $value
}

function Assert-WindowsCodexRouteCapabilities {
  param([Parameter(Mandatory = $true)]$Profile)
  $local = Get-WindowsCodexModelDefaults
  $capabilities = [ordered]@{}
  foreach ($role in $script:Roles) {
    $roleLabel = (Get-Culture).TextInfo.ToTitleCase($role)
    $model = Get-OptionalJsonString -Object $Profile -Name "model_$role"
    $reasoning = Get-OptionalJsonString -Object $Profile -Name "reasoning_$role"
    $entry = Get-CodexModelCatalogEntry -Catalog $local.Catalog -Model $model
    if ($null -eq $entry) {
      $capabilities[$role] = @()
      Write-Warning "$roleLabel Model $model is absent from the local Windows Codex cache; capability validation is unavailable."
      continue
    }
    $supported = @($entry.SupportedReasoning | ForEach-Object { ([string]$_.Effort).ToLowerInvariant() } | Select-Object -Unique)
    $capabilities[$role] = @($supported)
    if ($supported.Count -gt 0 -and $reasoning -ne "auto" -and $reasoning -notin $supported) {
      throw "$roleLabel Model $model does not support Reasoning=$reasoning; choose: auto, $($supported -join ', ')"
    }
  }
  return $capabilities
}

function Get-WindowsCodexAuthStatus {
  $path = Get-WindowsCodexAuthPath
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    return [pscustomobject]@{ Configured = $false; Path = $path; Mode = "missing"; Reason = "auth.json is missing" }
  }
  try {
    $auth = Read-JsonObject -Path $path
    $modeProperty = $auth.PSObject.Properties["auth_mode"]
    $mode = if ($null -ne $modeProperty) { [string]$modeProperty.Value } else { "" }
    $tokensProperty = $auth.PSObject.Properties["tokens"]
    $tokens = if ($null -ne $tokensProperty) { $tokensProperty.Value } else { $null }
    $access = if ($null -ne $tokens -and $null -ne $tokens.PSObject.Properties["access_token"]) {
      [string]$tokens.PSObject.Properties["access_token"].Value
    } else { "" }
    $refresh = if ($null -ne $tokens -and $null -ne $tokens.PSObject.Properties["refresh_token"]) {
      [string]$tokens.PSObject.Properties["refresh_token"].Value
    } else { "" }
    if ($mode -ne "chatgpt") {
      return [pscustomobject]@{ Configured = $false; Path = $path; Mode = $mode; Reason = "Codex is not using ChatGPT login" }
    }
    if ([string]::IsNullOrWhiteSpace($access) -or [string]::IsNullOrWhiteSpace($refresh)) {
      return [pscustomobject]@{ Configured = $false; Path = $path; Mode = $mode; Reason = "ChatGPT token chain is incomplete" }
    }
    return [pscustomobject]@{ Configured = $true; Path = $path; Mode = $mode; Reason = "" }
  } catch {
    return [pscustomobject]@{ Configured = $false; Path = $path; Mode = "invalid"; Reason = $_.Exception.Message }
  }
}

function Assert-WindowsCodexAuthConfigured {
  $status = Get-WindowsCodexAuthStatus
  if (-not $status.Configured) {
    throw "Windows Codex ChatGPT login is unavailable ($($status.Reason)). Run: codex login"
  }
  return $status
}

function Invoke-WindowsCodexLoginStatus {
  $command = Get-Command codex -ErrorAction SilentlyContinue
  if ($null -eq $command) { throw "Windows Codex CLI was not found; install it and run: codex login" }
  & $command.Source login status
  if ($LASTEXITCODE -ne 0) { throw "Windows Codex is not logged in. Run: codex login" }
}

function Test-ProfileConfigured {
  param(
    [Parameter(Mandatory = $true)]$State,
    [Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode
  )
  $profile = Get-ModeProfile -State $State -ProfileMode $ProfileMode
  $credentialReady = if ($ProfileMode -eq "codex") {
    [bool](Get-WindowsCodexAuthStatus).Configured
  } else {
    Test-Path -LiteralPath (Get-SecretPath -ProfileMode $ProfileMode) -PathType Leaf
  }
  if (-not $credentialReady) { return $false }
  foreach ($role in $script:Roles) {
    $model = Get-OptionalJsonString -Object $profile -Name "model_$role"
    if ([string]::IsNullOrWhiteSpace($model)) {
      return $false
    }
    $reasoningChoices = @(Get-ProfileReasoningChoices -ProfileMode $ProfileMode -Model $model)
    if ((Get-OptionalJsonString -Object $profile -Name "reasoning_$role") -notin $reasoningChoices) {
      return $false
    }
  }
  return $true
}

function Assert-ModelValue {
  param(
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value,
    [Parameter(Mandatory = $true)][string]$Label
  )
  if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Length -gt 200 -or $Value -match '[\r\n]') {
    throw "$Label is not configured; enter a model ID or accept a displayed default"
  }
}

function Configure-WindowsClaudeProfile {
  param([Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode)
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body {
      Configure-WindowsClaudeProfile -ProfileMode $ProfileMode
    })
  }
  # Fail before profile/secret mutation on a machine that cannot run the
  # Windows-only loopback gateway.
  $null = Get-WindowsPython
  $state = Initialize-WindowsClaudeState
  $profile = Get-ModeProfile -State $state -ProfileMode $ProfileMode
  Write-Host "Configure the independent Windows Claude $ProfileMode profile." -ForegroundColor Cyan
  $codexAuth = $null
  if ($ProfileMode -eq "codex") {
    Write-Host "Credential owner: the official Windows Codex CLI ChatGPT login; no separate OpenAI API key is requested."
    Write-Host "This does not read, change, or invoke WSL."
    $codexAuth = Assert-WindowsCodexAuthConfigured
    Invoke-WindowsCodexLoginStatus
    Write-Host "Existing Windows Codex ChatGPT login detected; another browser login is not needed."
    $upstream = [string]$profile.upstream
  } else {
    Write-Host "This does not read, change, or invoke WSL. The provider API key is encrypted with Windows DPAPI."
    $upstream = Read-Host "Upstream API base URL [$($profile.upstream)]"
    if (-not $upstream) { $upstream = [string]$profile.upstream }
    Assert-WindowsOnlyValue -Value $upstream -Label "$ProfileMode upstream"
  }
  $selectedModels = @{}
  $selectedReasoning = @{}
  $localDefaults = $null
  $catalog = @()
  $listedModels = @()
  if ($ProfileMode -eq "codex") {
    $localDefaults = Get-WindowsCodexModelDefaults
    $listedModels = @($localDefaults.AvailableModels)
    $catalog = @($localDefaults.Catalog)
    if ($listedModels.Count -gt 0) {
      Write-Host ("Models: " + ($listedModels -join ", "))
    } else {
      Write-Host "Models: local Codex cache unavailable; using packaged Sol/Terra/Luna routes."
    }
  } else {
    Write-Host ("Models: " + (@($script:ProviderModels[$ProfileMode]) -join ", ") + " (package suggestions)")
    Write-Host "Reasoning is shown per selected model below."
  }
  Write-Host "Configure each Claude tier (only Model and Reasoning are stored):"
  foreach ($role in $script:Roles) {
    $roleLabel = (Get-Culture).TextInfo.ToTitleCase($role)
    $modelSeed = Get-OptionalJsonString -Object $profile -Name "model_$role"
    if (-not $modelSeed -and $ProfileMode -eq "codex") {
      $defaultProperty = "${roleLabel}Model"
      $modelSeed = [string]$localDefaults.$defaultProperty
    }
    $modelValue = Read-Host "$roleLabel Model [$modelSeed]"
    if ([string]::IsNullOrWhiteSpace([string]$modelValue)) { $modelValue = $modelSeed }
    $modelValue = ([string]$modelValue).Trim()
    Assert-ModelValue -Value $modelValue -Label "$roleLabel Model"
    if ($ProfileMode -eq "codex" -and $listedModels.Count -gt 0 -and $modelValue -notin $listedModels) {
      throw "$roleLabel Model $modelValue is not advertised by the local Windows Codex cache"
    }
    $selectedModels[$role] = $modelValue

    $reasoningSeed = Get-OptionalJsonString -Object $profile -Name "reasoning_$role"
    if ($ProfileMode -eq "codex") {
      if (-not $reasoningSeed) {
        $reasoningProperty = "${roleLabel}Effort"
        $reasoningSeed = [string]$localDefaults.$reasoningProperty
      }
      $reasoningValue = Read-CodexReasoningEffort -Role $roleLabel -Model $modelValue -Preferred $reasoningSeed -Catalog $catalog
    } else {
      $reasoningChoices = @(Get-ProfileReasoningChoices -ProfileMode $ProfileMode -Model $modelValue)
      if ($reasoningSeed -notin $reasoningChoices) { $reasoningSeed = "auto" }
      Write-Host ("$roleLabel Reasoning: " + ($reasoningChoices -join ", "))
      $reasoningValue = Read-Host "$roleLabel Reasoning [$reasoningSeed]"
      if ([string]::IsNullOrWhiteSpace([string]$reasoningValue)) { $reasoningValue = $reasoningSeed }
      $reasoningValue = ([string]$reasoningValue).Trim().ToLowerInvariant()
      if ($reasoningValue -notin $reasoningChoices) {
        throw "$roleLabel Reasoning must be one of: $($reasoningChoices -join ', ')"
      }
    }
    $selectedReasoning[$role] = $reasoningValue
  }
  $secretPath = if ($ProfileMode -ne "codex") { Get-SecretPath -ProfileMode $ProfileMode } else { "" }
  $secureKey = $null
  $replaceKey = $false
  if ($ProfileMode -ne "codex") {
    $secureKey = Read-Host "API key (hidden; blank keeps the existing encrypted key)" -AsSecureString
    $replaceKey = $secureKey.Length -gt 0
    if (-not $replaceKey -and -not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
      throw "No existing encrypted API key; enter a key to configure this profile"
    }
  }

  $stateBefore = [IO.File]::ReadAllBytes($ProfilesPath)
  $secretExisted = $ProfileMode -ne "codex" -and (Test-Path -LiteralPath $secretPath -PathType Leaf)
  $secretBefore = [byte[]]::new(0)
  if ($secretExisted) { $secretBefore = [IO.File]::ReadAllBytes($secretPath) }
  try {
    Set-JsonProperty -Object $profile -Name "upstream" -Value $upstream.TrimEnd("/")
    foreach ($role in $script:Roles) {
      Set-JsonProperty -Object $profile -Name "model_$role" -Value ([string]$selectedModels[$role])
      Set-JsonProperty -Object $profile -Name "reasoning_$role" -Value ([string]$selectedReasoning[$role])
    }
    foreach ($legacyName in @(
      "model_default", "model_fast", "reasoning_effort",
      "reasoning_effort_opus", "reasoning_effort_sonnet", "reasoning_effort_haiku"
    )) {
      Remove-JsonProperty -Object $profile -Name $legacyName
    }
    if ($replaceKey) { Protect-ApiKey -SecureKey $secureKey -Path $secretPath }
    Assert-StateShape -State $state
    Write-AtomicJson -Path $ProfilesPath -Value $state
  } catch {
    Write-AtomicBytes -Path $ProfilesPath -Bytes $stateBefore
    if ($ProfileMode -ne "codex" -and $secretExisted) {
      Write-AtomicBytes -Path $secretPath -Bytes $secretBefore
    } elseif ($ProfileMode -ne "codex" -and (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
      Remove-Item -LiteralPath $secretPath -Force
    }
    throw
  } finally {
    [Array]::Clear($stateBefore, 0, $stateBefore.Length)
    if ($secretBefore.Length -gt 0) { [Array]::Clear($secretBefore, 0, $secretBefore.Length) }
    if ($secureKey) { $secureKey.Dispose() }
  }
  Protect-StateRootAcl
  if ($ProfileMode -eq "codex") {
    Write-Host "Windows Claude Codex profile configured from Windows Codex login; token was not copied into Switchboard or WSL." -ForegroundColor Green
    Write-Host "Windows Codex auth owner: $($codexAuth.Path)"
  } else {
    Write-Host "Windows Claude $ProfileMode API profile configured; no key was written to JSON, argv, environment, or WSL." -ForegroundColor Green
  }
  foreach ($role in $script:Roles) {
    $roleLabel = (Get-Culture).TextInfo.ToTitleCase($role)
    Write-Host "  $roleLabel  Model=$([string]$selectedModels[$role])  Reasoning=$([string]$selectedReasoning[$role])"
  }
}

function Get-ClaudeDesktopPaths {
  $package = @(Get-AppxPackage -Name Claude -ErrorAction SilentlyContinue |
    Sort-Object Version -Descending | Select-Object -First 1)
  $startApp = @(Get-StartApps -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "Claude" -or $_.AppID -match '^Claude_.+!Claude$' } |
    Select-Object -First 1)
  if ($package.Count -eq 0 -and $startApp.Count -eq 0) {
    throw "Official Windows Claude application is not installed for this user"
  }
  $roamingDir = Join-Path $env:APPDATA "Claude"
  $legacyLocalDir = Join-Path $env:LOCALAPPDATA "Claude"
  $containerDir = $null
  if ($package.Count -gt 0) {
    $containerDir = Join-Path $env:LOCALAPPDATA ("Packages\{0}\LocalCache\Roaming\Claude" -f $package[0].PackageFamilyName)
  }
  if (Test-Path -LiteralPath $roamingDir -PathType Container) {
    $normalDir = $roamingDir
  } elseif ($containerDir -and (Test-Path -LiteralPath $containerDir -PathType Container)) {
    $normalDir = $containerDir
  } elseif (Test-Path -LiteralPath $legacyLocalDir -PathType Container) {
    $normalDir = $legacyLocalDir
  } elseif ($containerDir) {
    $normalDir = $containerDir
  } else {
    $normalDir = $roamingDir
  }
  $appId = if ($startApp.Count -gt 0) { [string]$startApp[0].AppID } elseif ($package.Count -gt 0) {
    "$($package[0].PackageFamilyName)!Claude"
  } else { "" }
  $version = if ($package.Count -gt 0) { $package[0].Version.ToString() } else { "unknown" }
  $threepRoot = Join-Path $env:LOCALAPPDATA "Claude-3p"
  $library = Join-Path $threepRoot "configLibrary"
  return [pscustomobject]@{
    AppId = $appId
    Version = $version
    NormalConfig = Join-Path $normalDir "claude_desktop_config.json"
    ThreepConfig = Join-Path $threepRoot "claude_desktop_config.json"
    Library = $library
    Meta = Join-Path $library "_meta.json"
  }
}

function Get-ClaudeManagedFiles {
  param([Parameter(Mandatory = $true)]$Paths)
  $files = @(
    [pscustomobject]@{ Role = "normal-config"; Path = [string]$Paths.NormalConfig },
    [pscustomobject]@{ Role = "threep-config"; Path = [string]$Paths.ThreepConfig },
    [pscustomobject]@{ Role = "profile-meta"; Path = [string]$Paths.Meta }
  )
  foreach ($profileMode in $script:Modes) {
    $files += [pscustomobject]@{
      Role = "profile-$profileMode"
      Path = Join-Path $Paths.Library "$($script:ProfileIds[$profileMode]).json"
    }
  }
  return @($files)
}

function Get-FileSnapshots {
  param([Parameter(Mandatory = $true)][object[]]$Files)
  $snapshots = @()
  foreach ($file in $Files) {
    $exists = Test-Path -LiteralPath $file.Path -PathType Leaf
    $snapshots += [pscustomobject]@{
      Role = $file.Role
      Path = $file.Path
      Exists = $exists
      Bytes = if ($exists) { [IO.File]::ReadAllBytes($file.Path) } else { [byte[]]@() }
    }
  }
  return @($snapshots)
}

function Restore-FileSnapshots {
  param([Parameter(Mandatory = $true)][object[]]$Snapshots)
  foreach ($snapshot in $Snapshots) {
    if ($snapshot.Exists) {
      Write-AtomicBytes -Path $snapshot.Path -Bytes $snapshot.Bytes
    } elseif (Test-Path -LiteralPath $snapshot.Path -PathType Leaf) {
      Remove-Item -LiteralPath $snapshot.Path -Force
    }
  }
}

function Backup-ClaudeSnapshots {
  param([Parameter(Mandatory = $true)][object[]]$Snapshots)
  if ($NoBackup) { return "" }
  $existing = @($Snapshots | Where-Object { $_.Exists })
  if ($existing.Count -eq 0) { return "" }
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backupRoot = Join-Path (Split-Path -Parent $StateRoot) "Backups\ClaudeDesktop\$stamp-$([guid]::NewGuid().ToString('N').Substring(0,8))"
  New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
  foreach ($snapshot in $existing) {
    $backupFile = Join-Path $backupRoot ("{0}--{1}" -f $snapshot.Role, (Split-Path -Leaf $snapshot.Path))
    [IO.File]::WriteAllBytes($backupFile, $snapshot.Bytes)
  }
  return $backupRoot
}

function Set-DeploymentMode {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][ValidateSet("1p", "3p")][string]$DeploymentMode
  )
  $config = Read-JsonObject -Path $Path -AllowMissing
  Set-JsonProperty -Object $config -Name "deploymentMode" -Value $DeploymentMode
  Write-AtomicJson -Path $Path -Value $config
}

function Get-MetaEntries {
  param([Parameter(Mandatory = $true)]$Meta)
  $property = $Meta.PSObject.Properties["entries"]
  if ($null -eq $property -or $null -eq $property.Value) { return @() }
  $value = $property.Value
  if ($value -is [Array]) { return @($value) }
  if ($value -is [pscustomobject] -and $null -ne $value.PSObject.Properties["id"]) { return @($value) }
  return @()
}

function New-ClaudeProfilePayload {
  param(
    [Parameter(Mandatory = $true)]$State,
    [Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode
  )
  $profile = Get-ModeProfile -State $State -ProfileMode $ProfileMode
  $opus = Get-OptionalJsonString -Object $profile -Name "model_opus"
  $sonnet = Get-OptionalJsonString -Object $profile -Name "model_sonnet"
  $haiku = Get-OptionalJsonString -Object $profile -Name "model_haiku"
  $opusReasoning = Get-OptionalJsonString -Object $profile -Name "reasoning_opus"
  $sonnetReasoning = Get-OptionalJsonString -Object $profile -Name "reasoning_sonnet"
  $haikuReasoning = Get-OptionalJsonString -Object $profile -Name "reasoning_haiku"
  if (-not $opus) { $opus = "not-configured" }
  if (-not $sonnet) { $sonnet = "not-configured" }
  if (-not $haiku) { $haiku = "not-configured" }
  $opusLabel = "$opus | reasoning=$opusReasoning"
  $sonnetLabel = "$sonnet | reasoning=$sonnetReasoning"
  $haikuLabel = "$haiku | reasoning=$haikuReasoning"
  $base = "http://127.0.0.1:$([int]$State.port)/$([string]$profile.path_secret)"
  return [pscustomobject]@{
    disableDeploymentModeChooser = $true
    inferenceGatewayApiKey = [string]$profile.client_token
    inferenceGatewayAuthScheme = "bearer"
    inferenceGatewayBaseUrl = $base
    inferenceModels = @(
      [pscustomobject]@{ name = "claude-opus-4-8"; labelOverride = $opusLabel },
      [pscustomobject]@{ name = "claude-sonnet-4-5"; labelOverride = $sonnetLabel },
      [pscustomobject]@{ name = "claude-haiku-4-5-20251001"; labelOverride = $haikuLabel }
    )
    inferenceProvider = "gateway"
  }
}

function Install-ClaudeProfileLibrary {
  param(
    [Parameter(Mandatory = $true)]$State,
    [ValidateSet("", "deepseek", "kimi", "glm", "codex")][string]$AppliedMode = "",
    [switch]$Activate
  )
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body {
      Install-ClaudeProfileLibrary -State $State -AppliedMode $AppliedMode -Activate:$Activate
    })
  }
  $paths = Get-ClaudeDesktopPaths
  $files = Get-ClaudeManagedFiles -Paths $paths
  $snapshots = Get-FileSnapshots -Files $files
  $backup = Backup-ClaudeSnapshots -Snapshots $snapshots
  try {
    if (-not (Test-Path -LiteralPath $paths.Library -PathType Container)) {
      New-Item -ItemType Directory -Path $paths.Library -Force | Out-Null
    }
    foreach ($profileMode in $script:Modes) {
      $profilePath = Join-Path $paths.Library "$($script:ProfileIds[$profileMode]).json"
      Write-AtomicJson -Path $profilePath -Value (New-ClaudeProfilePayload -State $State -ProfileMode $profileMode)
    }
    $meta = Read-JsonObject -Path $paths.Meta -AllowMissing
    $knownIds = @($script:ProfileIds.Values)
    $entries = @(Get-MetaEntries -Meta $meta | Where-Object {
      $idProperty = $_.PSObject.Properties["id"]
      $null -eq $idProperty -or $knownIds -notcontains [string]$idProperty.Value
    })
    foreach ($profileMode in $script:Modes) {
      $profile = Get-ModeProfile -State $State -ProfileMode $profileMode
      $entries += [pscustomobject]@{ id = [string]$profile.profile_id; name = [string]$profile.name }
    }
    Set-JsonProperty -Object $meta -Name "entries" -Value @($entries)
    $applied = $meta.PSObject.Properties["appliedId"]
    if ($AppliedMode) {
      Set-JsonProperty -Object $meta -Name "appliedId" -Value $script:ProfileIds[$AppliedMode]
    } elseif ($null -ne $applied -and $knownIds -contains [string]$applied.Value) {
      $meta.PSObject.Properties.Remove("appliedId")
    }
    Write-AtomicJson -Path $paths.Meta -Value $meta
    if ($Activate) {
      Set-DeploymentMode -Path $paths.NormalConfig -DeploymentMode "3p"
      Set-DeploymentMode -Path $paths.ThreepConfig -DeploymentMode "3p"
    }
  } catch {
    $primary = $_.Exception.Message
    try { Restore-FileSnapshots -Snapshots $snapshots } catch {
      throw "Claude profile install failed ($primary) and rollback failed: $($_.Exception.Message)"
    }
    throw "Claude profile install failed; prior config restored: $primary"
  }
  return [pscustomobject]@{ Paths = $paths; Backup = $backup }
}

function Initialize-WindowsClaude {
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body { Initialize-WindowsClaude })
  }
  $python = Get-WindowsPython
  $state = Initialize-WindowsClaudeState
  $installed = Install-ClaudeProfileLibrary -State $state
  $configured = @($script:Modes | Where-Object { Test-ProfileConfigured -State $state -ProfileMode $_ }).Count
  Write-Host "Windows-only Claude stack initialized: $configured/4 provider profiles configured." -ForegroundColor Green
  Write-Host "DeepSeek/Kimi/GLM use Windows API keys; Codex uses the Windows Codex CLI login."
  Write-Host "Claude remains in official 1P mode until a configured Windows profile is started."
  Write-Host "Runtime state: $StateRoot"
  Write-Host "Windows Python runtime: $python"
  if ($installed.Backup) { Write-Host "Claude config backup: $($installed.Backup)" }
  Write-Host "Isolation: Windows port 18987; no WSL command, path, port, credential, or process is used."
}

function ConvertTo-NativeArgumentString {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)
  return (($Arguments | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' } else { $_ }
  }) -join ' ')
}

function Get-WindowsPythonInfo {
  $py = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($py) {
    $resolved = @(& $py.Source -3 -c "import sys; print(sys.executable); print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Where-Object { $_ })
    if ($LASTEXITCODE -eq 0 -and $resolved.Count -ge 2) {
      $candidate = [string]$resolved[-2]
      $version = [string]$resolved[-1]
      if ((Test-Path -LiteralPath $candidate -PathType Leaf) -and [version]$version -ge [version]"3.10") {
        return [pscustomobject]@{
          Path = (Resolve-Path -LiteralPath $candidate).Path
          Version = $version
        }
      }
    }
  }
  foreach ($name in @("python.exe", "python3.exe")) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
      $majorMinor = & $command.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
      if ($LASTEXITCODE -eq 0 -and [version]$majorMinor -ge [version]"3.10") {
        return [pscustomobject]@{
          Path = (Resolve-Path -LiteralPath $command.Source).Path
          Version = [string]$majorMinor
        }
      }
    }
  }
  throw "Windows Python 3.10+ was not found. Install a current 64-bit Python from https://www.python.org/downloads/windows/, enable py.exe or python.exe for this user, then rerun the Windows Claude action. Build installs WSL Python only."
}

function Get-WindowsPython {
  return [string](Get-WindowsPythonInfo).Path
}

function Invoke-LoopbackJson {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$ClientToken,
    [ValidateSet("GET", "POST")][string]$Method = "GET",
    [string]$ControlToken = "",
    [int]$TimeoutMilliseconds = 2000
  )
  $request = [Net.HttpWebRequest]::Create($Url)
  $request.Proxy = $null
  $request.Method = $Method
  $request.Timeout = $TimeoutMilliseconds
  $request.ReadWriteTimeout = $TimeoutMilliseconds
  $request.Headers["Authorization"] = "Bearer $ClientToken"
  if ($ControlToken) { $request.Headers["X-FinalKit-Control"] = $ControlToken }
  if ($Method -eq "POST") { $request.ContentLength = 0 }
  $response = $request.GetResponse()
  try {
    $reader = New-Object IO.StreamReader($response.GetResponseStream(), [Text.Encoding]::UTF8)
    try { return ($reader.ReadToEnd() | ConvertFrom-Json) } finally { $reader.Dispose() }
  } finally {
    $response.Dispose()
  }
}

function Get-GatewayHealth {
  param([Parameter(Mandatory = $true)]$RuntimeConfig)
  $url = "http://127.0.0.1:$([int]$RuntimeConfig.port)/$([string]$RuntimeConfig.path_secret)/health"
  try {
    $health = Invoke-LoopbackJson -Url $url -ClientToken ([string]$RuntimeConfig.client_token)
    if (
      $health.status -eq "ok" -and
      $health.owner -eq "ScienceCodexFinalKit-WindowsClaude" -and
      [string]$health.instance_id -eq [string]$RuntimeConfig.instance_id -and
      [string]$health.profile -eq [string]$RuntimeConfig.profile
    ) { return $health }
  } catch { }
  return $null
}

function Test-GatewayProcessIdentity {
  param([Parameter(Mandatory = $true)][int]$ProcessId)
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if (-not $process) { return $false }
  $commandLine = [string]$process.CommandLine
  return (
    $commandLine.IndexOf($GatewayScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $commandLine.IndexOf($RuntimeConfigPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
  )
}

function Stop-WindowsClaudeGateway {
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body { Stop-WindowsClaudeGateway })
  }
  if (-not (Test-Path -LiteralPath $GatewayStatePath -PathType Leaf)) {
    if (Test-Path -LiteralPath $RuntimeConfigPath -PathType Leaf) { Remove-Item -LiteralPath $RuntimeConfigPath -Force }
    return
  }
  $state = Read-JsonObject -Path $GatewayStatePath
  $processId = [int]$state.pid
  $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if (-not $process) {
    Remove-Item -LiteralPath $GatewayStatePath -Force
    if (Test-Path -LiteralPath $RuntimeConfigPath -PathType Leaf) { Remove-Item -LiteralPath $RuntimeConfigPath -Force }
    return
  }
  if (-not (Test-GatewayProcessIdentity -ProcessId $processId)) {
    throw "PID $processId is not the owned Windows Claude gateway; refusing to stop it"
  }
  $runtime = Read-JsonObject -Path $RuntimeConfigPath
  if ([string]$runtime.instance_id -ne [string]$state.instance_id) {
    throw "Windows Claude gateway state identity mismatch; refusing to stop PID $processId"
  }
  $url = "http://127.0.0.1:$([int]$runtime.port)/$([string]$runtime.path_secret)/control/stop"
  try {
    [void](Invoke-LoopbackJson -Url $url -ClientToken ([string]$runtime.client_token) -Method POST -ControlToken ([string]$runtime.control_secret))
  } catch {
    Write-Warning "Graceful gateway stop was unavailable; verified owner PID $processId will be stopped locally."
  }
  $deadline = (Get-Date).AddSeconds(10)
  while ((Get-Process -Id $processId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 200
  }
  if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
    if (-not (Test-GatewayProcessIdentity -ProcessId $processId)) {
      throw "Gateway PID identity changed during stop; refusing force termination"
    }
    Stop-Process -Id $processId -Force
  }
  Remove-Item -LiteralPath $GatewayStatePath -Force
  if (Test-Path -LiteralPath $RuntimeConfigPath -PathType Leaf) { Remove-Item -LiteralPath $RuntimeConfigPath -Force }
  Write-Host "Windows Claude gateway stopped."
}

function Start-WindowsClaudeGateway {
  param(
    [Parameter(Mandatory = $true)]$State,
    [Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode
  )
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body {
      Start-WindowsClaudeGateway -State $State -ProfileMode $ProfileMode
    })
  }
  if (-not (Test-ProfileConfigured -State $State -ProfileMode $ProfileMode)) {
    throw "Windows Claude $ProfileMode is unconfigured; run configure first"
  }
  Stop-WindowsClaudeGateway
  $profile = Get-ModeProfile -State $State -ProfileMode $ProfileMode
  $codexAuth = if ($ProfileMode -eq "codex") { Assert-WindowsCodexAuthConfigured } else { $null }
  $codexCapabilities = if ($ProfileMode -eq "codex") {
    Assert-WindowsCodexRouteCapabilities -Profile $profile
  } else { $null }
  $instanceId = [guid]::NewGuid().ToString()
  $runtime = [pscustomobject]@{
    schema_version = 3
    instance_id = $instanceId
    profile = $ProfileMode
    profile_id = [string]$profile.profile_id
    profile_name = [string]$profile.name
    host = "127.0.0.1"
    port = [int]$State.port
    path_secret = [string]$profile.path_secret
    client_token = [string]$profile.client_token
    control_secret = New-UrlSecret
    protocol = [string]$profile.protocol
    upstream = [string]$profile.upstream
    auth_style = [string]$profile.auth_style
    offline_smoke = $false
  }
  if ($ProfileMode -eq "codex") {
    Set-JsonProperty -Object $runtime -Name "codex_auth_file" -Value ([string]$codexAuth.Path)
    foreach ($role in $script:Roles) {
      Set-JsonProperty -Object $runtime -Name "supported_reasoning_$role" -Value @($codexCapabilities[$role])
    }
  }
  foreach ($role in $script:Roles) {
    foreach ($kind in @("model", "reasoning")) {
      $name = "${kind}_$role"
      Set-JsonProperty -Object $runtime -Name $name -Value (Get-OptionalJsonString -Object $profile -Name $name)
    }
  }
  Write-AtomicJson -Path $RuntimeConfigPath -Value $runtime
  Protect-StateRootAcl
  $python = Get-WindowsPython
  $log = Join-Path $LogsRoot ("gateway-{0}-{1}.log" -f $ProfileMode, (Get-Date -Format "yyyyMMdd-HHmmss"))
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = $python
  $startInfo.Arguments = ConvertTo-NativeArgumentString -Arguments @($GatewayScript, "--config", $RuntimeConfigPath, "--log-file", $log)
  $startInfo.WorkingDirectory = $StateRoot
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardInput = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  $keyBytes = $null
  try {
    [void]$process.Start()
    if ($ProfileMode -ne "codex") {
      $keyBytes = Unprotect-ApiKeyBytes -Path (Get-SecretPath -ProfileMode $ProfileMode)
      $process.StandardInput.BaseStream.Write($keyBytes, 0, $keyBytes.Length)
      $process.StandardInput.BaseStream.WriteByte(10)
      $process.StandardInput.BaseStream.Flush()
    }
    $process.StandardInput.Close()
  } catch {
    if (-not $process.HasExited) { $process.Kill() }
    if (Test-Path -LiteralPath $RuntimeConfigPath -PathType Leaf) { Remove-Item -LiteralPath $RuntimeConfigPath -Force }
    throw
  } finally {
    if ($keyBytes) { [Array]::Clear($keyBytes, 0, $keyBytes.Length) }
  }
  $health = $null
  $deadline = (Get-Date).AddSeconds(15)
  while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) { break }
    $health = Get-GatewayHealth -RuntimeConfig $runtime
    if ($health) { break }
    Start-Sleep -Milliseconds 200
  }
  if (-not $health) {
    if (-not $process.HasExited) { $process.Kill() }
    $tail = if (Test-Path -LiteralPath $log -PathType Leaf) { (Get-Content -LiteralPath $log -Tail 20) -join " | " } else { "no gateway log" }
    if (Test-Path -LiteralPath $RuntimeConfigPath -PathType Leaf) { Remove-Item -LiteralPath $RuntimeConfigPath -Force }
    throw "Windows Claude gateway did not become healthy: $tail"
  }
  $gatewayState = [pscustomobject]@{
    schema_version = 1
    pid = [int]$process.Id
    instance_id = $instanceId
    profile = $ProfileMode
    port = [int]$State.port
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    log = $log
  }
  Write-AtomicJson -Path $GatewayStatePath -Value $gatewayState
  Protect-StateRootAcl
  Write-Host "Windows Claude gateway ready: profile=$ProfileMode pid=$($process.Id) 127.0.0.1:$($State.port)" -ForegroundColor Green
  return $runtime
}

function Start-WindowsClaude {
  param([Parameter(Mandatory = $true)][ValidateSet("deepseek", "kimi", "glm", "codex")][string]$ProfileMode)
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body {
      Start-WindowsClaude -ProfileMode $ProfileMode
    })
  }
  $null = Get-WindowsPython
  $state = Initialize-WindowsClaudeState
  $runtime = Start-WindowsClaudeGateway -State $state -ProfileMode $ProfileMode
  try {
    $installed = Install-ClaudeProfileLibrary -State $state -AppliedMode $ProfileMode -Activate
  } catch {
    Stop-WindowsClaudeGateway
    throw
  }
  $paths = $installed.Paths
  Write-Host "Applied official Windows Claude 3P profile: $ProfileMode"
  Write-Host "Effective route:"
  foreach ($role in $script:Roles) {
    $roleLabel = (Get-Culture).TextInfo.ToTitleCase($role)
    $modelName = "model_$role"
    $reasoningName = "reasoning_$role"
    Write-Host "  $roleLabel  Model=$([string]$runtime.$modelName)  Reasoning=$([string]$runtime.$reasoningName)"
  }
  if ($installed.Backup) { Write-Host "Claude config backup: $($installed.Backup)" }
  if ($ProfileMode -eq "codex") {
    Write-Host "Inference auth remains owned by the Windows Codex CLI; no token enters Switchboard state, Claude profile, or WSL."
  } else {
    Write-Host "Windows API key remains DPAPI-encrypted under $SecretsRoot and never enters WSL."
  }
  if ($NoLaunch) { return }
  $running = @(Get-Process -Name Claude -ErrorAction SilentlyContinue)
  if ($running.Count -gt 0 -and -not $Force) {
    Write-Warning "Claude is already running. Fully quit it and rerun start, or use -Force to relaunch with the new profile."
    return
  }
  if ($running.Count -gt 0 -and $Force) {
    $running | Stop-Process -Force
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Process -Name Claude -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
      Start-Sleep -Milliseconds 200
    }
    if (Get-Process -Name Claude -ErrorAction SilentlyContinue) { throw "Claude processes did not stop" }
  }
  if (-not $paths.AppId) { throw "Claude AppUserModelId was not found" }
  Start-Process -FilePath "explorer.exe" -ArgumentList "shell:AppsFolder\$($paths.AppId)"
  Write-Host "Opened Windows Claude with the independent $ProfileMode provider profile." -ForegroundColor Green
}

function Restore-WindowsClaudeOfficial {
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body { Restore-WindowsClaudeOfficial })
  }
  Stop-WindowsClaudeGateway
  $paths = Get-ClaudeDesktopPaths
  $files = Get-ClaudeManagedFiles -Paths $paths
  $snapshots = Get-FileSnapshots -Files $files
  $backup = Backup-ClaudeSnapshots -Snapshots $snapshots
  try {
    Set-DeploymentMode -Path $paths.NormalConfig -DeploymentMode "1p"
    Set-DeploymentMode -Path $paths.ThreepConfig -DeploymentMode "1p"
    $knownIds = @($script:ProfileIds.Values)
    foreach ($profileMode in $script:Modes) {
      $profilePath = Join-Path $paths.Library "$($script:ProfileIds[$profileMode]).json"
      if (Test-Path -LiteralPath $profilePath -PathType Leaf) { Remove-Item -LiteralPath $profilePath -Force }
    }
    if (Test-Path -LiteralPath $paths.Meta -PathType Leaf) {
      $meta = Read-JsonObject -Path $paths.Meta
      $entries = @(Get-MetaEntries -Meta $meta | Where-Object {
        $idProperty = $_.PSObject.Properties["id"]
        $null -eq $idProperty -or $knownIds -notcontains [string]$idProperty.Value
      })
      Set-JsonProperty -Object $meta -Name "entries" -Value @($entries)
      $applied = $meta.PSObject.Properties["appliedId"]
      if ($null -ne $applied -and $knownIds -contains [string]$applied.Value) {
        $meta.PSObject.Properties.Remove("appliedId")
      }
      Write-AtomicJson -Path $paths.Meta -Value $meta
    }
  } catch {
    $primary = $_.Exception.Message
    try { Restore-FileSnapshots -Snapshots $snapshots } catch {
      throw "Official-mode restore failed ($primary) and rollback failed: $($_.Exception.Message)"
    }
    throw "Official-mode restore failed; prior config restored: $primary"
  }
  Write-Host "Windows Claude official 1P mode restored; WSL was not inspected or changed." -ForegroundColor Green
  Write-Host "Three Windows API-key settings and the Windows Codex login binding remain available for later reuse."
  if ($backup) { Write-Host "Claude config backup: $backup" }
  if (Get-Process -Name Claude -ErrorAction SilentlyContinue) {
    Write-Warning "Claude is still running and may retain the previous 3P session in memory. Fully quit every Claude process, then reopen the app to enter official 1P mode."
  } else {
    Write-Host "Reopen Claude to start a fresh official 1P session."
  }
}

function Show-WindowsClaudeStatus {
  if ($script:WindowsClaudeMutationLockDepth -eq 0) {
    return (Invoke-WithWindowsClaudeMutationLock -Body { Show-WindowsClaudeStatus })
  }
  Write-Host "Windows Claude owner: $StateRoot"
  Write-Host "Isolation: Windows-only; fixed gateway 127.0.0.1:18987; WSL dependency=none"
  try {
    $python = Get-WindowsPythonInfo
    Write-Host "Windows Python 3.10+: ready ($($python.Version); $($python.Path))"
  } catch {
    Write-Host "Windows Python 3.10+: missing"
    Write-Warning $_.Exception.Message
  }
  if (-not (Test-Path -LiteralPath $ProfilesPath -PathType Leaf)) {
    Write-Host "Initialized: false"
    Write-Host "Run: .\WindowsClaude.ps1 -Action init"
    return
  }
  $state = Get-WindowsClaudeState
  $configuredCount = 0
  $codexAuth = Get-WindowsCodexAuthStatus
  foreach ($profileMode in $script:Modes) {
    $configured = Test-ProfileConfigured -State $state -ProfileMode $profileMode
    if ($configured) { $configuredCount++ }
    $profile = Get-ModeProfile -State $state -ProfileMode $profileMode
    $route = if (-not $configured) {
      "empty"
    } else {
      (@($script:Roles | ForEach-Object {
        $modelName = "model_$_"
        $reasoningName = "reasoning_$_"
        "$_=($([string]$profile.$modelName),$([string]$profile.$reasoningName))"
      }) -join " ")
    }
    $credential = if ($profileMode -eq "codex") {
      if ($codexAuth.Configured) { "windows-codex-login" } else { "login-missing" }
    } else { "windows-dpapi" }
    Write-Host ("  {0,-8} configured={1,-5} auth={2,-19} protocol={3,-18} route={4}" -f $profileMode,$configured,$credential,$profile.protocol,$route)
  }
  Write-Host "Configured profiles: $configuredCount/4"
  Write-Host "Windows Codex auth: configured=$($codexAuth.Configured) mode=$($codexAuth.Mode) owner=$($codexAuth.Path)"
  $paths = Get-ClaudeDesktopPaths
  $appliedMode = "official-or-unconfigured"
  if (Test-Path -LiteralPath $paths.Meta -PathType Leaf) {
    $meta = Read-JsonObject -Path $paths.Meta
    $applied = $meta.PSObject.Properties["appliedId"]
    if ($null -ne $applied) {
      foreach ($profileMode in $script:Modes) {
        if ([string]$applied.Value -eq $script:ProfileIds[$profileMode]) { $appliedMode = $profileMode }
      }
    }
  }
  Write-Host "Claude version: $($paths.Version)"
  Write-Host "Claude profile: $appliedMode"
  if (Test-Path -LiteralPath $GatewayStatePath -PathType Leaf) {
    try {
      $gatewayState = Read-JsonObject -Path $GatewayStatePath
      $runtime = Read-JsonObject -Path $RuntimeConfigPath
      $health = Get-GatewayHealth -RuntimeConfig $runtime
      $identity = Test-GatewayProcessIdentity -ProcessId ([int]$gatewayState.pid)
      Write-Host "Gateway running: $([bool]($health -and $identity)) profile=$($gatewayState.profile) pid=$($gatewayState.pid)"
      Write-Host "Gateway log: $($gatewayState.log)"
    } catch {
      Write-Warning "Gateway state is stale or invalid: $($_.Exception.Message)"
    }
  } else {
    Write-Host "Gateway running: false"
  }
}

function Show-WindowsClaudeMenu {
  while ($true) {
    Write-Host ""
    Write-Host "Independent Windows Claude provider stack"
    Write-Host "  1  Initialize four isolated profiles (Codex gets three independent model routes)"
    Write-Host "  2  Configure DeepSeek   3 Kimi   4 GLM   5 Codex login"
    Write-Host "  6  Start DeepSeek       7 Kimi   8 GLM   9 Codex login"
    Write-Host "  10 Status   11 Stop Windows gateway   12 Restore official 1P mode"
    Write-Host "  0  Back"
    $selection = Read-Host "Select"
    try {
      switch ($selection) {
        "1" { Initialize-WindowsClaude }
        "2" { Configure-WindowsClaudeProfile -ProfileMode deepseek }
        "3" { Configure-WindowsClaudeProfile -ProfileMode kimi }
        "4" { Configure-WindowsClaudeProfile -ProfileMode glm }
        "5" { Configure-WindowsClaudeProfile -ProfileMode codex }
        "6" { Start-WindowsClaude -ProfileMode deepseek }
        "7" { Start-WindowsClaude -ProfileMode kimi }
        "8" { Start-WindowsClaude -ProfileMode glm }
        "9" { Start-WindowsClaude -ProfileMode codex }
        "10" { Show-WindowsClaudeStatus }
        "11" { Stop-WindowsClaudeGateway }
        "12" { Restore-WindowsClaudeOfficial }
        "0" { return }
        default { Write-Warning "Unknown selection" }
      }
    } catch {
      Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
  }
}

function Show-Help {
  @"
Independent Windows Claude provider stack

Initialize four Windows-only profiles (three empty API-key providers plus independent Codex Opus/Sonnet/Haiku routes):
  .\WindowsClaude.ps1 -Action init

Configure later; provider API keys are DPAPI-encrypted, while codex reuses `codex login`:
  .\WindowsClaude.ps1 -Action configure -Mode deepseek|kimi|glm|codex

Start one configured profile and launch the official Windows Claude application:
  .\WindowsClaude.ps1 -Action start -Mode deepseek|kimi|glm|codex

Inspect or stop only the Windows gateway:
  .\WindowsClaude.ps1 -Action status
  .\WindowsClaude.ps1 -Action stop

Restore official Claude account mode without deleting provider settings or the Codex binding:
  .\WindowsClaude.ps1 -Action official

This controller never calls WSL. Windows port 18987 and the legacy-compatible state namespace
%LOCALAPPDATA%\ScienceCodexFinalKit\WindowsClaude are separate from WSL Science.
"@
}

if ($MyInvocation.InvocationName -eq ".") { return }

try {
  switch ($Action) {
    "menu" { Show-WindowsClaudeMenu }
    "init" { Initialize-WindowsClaude }
    "configure" {
      if (-not $Mode) { throw "configure requires -Mode deepseek|kimi|glm|codex" }
      Configure-WindowsClaudeProfile -ProfileMode $Mode
    }
    "start" {
      if (-not $Mode) { throw "start requires -Mode deepseek|kimi|glm|codex" }
      Start-WindowsClaude -ProfileMode $Mode
    }
    "status" { Show-WindowsClaudeStatus }
    "stop" { Stop-WindowsClaudeGateway }
    "official" { Restore-WindowsClaudeOfficial }
    "help" { Show-Help }
  }
} catch {
  [Console]::Error.WriteLine("ERROR: " + $_.Exception.Message)
  exit 1
}
