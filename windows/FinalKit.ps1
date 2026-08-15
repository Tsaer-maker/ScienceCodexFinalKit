[CmdletBinding()]
param(
  [ValidateSet(
    "help", "menu", "clear", "build", "bootstrap",
    "configure-deepseek", "configure-kimi", "configure-glm", "configure-codex", "configure-codex-device",
    "migrate-windows-codex-auth-to-wsl",
    "login-linux-codex", "deepseek", "kimi", "glm", "codex", "claude", "science",
    "restart", "smoke", "test-deepseek", "test-kimi", "test-glm", "test-codex", "test-codex-tiers",
    "models", "discover-models", "update-runtime", "update-models", "update-tools",
    "status", "doctor", "stop", "browser-start", "browser-science", "browser-status", "browser-stop",
    "browser-mcp-info", "init-project", "windows-review",
    "windows-claude-menu", "windows-claude-init", "windows-claude-configure", "windows-claude",
    "windows-claude-status", "windows-claude-stop", "windows-claude-official"
  )]
  [string]$Action = "help",

  [ValidatePattern('^[A-Za-z0-9._-]+$')]
  [string]$Distro = "Ubuntu-24.04",

  [string]$DistroLocation = "",

  [ValidatePattern('^$|^[a-z_][a-z0-9_-]*$')]
  [string]$LinuxUser = "",

  [string]$Project = "",

  [ValidateRange(1024, 65535)]
  [int]$BrowserPort = 9223,

  [switch]$NoBrowser,
  [switch]$AllUbuntu,
  [switch]$Force,
  [switch]$NoBackup,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$RemainingArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$KitRoot = Split-Path -Parent $PSScriptRoot
$VersionFile = Join-Path $KitRoot "VERSION"
$PackageVersion = if (Test-Path -LiteralPath $VersionFile) { (Get-Content -LiteralPath $VersionFile -Raw).Trim() } else { "3.x" }
$BrowserStateRoot = Join-Path $env:LOCALAPPDATA "ScienceCodexFinalKit"
$BrowserProfile = Join-Path $BrowserStateRoot "ChromeProfile"
$BrowserState = Join-Path $BrowserStateRoot "browser.json"
$script:ResolvedLinuxUser = ""
$script:RouteRoles = @("opus", "sonnet", "haiku")
$script:ProviderReasoning = @{
  deepseek = @("auto", "none", "high", "max")
  kimi = @("auto", "none", "low", "high", "max")
  glm = @("auto", "none", "high", "max")
  codex = @("none", "low", "medium", "high", "xhigh", "max", "ultra")
}

function ConvertTo-NativeArgumentString {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)
  return (($Arguments | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
  }) -join ' ')
}

function Remove-WslTerminalSequences {
  param([AllowEmptyString()][string]$Text)
  if ($null -eq $Text) { return "" }
  # Recent wsl.exe builds can emit virtual-terminal setup sequences even when
  # redirected.  Keep the diagnostic text, but remove console-only control data.
  $value = [regex]::Replace($Text, '\x1B\][^\x07]*(?:\x07|\x1B\\)', '')
  $value = [regex]::Replace($value, '\x1B\[[0-?]*[ -/]*[@-~]', '')
  $value = $value.Replace("`0", "").Replace("`r`r`n", "`r`n")
  return $value
}

function Get-ValidUtf8PrefixLength {
  param([Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes)
  $index = 0
  while ($index -lt $Bytes.Length) {
    $first = [int]$Bytes[$index]
    if ($first -le 0x7F) { $index++; continue }
    if ($first -ge 0xC2 -and $first -le 0xDF) {
      $needed = 1
    } elseif ($first -ge 0xE0 -and $first -le 0xEF) {
      $needed = 2
    } elseif ($first -ge 0xF0 -and $first -le 0xF4) {
      $needed = 3
    } else {
      return $index
    }
    if ($index + $needed -ge $Bytes.Length) { return $index }
    for ($offset = 1; $offset -le $needed; $offset++) {
      $continuation = [int]$Bytes[$index + $offset]
      if ($continuation -lt 0x80 -or $continuation -gt 0xBF) { return $index }
    }
    if ($needed -eq 2) {
      $second = [int]$Bytes[$index + 1]
      if (($first -eq 0xE0 -and $second -lt 0xA0) -or ($first -eq 0xED -and $second -gt 0x9F)) {
        return $index
      }
    }
    if ($needed -eq 3) {
      $second = [int]$Bytes[$index + 1]
      if (($first -eq 0xF0 -and $second -lt 0x90) -or ($first -eq 0xF4 -and $second -gt 0x8F)) {
        return $index
      }
    }
    $index += $needed + 1
  }
  return $index
}

function ConvertFrom-WslBytes {
  param(
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes,
    [System.Text.Encoding]$PreferredEncoding = $null
  )
  if ($Bytes.Length -eq 0) { return "" }
  if ($PreferredEncoding) {
    return Remove-WslTerminalSequences -Text $PreferredEncoding.GetString($Bytes)
  }

  if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE) {
    return Remove-WslTerminalSequences -Text ([System.Text.Encoding]::Unicode.GetString($Bytes, 2, $Bytes.Length - 2))
  }
  if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
    return Remove-WslTerminalSequences -Text ([System.Text.Encoding]::UTF8.GetString($Bytes, 3, $Bytes.Length - 3))
  }

  # Traditional Windows wsl.exe management output is UTF-16LE.  New Store WSL
  # builds can instead emit UTF-8, and one failure can even contain a UTF-8
  # console prefix followed by a UTF-16LE Win32 diagnostic.
  $oddNulls = 0
  $evenNulls = 0
  for ($index = 0; $index -lt $Bytes.Length; $index++) {
    if ($Bytes[$index] -eq 0) {
      if (($index % 2) -eq 0) { $evenNulls++ } else { $oddNulls++ }
    }
  }
  if (($Bytes.Length % 2) -eq 0 -and $oddNulls -ge 2 -and $oddNulls -gt ($evenNulls * 3)) {
    return Remove-WslTerminalSequences -Text ([System.Text.Encoding]::Unicode.GetString($Bytes))
  }

  $utf8Prefix = Get-ValidUtf8PrefixLength -Bytes $Bytes
  if ($utf8Prefix -eq $Bytes.Length) {
    return Remove-WslTerminalSequences -Text ([System.Text.Encoding]::UTF8.GetString($Bytes))
  }
  if ($utf8Prefix -eq 0 -and ($Bytes.Length % 2) -eq 0) {
    return Remove-WslTerminalSequences -Text ([System.Text.Encoding]::Unicode.GetString($Bytes))
  }
  $suffixLength = $Bytes.Length - $utf8Prefix
  if ($utf8Prefix -gt 0 -and $suffixLength -ge 2) {
    $utf16Length = $suffixLength - ($suffixLength % 2)
    $prefix = [System.Text.Encoding]::UTF8.GetString($Bytes, 0, $utf8Prefix)
    $suffix = [System.Text.Encoding]::Unicode.GetString($Bytes, $utf8Prefix, $utf16Length)
    $tail = ""
    if ($utf16Length -lt $suffixLength) {
      $tail = [System.Text.Encoding]::UTF8.GetString($Bytes, $utf8Prefix + $utf16Length, $suffixLength - $utf16Length)
    }
    return Remove-WslTerminalSequences -Text ($prefix + $suffix + $tail)
  }
  return Remove-WslTerminalSequences -Text ([System.Text.Encoding]::UTF8.GetString($Bytes))
}

function Invoke-WslCapture {
  param(
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [System.Text.Encoding]$Encoding = $null,
    [ValidateRange(0, 86400)][int]$TimeoutSeconds = 0,
    [AllowNull()][byte[]]$StandardInputBytes = $null
  )
  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if (-not $wsl) {
    return [pscustomobject]@{
      ExitCode = 9009
      StdOut = ""
      StdErr = "wsl.exe was not found"
    }
  }
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $wsl.Source
  $startInfo.Arguments = ConvertTo-NativeArgumentString -Arguments $Arguments
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.RedirectStandardInput = ($null -ne $StandardInputBytes)
  $startInfo.CreateNoWindow = $true
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $startInfo
  [void]$process.Start()
  # Capture raw bytes because current wsl.exe versions differ between UTF-16LE
  # and UTF-8, and some errors contain both encodings in one invocation.
  $stdoutBuffer = New-Object System.IO.MemoryStream
  $stderrBuffer = New-Object System.IO.MemoryStream
  $stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync($stdoutBuffer)
  $stderrTask = $process.StandardError.BaseStream.CopyToAsync($stderrBuffer)
  if ($null -ne $StandardInputBytes) {
    try {
      if ($StandardInputBytes.Length -gt 0) {
        $process.StandardInput.BaseStream.Write($StandardInputBytes, 0, $StandardInputBytes.Length)
        $process.StandardInput.BaseStream.Flush()
      }
    } finally {
      $process.StandardInput.Close()
    }
  }
  $timedOut = $false
  if ($TimeoutSeconds -gt 0) {
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
      $timedOut = $true
      try { $process.Kill() } catch { }
      $process.WaitForExit()
    }
  } else {
    $process.WaitForExit()
  }
  [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask))
  $stdoutBytes = $stdoutBuffer.ToArray()
  $stderrBytes = $stderrBuffer.ToArray()
  $stdoutBuffer.Dispose()
  $stderrBuffer.Dispose()
  $stdout = ConvertFrom-WslBytes -Bytes $stdoutBytes -PreferredEncoding $Encoding
  $stderr = ConvertFrom-WslBytes -Bytes $stderrBytes -PreferredEncoding $Encoding
  return [pscustomobject]@{
    ExitCode = if ($timedOut) { 124 } else { $process.ExitCode }
    StdOut = $stdout
    StdErr = if ($timedOut) { "wsl.exe timed out after $TimeoutSeconds seconds`n$stderr".Trim() } else { $stderr }
  }
}

function Get-WslResultDetail {
  param([Parameter(Mandatory = $true)]$Result)
  return (($Result.StdErr + "`n" + $Result.StdOut) -replace "`0", "").Trim()
}

function New-UnicodeText {
  param([Parameter(Mandatory = $true)][int[]]$CodePoints)
  return (-join ([char[]]$CodePoints))
}

function Test-WslNoDistroDiagnostic {
  param([AllowEmptyString()][string]$Text)
  if ($Text -match '(?i)no installed distributions|does not have any installed distributions|WSL_E_DEFAULT_DISTRO_NOT_FOUND') {
    return $true
  }
  $noInstalled = New-UnicodeText @(0x6CA1, 0x6709, 0x5DF2, 0x5B89, 0x88C5)
  $notInstalled = New-UnicodeText @(0x672A, 0x5B89, 0x88C5, 0x4EFB, 0x4F55)
  $distribution = New-UnicodeText @(0x5206, 0x53D1)
  return (($Text.Contains($noInstalled) -or $Text.Contains($notInstalled)) -and $Text.Contains($distribution))
}

function Test-WslPreparationDiagnostic {
  param([AllowEmptyString()][string]$Text)
  if ($Text -match '(?i)requires elevation|operation requires elevation|WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED|optional component|required feature|Microsoft-Windows-Subsystem-Linux|VirtualMachinePlatform|0x800702e4') {
    return $true
  }
  $needsElevation = New-UnicodeText @(0x9700, 0x8981, 0x63D0, 0x5347)
  $optionalComponent = New-UnicodeText @(0x53EF, 0x9009, 0x7EC4, 0x4EF6)
  $notEnabled = New-UnicodeText @(0x672A, 0x542F, 0x7528)
  $subsystem = New-UnicodeText @(0x5B50, 0x7CFB, 0x7EDF)
  return $Text.Contains($needsElevation) -or $Text.Contains($optionalComponent) -or ($Text.Contains($notEnabled) -and $Text.Contains($subsystem))
}

function Test-WslRestartDiagnostic {
  param([AllowEmptyString()][string]$Text)
  if ($Text -match '(?i)restart|reboot|changes will not be effective') { return $true }
  $restartShort = New-UnicodeText @(0x91CD, 0x542F)
  $restartLong = New-UnicodeText @(0x91CD, 0x65B0, 0x542F, 0x52A8)
  $rebootLong = New-UnicodeText @(0x91CD, 0x65B0, 0x5F15, 0x5BFC)
  return $Text.Contains($restartShort) -or $Text.Contains($restartLong) -or $Text.Contains($rebootLong)
}

function Test-WindowsAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ElevatedWslPreparation {
  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if (-not $wsl) { throw 'wsl.exe was not found. FinalKit requires Windows 10 2004+ or Windows 11.' }
  Write-Host 'Windows must enable/update the WSL platform before a user distribution can be installed.' -ForegroundColor Yellow
  Write-Host 'FinalKit will request Administrator approval for: wsl.exe --install --no-distribution'
  Write-Host 'The elevated process installs no Ubuntu distribution and writes no FinalKit credentials.'
  if (Test-WindowsAdministrator) {
    $result = Invoke-WslCapture -Arguments @('--install', '--no-distribution')
    if ($result.StdOut) { Write-Host $result.StdOut.TrimEnd() }
    if ($result.StdErr) { Write-Host $result.StdErr.TrimEnd() }
    if ($result.ExitCode -notin @(0, 3010, 1641)) {
      $detail = Get-WslResultDetail $result
      if (-not $detail) { $detail = "wsl.exe exited with code $($result.ExitCode)" }
      throw "Elevated WSL platform preparation failed.`n`nWindows reported:`n$detail"
    }
    return
  }
  try {
    $process = Start-Process -FilePath $wsl.Source -ArgumentList '--install --no-distribution' -Verb RunAs -WindowStyle Hidden -Wait -PassThru
  } catch {
    throw 'Administrator approval for WSL platform preparation was cancelled or could not start. No distribution was created.'
  }
  # 3010/1641 are Windows Installer success codes whose only remaining action
  # is a restart.  The caller re-reads `wsl --status` and presents that boundary.
  if ($process.ExitCode -notin @(0, 3010, 1641)) {
    throw "Elevated WSL platform preparation failed (exit $($process.ExitCode)). No distribution was created."
  }
}

function Ensure-WslPlatform {
  $status = Invoke-WslCapture -Arguments @('--status') -TimeoutSeconds 20
  if ($status.ExitCode -eq 0 -or (Test-WslNoDistroDiagnostic -Text (Get-WslResultDetail $status))) { return }
  $detail = Get-WslResultDetail $status
  if ($status.ExitCode -eq 124) {
    throw @"
The WSL status probe timed out. FinalKit did not create or remove a distribution.

Windows reported:
$detail

Run `wsl --shutdown`, restart Windows if needed, and then run Build again. Do not use Clear for a timeout.
"@
  }
  if (-not (Test-WslPreparationDiagnostic -Text $detail)) {
    if (-not $detail) { $detail = "wsl.exe --status exited with code $($status.ExitCode)" }
    throw "The Windows WSL platform is not ready.`n`nWindows reported:`n$detail"
  }

  Invoke-ElevatedWslPreparation
  Start-Sleep -Milliseconds 800
  $after = Invoke-WslCapture -Arguments @('--status') -TimeoutSeconds 20
  $afterDetail = Get-WslResultDetail $after
  if ($after.ExitCode -eq 0 -or (Test-WslNoDistroDiagnostic -Text $afterDetail)) { return }
  if (-not $afterDetail -or (Test-WslPreparationDiagnostic -Text $afterDetail) -or (Test-WslRestartDiagnostic -Text $afterDetail)) {
    $restartHint = if (Test-WslRestartDiagnostic -Text $afterDetail) { 'Windows explicitly requires a restart.' } else { 'The WSL platform is not active in this user session yet and normally requires a restart.' }
    throw @"
WSL system preparation completed, but Build must stop before installing Ubuntu.
$restartHint

Restart Windows, sign back in as the same normal Windows user, then run Build again.
FinalKit deliberately did not install a distribution from the elevated process.

Current Windows diagnostic:
$afterDetail
"@
  }
  throw "WSL platform preparation could not be verified.`n`nWindows reported:`n$afterDetail"
}

function Invoke-WslDistroInstall {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)
  Write-Host 'The Ubuntu download can take several minutes; FinalKit will preserve the complete WSL diagnostic.'
  $result = Invoke-WslCapture -Arguments $Arguments
  if ($result.StdOut) { Write-Host $result.StdOut.TrimEnd() }
  if ($result.StdErr) { Write-Host $result.StdErr.TrimEnd() }
  if ($result.ExitCode -eq 0) { return }

  $detail = Get-WslResultDetail $result
  if ($result.ExitCode -in @(3010, 1641) -or (Test-WslRestartDiagnostic -Text $detail)) {
    throw @"
Windows must restart before Ubuntu installation can continue.
Restart Windows, sign back in as the same normal Windows user, then run Build again.
Do not run Clear: the partially completed WSL system preparation is recoverable.

Current Windows diagnostic:
$detail
"@
  }
  if (Test-WslPreparationDiagnostic -Text $detail) {
    Write-Warning 'Windows blocked the user-scoped distribution install because the WSL platform still needs Administrator preparation.'
    Invoke-ElevatedWslPreparation
    Start-Sleep -Milliseconds 800
    $after = Invoke-WslCapture -Arguments @('--status') -TimeoutSeconds 20
    $afterDetail = Get-WslResultDetail $after
    $platformReady = $after.ExitCode -eq 0 -or (Test-WslNoDistroDiagnostic -Text $afterDetail)
    if ($platformReady) {
      Write-Host 'WSL platform preparation is active; retrying Ubuntu as the original Windows user.'
      $result = Invoke-WslCapture -Arguments $Arguments
      if ($result.StdOut) { Write-Host $result.StdOut.TrimEnd() }
      if ($result.StdErr) { Write-Host $result.StdErr.TrimEnd() }
      if ($result.ExitCode -eq 0) { return }
      $detail = Get-WslResultDetail $result
      if ($result.ExitCode -in @(3010, 1641) -or (Test-WslPreparationDiagnostic -Text $detail) -or (Test-WslRestartDiagnostic -Text $detail)) {
        throw @"
WSL system preparation completed, but Windows must restart before Ubuntu can be installed.
Restart Windows, sign back in as the same normal Windows user, then run Build again.
FinalKit deliberately did not install Ubuntu from the elevated process.

Current Windows diagnostic:
$detail
"@
      }
    } elseif ((Test-WslPreparationDiagnostic -Text $afterDetail) -or (Test-WslRestartDiagnostic -Text $afterDetail)) {
      throw @"
WSL system preparation completed, but Windows must restart before Ubuntu can be installed.
Restart Windows, sign back in as the same normal Windows user, then run Build again.
FinalKit deliberately did not install Ubuntu from the elevated process.

Current Windows diagnostic:
$afterDetail
"@
    } else {
      if (-not $afterDetail) { $afterDetail = "wsl.exe --status exited with code $($after.ExitCode)" }
      throw "WSL platform preparation could not be verified.`n`nWindows reported:`n$afterDetail"
    }
  }

  Write-Warning 'The normal Store-backed Ubuntu install failed; retrying from the official WSL online source (--web-download).'
  $fallbackArguments = @($Arguments) + '--web-download'
  $fallback = Invoke-WslCapture -Arguments $fallbackArguments
  if ($fallback.StdOut) { Write-Host $fallback.StdOut.TrimEnd() }
  if ($fallback.StdErr) { Write-Host $fallback.StdErr.TrimEnd() }
  if ($fallback.ExitCode -ne 0) {
    $fallbackDetail = Get-WslResultDetail $fallback
    if ($fallback.ExitCode -in @(3010, 1641) -or (Test-WslRestartDiagnostic -Text $fallbackDetail)) {
      throw @"
Windows must restart before Ubuntu installation can continue.
Restart Windows, sign back in as the same normal Windows user, then run Build again.
Do not run Clear: the partially completed WSL system preparation is recoverable.

Current Windows diagnostic:
$fallbackDetail
"@
    }
    if (-not $fallbackDetail) { $fallbackDetail = "wsl.exe exited with code $($fallback.ExitCode)" }
    throw "WSL Ubuntu installation failed through both Store and --web-download paths.`n`nWindows reported:`n$fallbackDetail"
  }
}

function Invoke-WslLinuxCapture {
  param(
    [Parameter(Mandatory = $true)][string[]]$Command,
    [string]$AsUser = "",
    [ValidateRange(0, 86400)][int]$TimeoutSeconds = 0,
    [AllowNull()][byte[]]$StandardInputBytes = $null
  )
  $arguments = @("-d", $Distro)
  if ($AsUser) { $arguments += @("-u", $AsUser) }
  $arguments += "--"
  $arguments += $Command
  return Invoke-WslCapture -Arguments $arguments -Encoding (New-Object System.Text.UTF8Encoding($false)) -TimeoutSeconds $TimeoutSeconds -StandardInputBytes $StandardInputBytes
}

function Invoke-WslManagement {
  param(
    [Parameter(Mandatory = $true)][string[]]$Arguments,
    [string]$Failure = "WSL command failed"
  )
  $result = Invoke-WslCapture -Arguments $Arguments
  if ($result.StdOut) { Write-Host $result.StdOut.TrimEnd() }
  if ($result.StdErr) { Write-Host $result.StdErr.TrimEnd() }
  if ($result.ExitCode -ne 0) {
    throw "$Failure (exit $($result.ExitCode))"
  }
  return $result
}

function Get-WslDistroNames {
  $result = Invoke-WslCapture -Arguments @("--list", "--quiet") -TimeoutSeconds 15
  if ($result.ExitCode -ne 0) {
    # A ready WSL installation with zero distributions can return a non-zero
    # code and a localized explanatory message.  Registry truth distinguishes
    # that first-install state from a failure involving an existing distro.
    if (@(Get-WslRegistrations).Count -eq 0) {
      return @()
    }
    $detail = (($result.StdErr + "`n" + $result.StdOut) -replace "`0", "").Trim()
    if (-not $detail) { $detail = "wsl.exe exited with code $($result.ExitCode)" }
    if ($result.ExitCode -eq 124) {
      throw @"
The WSL distribution probe timed out. FinalKit did not create, remove, or repair a distribution.

Windows reported:
$detail

Run `wsl --shutdown`, restart Windows if needed, and then run Build again. Do not use Clear for a timeout.
"@
    }
    throw @"
WSL is installed but not ready for FinalKit.

Windows reported:
$detail

Open PowerShell as Administrator and run:
  wsl --install --no-distribution

Then restart Windows and run Build again. FinalKit did not delete or create a distribution.
"@
  }
  return @(($result.StdOut -replace "`0", "") -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Test-WslDistro([string]$Name = $Distro) {
  return (Get-WslDistroNames) -contains $Name
}

function Get-WslRegistrations {
  $root = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss"
  if (-not (Test-Path -LiteralPath $root)) { return @() }
  return @(Get-ChildItem -LiteralPath $root | ForEach-Object {
    $item = Get-ItemProperty -LiteralPath $_.PSPath
    [pscustomobject]@{
      RegistryKey = $_.PSChildName
      Name = [string]$item.DistributionName
      BasePath = [string]$item.BasePath
      Version = [int]$item.Version
    }
  })
}

function Get-WslRegistration([string]$Name) {
  return @(Get-WslRegistrations | Where-Object { $_.Name -ceq $Name })
}

function Convert-ToWslMountPath {
  param([Parameter(Mandatory = $true)][string]$WindowsPath)
  $resolved = (Resolve-Path -LiteralPath $WindowsPath).Path
  if ($resolved -notmatch '^(?<drive>[A-Za-z]):\\(?<rest>.*)$') {
    throw "FinalKit must be stored on a mounted Windows drive: $resolved"
  }
  return "/mnt/$($Matches['drive'].ToLowerInvariant())/$($Matches['rest'].Replace('\', '/'))"
}

function Invoke-WslNative {
  param(
    [Parameter(Mandatory = $true)][string[]]$Command,
    [string]$AsUser = ""
  )
  $arguments = @("-d", $Distro)
  if ($AsUser) { $arguments += @("-u", $AsUser) }
  $arguments += "--"
  $arguments += $Command
  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if (-not $wsl) { throw "wsl.exe was not found" }
  $startInfo = New-Object System.Diagnostics.ProcessStartInfo
  $startInfo.FileName = $wsl.Source
  $startInfo.Arguments = ConvertTo-NativeArgumentString -Arguments $arguments
  $startInfo.UseShellExecute = $false
  # Inherit the console so long installs remain visible.  Starting wsl.exe via
  # Process avoids Windows PowerShell 5 promoting native stderr to an exception.
  $process = New-Object System.Diagnostics.Process
  $process.StartInfo = $startInfo
  [void]$process.Start()
  $process.WaitForExit()
  if ($process.ExitCode -ne 0) {
    throw "WSL command failed (exit $($process.ExitCode)): $($Command -join ' ')"
  }
}

function Get-WslOutput {
  param(
    [Parameter(Mandatory = $true)][string[]]$Command,
    [string]$AsUser = ""
  )
  $result = Invoke-WslLinuxCapture -Command $Command -AsUser $AsUser
  if ($result.ExitCode -ne 0) {
    $detail = (($result.StdErr + "`n" + $result.StdOut) -replace "`0", "").Trim()
    if ($detail) { Write-Host $detail }
    throw "WSL command failed (exit $($result.ExitCode)): $($Command -join ' ')"
  }
  return (($result.StdOut | Out-String) -replace "`0", "").Trim()
}

function Resolve-LinuxUser {
  if ($LinuxUser) { return $LinuxUser }
  if ($script:ResolvedLinuxUser) { return $script:ResolvedLinuxUser }
  if (Test-WslDistro) {
    $current = Get-WslOutput -Command @("id", "-un")
    if ($current -and $current -ne "root" -and $current -match '^[a-z_][a-z0-9_-]*$') {
      $script:ResolvedLinuxUser = $current
      return $current
    }
  }
  $candidate = $env:USERNAME.ToLowerInvariant() -replace '[^a-z0-9_-]', '-'
  if ($candidate -notmatch '^[a-z_]') { $candidate = "user-$candidate" }
  if ($candidate -in @("root", "daemon", "nobody")) { $candidate = "science-user" }
  $script:ResolvedLinuxUser = $candidate
  return $candidate
}

function Get-FkctlPath { return "/home/$(Resolve-LinuxUser)/.local/bin/fkctl" }

function Get-FkctlCapabilities {
  try {
    if (@(Get-WslRegistration -Name $Distro).Count -eq 0) { return @() }
    $user = Resolve-LinuxUser
    $fkctl = "/home/$user/.local/bin/fkctl"
    $result = Invoke-WslLinuxCapture -AsUser $user -Command @($fkctl, "capabilities") -TimeoutSeconds 12
    if ($result.ExitCode -ne 0) { return @() }
    $payload = (($result.StdOut | Out-String) -replace "`0", "").Trim() | ConvertFrom-Json
    return @($payload.capabilities | ForEach-Object { [string]$_ })
  } catch {
    return @()
  }
}

function Test-FkctlCapability {
  param([Parameter(Mandatory = $true)][string]$Capability)
  return (Get-FkctlCapabilities) -contains $Capability
}

function Assert-FkctlCapability {
  param(
    [Parameter(Mandatory = $true)][string]$Capability,
    [Parameter(Mandatory = $true)][string]$ActionLabel
  )
  if (-not (Test-FkctlCapability -Capability $Capability)) {
    throw "RUNTIME_UPDATE_REQUIRED: the installed fkctl does not support $ActionLabel (missing capability '$Capability'). Existing supported commands remain usable. Run menu 16 / -Action update-runtime. If FinalKit is not installed yet, use menu 2."
  }
}

function Invoke-Fkctl {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)
  Invoke-WslNative -AsUser (Resolve-LinuxUser) -Command (@(Get-FkctlPath) + $Arguments)
}

function Get-FkctlOutput {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)
  return Get-WslOutput -AsUser (Resolve-LinuxUser) -Command (@(Get-FkctlPath) + $Arguments)
}

function Get-WindowsCodexAuthOwnerPath {
  $codexHome = if (-not [string]::IsNullOrWhiteSpace([string]$env:CODEX_HOME)) {
    [string]$env:CODEX_HOME
  } else {
    Join-Path $env:USERPROFILE ".codex"
  }
  try { $resolvedHome = [IO.Path]::GetFullPath($codexHome) } catch {
    throw "Windows CODEX_HOME is malformed"
  }
  if ($resolvedHome -match '(?i)(\\\\wsl\$|\\\\wsl\.localhost|/mnt/|/home/|wsl\.exe)') {
    throw "The Windows Codex auth source cannot reference WSL"
  }
  return Join-Path $resolvedHome "auth.json"
}

function Get-WindowsCodexAuthPayload {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Windows Codex auth is missing: $Path. Run Windows 'codex login' first."
  }
  $item = Get-Item -LiteralPath $Path
  if ($item.Length -le 0 -or $item.Length -gt 1MB) {
    throw "Windows Codex auth has an invalid size"
  }

  $codex = Get-Command codex -ErrorAction SilentlyContinue
  if ($null -eq $codex) { throw "Windows Codex CLI was not found; install it and run: codex login" }
  & $codex.Source login status | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Windows Codex is not logged in. Run: codex login" }

  [byte[]]$bytes = [IO.File]::ReadAllBytes($Path)
  try {
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $auth = ($strictUtf8.GetString($bytes) | ConvertFrom-Json)
    $modeProperty = $auth.PSObject.Properties["auth_mode"]
    $tokensProperty = $auth.PSObject.Properties["tokens"]
    $tokens = if ($null -ne $tokensProperty) { $tokensProperty.Value } else { $null }
    $accessProperty = if ($null -ne $tokens) { $tokens.PSObject.Properties["access_token"] } else { $null }
    $refreshProperty = if ($null -ne $tokens) { $tokens.PSObject.Properties["refresh_token"] } else { $null }
    if ($null -eq $modeProperty -or [string]$modeProperty.Value -ne "chatgpt") {
      throw "Windows Codex is not using the official ChatGPT login"
    }
    if (
      $null -eq $accessProperty -or [string]::IsNullOrWhiteSpace([string]$accessProperty.Value) -or
      $null -eq $refreshProperty -or [string]::IsNullOrWhiteSpace([string]$refreshProperty.Value)
    ) {
      throw "Windows Codex ChatGPT token chain is incomplete"
    }
    return [pscustomobject]@{ Path = $Path; Bytes = $bytes }
  } catch {
    [Array]::Clear($bytes, 0, $bytes.Length)
    throw
  }
}

function Invoke-WindowsCodexAuthMigrationToWsl {
  Assert-FinalKitInstalled
  Assert-FkctlCapability -Capability "stdin-codex-auth-import" -ActionLabel "one-time Windows Codex auth import"
  $user = Resolve-LinuxUser
  $fkctl = Get-FkctlPath
  $sourcePath = Get-WindowsCodexAuthOwnerPath
  Write-Host "One-time Windows Codex auth -> WSL migration" -ForegroundColor Cyan
  Write-Host "  Source:      $sourcePath"
  Write-Host "  Destination: $Distro /home/$user/.finalkit-client/.codex/auth.json"
  Write-Host "This replaces the WSL Codex login once. It does not create startup sync; WSL keeps its own refresh and login commands."
  if (-not $Force) {
    $confirmation = Read-Host "Type MIGRATE to stop WSL Science, replace its Codex login, and restart Codex Science"
    if ($confirmation -cne "MIGRATE") {
      Write-Host "Migration cancelled; Windows and WSL auth were not changed."
      return
    }
  }

  $payload = Get-WindowsCodexAuthPayload -Path $sourcePath
  $stopped = $false
  $importCommitted = $false
  try {
    Invoke-Fkctl @("stop")
    $stopped = $true
    $result = Invoke-WslLinuxCapture `
      -AsUser $user `
      -Command @($fkctl, "import-codex-auth") `
      -TimeoutSeconds 60 `
      -StandardInputBytes $payload.Bytes
    if ($result.StdOut) { Write-Host $result.StdOut.TrimEnd() }
    if ($result.StdErr) { Write-Host $result.StdErr.TrimEnd() }
    if ($result.ExitCode -ne 0) {
      throw "WSL rejected the imported Windows Codex login (exit $($result.ExitCode))"
    }
    $importCommitted = $true
    Invoke-Fkctl @("start", "codex")
    Invoke-Fkctl @("status")
    Write-Host "WINDOWS_CODEX_AUTH_MIGRATION_OK distro=$Distro linux_user=$user" -ForegroundColor Green
    Write-Host "Windows and WSL now hold separate copies. Future WSL refresh or re-login does not change Windows, and no startup copy occurs."
    Write-Host "They initially share one OAuth token chain, not two newly issued sessions; if upstream refresh rotation invalidates one copy, re-login only on that side."
  } catch {
    $primary = $_.Exception.Message
    if ($stopped -and -not $importCommitted) {
      $restartFailure = ""
      try {
        Invoke-Fkctl @("start", "codex")
      } catch {
        $restartFailure = $_.Exception.Message
      }
      if ($restartFailure) {
        throw "$primary`nThe WSL importer restored the prior auth bytes, but restarting the prior Codex runtime also failed: $restartFailure"
      }
      throw "$primary`nThe WSL importer restored the prior auth bytes, and Codex Science was restarted with the prior login."
    }
    if ($importCommitted) {
      throw "$primary`nThe one-time auth import passed official Codex validation and remains committed; no recurring sync was enabled."
    }
    throw
  } finally {
    if ($null -ne $payload -and $null -ne $payload.Bytes) {
      [Array]::Clear($payload.Bytes, 0, $payload.Bytes.Length)
    }
  }
}

function Get-UbuntuTargets {
  if (-not $AllUbuntu) { return @(Get-WslRegistration -Name $Distro) }
  $targets = @()
  foreach ($registration in Get-WslRegistrations) {
    $savedDistro = $Distro
    $Distro = $registration.Name
    try {
      $result = Invoke-WslLinuxCapture -AsUser root -Command @("cat", "/etc/os-release")
    } finally {
      $Distro = $savedDistro
    }
    $osReleaseText = ($result.StdOut -replace "`0", "")
    if ($result.ExitCode -eq 0 -and $osReleaseText -match '(?m)^ID=ubuntu\s*$') {
      $targets += $registration
    }
  }
  return @($targets)
}

function Invoke-Clear {
  $targets = @(Get-UbuntuTargets)
  if ($targets.Count -eq 0) {
    Write-Host "No matching Ubuntu WSL distribution is registered."
    return
  }
  foreach ($target in $targets) {
    if (-not $target.Name -or -not $target.BasePath) { throw "Refusing a WSL registration with missing identity" }
    $fullBase = [System.IO.Path]::GetFullPath($target.BasePath)
    $root = [System.IO.Path]::GetPathRoot($fullBase)
    if ($fullBase -eq $root -or $fullBase.Length -lt 8) { throw "Unsafe WSL BasePath: $fullBase" }
    Write-Host "Will unregister Ubuntu: $($target.Name)" -ForegroundColor Yellow
    Write-Host "Registered BasePath: $fullBase"
    if (-not $Force) {
      $confirmation = Read-Host "Type DELETE $($target.Name) to continue"
      if ($confirmation -cne "DELETE $($target.Name)") { throw "Clear cancelled" }
    }
    if (-not $NoBackup) {
      $backupRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "ScienceCodexFinalKit\Backups"
      New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
      $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
      $backup = Join-Path $backupRoot "$($target.Name)-$stamp.tar"
      Write-Host "Exporting recoverable backup: $backup"
      Invoke-WslManagement -Arguments @("--export", $target.Name, $backup) -Failure "Backup export failed" | Out-Null
      if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
        throw "Backup export failed; distribution was not removed"
      }
    }
    Invoke-WslManagement -Arguments @("--terminate", $target.Name) -Failure "Could not terminate $($target.Name)" | Out-Null
    Invoke-WslManagement -Arguments @("--unregister", $target.Name) -Failure "Unregister failed: $($target.Name)" | Out-Null
    if ((Get-WslDistroNames) -contains $target.Name) { throw "Post-clear verification failed: $($target.Name)" }
    Write-Host "CLEAR_OK $($target.Name)" -ForegroundColor Green
  }
}

function Ensure-Distro {
  Ensure-WslPlatform
  if (Test-WslDistro) {
    Write-Host "WSL distribution already exists: $Distro"
    return
  }
  if ($DistroLocation) {
    if (Test-Path -LiteralPath $DistroLocation) {
      throw "Distribution is not registered, but target exists: $DistroLocation"
    }
    $parent = Split-Path -Parent $DistroLocation
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $resolvedParent = (Resolve-Path -LiteralPath $parent).Path.TrimEnd('\') + '\'
    $fullTarget = [System.IO.Path]::GetFullPath($DistroLocation)
    if (-not $fullTarget.StartsWith($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Resolved WSL target escaped its parent: $fullTarget"
    }
    Write-Host "Installing standard Ubuntu 24.04 as $Distro at $fullTarget ..."
    Invoke-WslDistroInstall -Arguments @("--install", "--distribution", "Ubuntu-24.04", "--name", $Distro, "--location", $fullTarget, "--no-launch")
  } else {
    if ($Distro -ne "Ubuntu-24.04") {
      throw "A custom -Distro requires an explicit -DistroLocation"
    }
    Write-Host "Installing standard Ubuntu-24.04 using the current Windows user's normal WSL location ..."
    Invoke-WslDistroInstall -Arguments @("--install", "--distribution", "Ubuntu-24.04", "--no-launch")
  }
  if (-not (Test-WslDistro)) { throw "WSL installation returned success but $Distro is not registered" }
}

function Ensure-LinuxUser {
  $user = Resolve-LinuxUser
  $passwd = Get-WslOutput -AsUser root -Command @("getent", "passwd")
  $userExists = @(($passwd -split "`n") | Where-Object { $_ -like "${user}:*" }).Count -gt 0
  if (-not $userExists) {
    Write-Host "Creating Linux user with a private home: $user"
    Invoke-WslNative -AsUser root -Command @("useradd", "--create-home", "--shell", "/bin/bash", $user)
  }
  $identity = Get-WslOutput -Command @("id", "-un")
  if ($identity -eq $user) {
    Write-Host "Default Linux user is already correct: $user"
    $script:ResolvedLinuxUser = $user
    return
  }
  Invoke-WslManagement `
    -Arguments @("--manage", $Distro, "--set-default-user", $user) `
    -Failure "Could not set default Linux user: $user" | Out-Null
  Invoke-WslManagement -Arguments @("--terminate", $Distro) -Failure "Could not reload $Distro" | Out-Null
  Start-Sleep -Milliseconds 700
  $identity = Get-WslOutput -Command @("id", "-un")
  if ($identity -ne $user) { throw "Default-user verification failed: expected $user, got $identity" }
  $script:ResolvedLinuxUser = $user
}

function Invoke-Build {
  Write-Host "FinalKit package folder: $KitRoot" -ForegroundColor DarkGray
  Ensure-Distro
  Ensure-LinuxUser
  $user = Resolve-LinuxUser
  $wslKitRoot = Convert-ToWslMountPath -WindowsPath $KitRoot
  $installer = "$wslKitRoot/wsl/install-final-stack.sh"
  Write-Host "[1/4] Ubuntu 24.04 system dependencies ..."
  Invoke-WslNative -AsUser root -Command @("bash", $installer, "--system")
  Write-Host "[2/4] Per-user Claude Science, Claude Code, Codex and provider switcher ..."
  Invoke-WslNative -AsUser $user -Command @("bash", $installer, "--user")
  Write-Host "[3/4] Pinned browser bridge dependencies ..."
  Invoke-Fkctl -Arguments @("doctor")
  Write-Host "[4/4] Windows Codex collaboration lane ..."
  $windowsCodex = Get-Command codex -ErrorAction SilentlyContinue
  if ($windowsCodex) {
    & $windowsCodex.Source --version
    & $windowsCodex.Source login status
  } else {
    Write-Warning "Windows Codex was not found; WSL stack works, but windows-review needs Codex Desktop/CLI."
  }
  Write-Host "BUILD_OK distro=$Distro linux_user=$user" -ForegroundColor Green
  Write-Host "Each additional Linux user can rerun Build with -LinuxUser <name>; credentials remain per-user."
}

function Assert-FinalKitInstalled {
  if (-not (Test-WslDistro)) {
    throw "FinalKit update needs an existing $Distro distribution. Use menu 2 for the first installation."
  }
  $user = Resolve-LinuxUser
  $fkctl = "/home/$user/.local/bin/fkctl"
  $result = Invoke-WslLinuxCapture -AsUser $user -Command @("test", "-x", $fkctl) -TimeoutSeconds 12
  if ($result.ExitCode -ne 0) {
    throw "FinalKit is not installed for Linux user '$user'. Use menu 2 for the first installation."
  }
}

function Invoke-RuntimeUpdate {
  Assert-FinalKitInstalled
  $user = Resolve-LinuxUser
  $wslKitRoot = Convert-ToWslMountPath -WindowsPath $KitRoot
  Write-Host "Updating only the FinalKit manager, gateways and managed connector patch ..." -ForegroundColor Cyan
  Write-Host "Ubuntu, official clients, API keys, OAuth and persistent model routes are not rebuilt."
  Invoke-WslNative -AsUser $user -Command @("bash", "$wslKitRoot/wsl/install-final-stack.sh", "--runtime")
  Write-Host "RUNTIME_UPDATE_OK package=$PackageVersion linux_user=$user" -ForegroundColor Green
  Write-Host "The runtime is stopped; start the provider you want from menu 7-10."
}

function Invoke-ToolsUpdate {
  Assert-FinalKitInstalled
  $user = Resolve-LinuxUser
  $wslKitRoot = Convert-ToWslMountPath -WindowsPath $KitRoot
  Write-Host "This network operation updates official Claude Science, Claude Code, Codex CLI," -ForegroundColor Cyan
  Write-Host "plus the Node/Chrome MCP versions pinned by this package. FinalKit auth and model routes are preserved."
  if (-not $Force) {
    $confirmation = Read-Host "Continue with the official tool update? [y/N]"
    if ($confirmation -notmatch '^(?i:y|yes)$') {
      Write-Host "Tool update cancelled; nothing was changed."
      return
    }
  }
  Invoke-WslNative -AsUser $user -Command @("bash", "$wslKitRoot/wsl/install-final-stack.sh", "--tools")
  Write-Host "TOOLS_UPDATE_OK linux_user=$user" -ForegroundColor Green
}

function Show-ModelRoutes {
  Assert-FkctlCapability -Capability "persistent-model-routes" -ActionLabel "persistent provider model routes"
  Invoke-Fkctl @("models")
}

function Invoke-ModelUpdateInteractive {
  Assert-FkctlCapability -Capability "model-route-update" -ActionLabel "independent model-route updates"
  Assert-FkctlCapability -Capability "per-role-provider-routes" -ActionLabel "Opus/Sonnet/Haiku Model routes"
  Assert-FkctlCapability -Capability "per-role-reasoning" -ActionLabel "per-tier Reasoning routes"
  $routes = (Get-FkctlOutput @("models", "--json")) | ConvertFrom-Json
  Write-Host "Current persistent model routes:" -ForegroundColor Cyan
  $routeTargets = @($routes.providers.deepseek, $routes.providers.kimi, $routes.providers.glm, $routes.codex)
  $routeLabels = @("DeepSeek", "Kimi", "GLM", "Codex")
  for ($index = 0; $index -lt $routeTargets.Count; $index++) {
    $target = $routeTargets[$index]
    $parts = @($script:RouteRoles | ForEach-Object {
      $reasoningName = "reasoning_$_"
      "$_=($([string]$target.$_),$([string]$target.$reasoningName))"
    })
    Write-Host ("  {0} {1,-8} {2}" -f ($index + 1), $routeLabels[$index], ($parts -join " "))
  }
  $selection = Read-Host "Provider [1-4]"
  $arguments = @("update-models")
  switch ($selection) {
    "1" { $provider = "deepseek"; $current = $routes.providers.deepseek }
    "2" { $provider = "kimi"; $current = $routes.providers.kimi }
    "3" { $provider = "glm"; $current = $routes.providers.glm }
    "4" { $provider = "codex"; $current = $routes.codex }
    default { throw "Provider selection must be 1, 2, 3 or 4" }
  }
  $arguments += $provider
  $catalog = $null
  if ($provider -ne "codex" -and (Test-FkctlCapability -Capability "provider-model-discovery")) {
    $discover = Read-Host "Read this account's official callable Models now? [Y/n]"
    if ($discover -notmatch '^(?i:n|no)$') {
      try {
        $catalog = (Get-FkctlOutput @("discover-models", $provider, "--json")) | ConvertFrom-Json
        Write-Host "Models:" -ForegroundColor Cyan
        for ($index = 0; $index -lt @($catalog.models).Count; $index++) {
          Write-Host ("  {0,2} {1}" -f ($index + 1), $catalog.models[$index])
        }
        Write-Host "Catalog source: $($catalog.catalog_source)" -ForegroundColor DarkGray
      } catch {
        Write-Warning "Official catalog discovery failed; manual Model entry remains available. $($_.Exception.Message)"
      }
    }
  }
  Write-Host ("Reasoning: " + (@($script:ProviderReasoning[$provider]) -join ", "))
  foreach ($role in $script:RouteRoles) {
    $roleLabel = (Get-Culture).TextInfo.ToTitleCase($role)
    $model = Read-Host "$roleLabel Model or list number [$([string]$current.$role)]"
    if (-not $model) { $model = [string]$current.$role }
    if ($catalog -and $model -match '^\d+$') {
      $modelIndex = [int]$model
      if ($modelIndex -lt 1 -or $modelIndex -gt @($catalog.models).Count) {
        throw "$roleLabel Model selection is outside the displayed list"
      }
      $model = [string]$catalog.models[$modelIndex - 1]
    }
    $reasoningName = "reasoning_$role"
    $reasoning = Read-Host "$roleLabel Reasoning [$([string]$current.$reasoningName)]"
    if (-not $reasoning) { $reasoning = [string]$current.$reasoningName }
    $reasoning = $reasoning.ToLowerInvariant()
    if ($reasoning -notin @($script:ProviderReasoning[$provider])) {
      throw "$roleLabel Reasoning must be one of: $(@($script:ProviderReasoning[$provider]) -join ', ')"
    }
    $arguments += @("--$role", $model, "--reasoning-$role", $reasoning)
  }
  $preview = (Get-FkctlOutput ($arguments + @("--dry-run", "--json"))) | ConvertFrom-Json
  if (-not $preview.changed) {
    Write-Host "The selected route already has those values; nothing to update."
    return
  }
  Write-Host "Preview:" -ForegroundColor Cyan
  $previewRoute = if ($provider -eq "codex") { $preview.routes.codex } else { $preview.routes.providers.$provider }
  foreach ($role in $script:RouteRoles) {
    $roleLabel = (Get-Culture).TextInfo.ToTitleCase($role)
    $reasoningName = "reasoning_$role"
    Write-Host "  $roleLabel  Model=$([string]$previewRoute.$role)  Reasoning=$([string]$previewRoute.$reasoningName)"
  }
  $confirmation = Read-Host "Apply this persistent route and restart the active FinalKit runtime if needed? [y/N]"
  if ($confirmation -notmatch '^(?i:y|yes)$') {
    Write-Host "Model update cancelled; the preview did not write anything."
    return
  }
  Invoke-Fkctl ($arguments + @("--restart"))
}

function Open-NativeClaude {
  param(
    [ValidateSet("deepseek", "kimi", "glm", "codex")][string]$Mode,
    [string[]]$Arguments = @()
  )
  $forwarded = @()
  foreach ($argument in @($Arguments)) {
    $forwarded += @($argument -split ',' | Where-Object { $_ -ne "" })
  }
  if ($forwarded.Count -gt 0) {
    Invoke-Fkctl (@("claude", $Mode) + $forwarded)
    return
  }
  $user = Resolve-LinuxUser
  $output = Get-WslOutput -AsUser $user -Command (@(Get-FkctlPath) + @("gateway", $Mode))
  if ($output) { Write-Host $output }
  $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
  if (-not $wsl) { throw "wsl.exe was not found" }
  $terminalArgs = @("-d", $Distro, "-u", $user, "--", (Get-FkctlPath), "claude", $Mode)
  [void](Start-Process -FilePath $wsl.Source -ArgumentList $terminalArgs)
  Write-Host "Opened native Claude Code with $Mode in a separate WSL terminal." -ForegroundColor Green
}

function Open-Science {
  param([ValidateSet("deepseek", "kimi", "glm", "codex")][string]$Mode)
  Assert-FkctlCapability -Capability "science-isolated-local-identity" -ActionLabel "direct Claude Science local identity"
  Assert-FkctlCapability -Capability "science-local-session-admission" -ActionLabel "Claude Science local session admission"
  $user = Resolve-LinuxUser
  $output = Get-WslOutput -AsUser $user -Command (@(Get-FkctlPath) + @("start", $Mode))
  if ($output) { Write-Host $output }
  $urls = @($output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^https?://\S+$' })
  $url = if ($urls.Count -gt 0) { $urls[-1] } else { Get-FkctlOutput -Arguments @("url") }
  Write-Host "Claude Science uses FinalKit's local-only identity in the isolated WSL profile; no Claude account is required." -ForegroundColor Green
  Write-Host "Claude Science: $url"
  if (-not $NoBrowser) { Start-Process -FilePath $url }
}

function Open-CurrentScience {
  Assert-FkctlCapability -Capability "science-isolated-local-identity" -ActionLabel "direct Claude Science local identity"
  Assert-FkctlCapability -Capability "science-local-session-admission" -ActionLabel "Claude Science local session admission"
  $user = Resolve-LinuxUser
  $output = Get-WslOutput -AsUser $user -Command (@(Get-FkctlPath) + @("restart"))
  if ($output) { Write-Host $output }
  $urls = @($output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^https?://\S+$' })
  $url = if ($urls.Count -gt 0) { $urls[-1] } else { Get-FkctlOutput -Arguments @("url") }
  Write-Host "Claude Science uses FinalKit's local-only identity in the isolated WSL profile; no Claude account is required." -ForegroundColor Green
  Write-Host "Claude Science: $url"
  if (-not $NoBrowser) { Start-Process -FilePath $url }
}

function Get-ChromePath {
  $candidates = @(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
  ) | Where-Object { $_ }
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return (Resolve-Path -LiteralPath $candidate).Path }
  }
  throw "Google Chrome was not found"
}

function Get-BrowserEndpoint { return "http://127.0.0.1:$BrowserPort" }

function Test-BrowserEndpoint {
  try {
    $response = Invoke-RestMethod -Uri "$(Get-BrowserEndpoint)/json/version" -TimeoutSec 2
    return [bool]$response.webSocketDebuggerUrl
  } catch { return $false }
}

function Start-BrowserBridge {
  param([string]$InitialUrl = "about:blank")
  if (Test-BrowserEndpoint) {
    Write-Host "Browser bridge is already reachable: $(Get-BrowserEndpoint)"
    if ($InitialUrl -ne "about:blank") {
      Start-Process -FilePath (Get-ChromePath) -ArgumentList @("--user-data-dir=$BrowserProfile", $InitialUrl) | Out-Null
    }
    return
  }
  New-Item -ItemType Directory -Path $BrowserStateRoot -Force | Out-Null
  New-Item -ItemType Directory -Path $BrowserProfile -Force | Out-Null
  $chrome = Get-ChromePath
  $arguments = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$BrowserPort",
    "--user-data-dir=$BrowserProfile",
    "--no-first-run",
    "--no-default-browser-check",
    $InitialUrl
  )
  $process = Start-Process -FilePath $chrome -ArgumentList $arguments -PassThru
  for ($i = 0; $i -lt 50; $i++) {
    if (Test-BrowserEndpoint) { break }
    Start-Sleep -Milliseconds 200
  }
  if (-not (Test-BrowserEndpoint)) { throw "Chrome remote debugging did not start on loopback" }
  $owned = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "chrome.exe" -and $_.CommandLine -like "*$BrowserProfile*" -and
    $_.CommandLine -like "*--remote-debugging-port=$BrowserPort*"
  })
  if ($owned.Count -eq 0) { throw "Chrome endpoint exists, but the isolated-profile owner could not be verified" }
  $state = [ordered]@{
    owner = $env:USERNAME
    profile = $BrowserProfile
    port = $BrowserPort
    pids = @($owned.ProcessId)
    started_at = (Get-Date).ToString("o")
  }
  $state | ConvertTo-Json | Set-Content -LiteralPath $BrowserState -Encoding UTF8
  Write-Host "BROWSER_OK isolated profile=$BrowserProfile endpoint=$(Get-BrowserEndpoint)" -ForegroundColor Green
  Write-Warning "Only sign in to sites you intentionally expose to this isolated automation profile."
}

function Open-ScienceInBrowserBridge {
  $url = Get-FkctlOutput -Arguments @("url")
  Start-BrowserBridge -InitialUrl $url
  Write-Host "Claude Science opened in the isolated automation Chrome: $url" -ForegroundColor Green
  Show-BrowserMcpInfo -ScienceReady
}

function Show-BrowserStatus {
  $reachable = Test-BrowserEndpoint
  Write-Host "Browser endpoint: $(Get-BrowserEndpoint)"
  Write-Host "Isolated profile: $BrowserProfile"
  Write-Host "Status: $(if ($reachable) {'reachable'} else {'stopped'})"
  if ($reachable -and (Test-WslDistro)) {
    & wsl.exe -d $Distro -u (Resolve-LinuxUser) -- curl -fsS --max-time 3 "$(Get-BrowserEndpoint)/json/version" | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "WSL reachability: OK" } else { Write-Warning "WSL cannot reach Windows loopback; enable WSL mirrored networking and retry." }
  }
}

function Stop-BrowserBridge {
  $owned = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "chrome.exe" -and $_.CommandLine -like "*$BrowserProfile*"
  })
  foreach ($process in $owned) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
  for ($i = 0; $i -lt 30 -and (Test-BrowserEndpoint); $i++) { Start-Sleep -Milliseconds 200 }
  if (Test-BrowserEndpoint) { throw "Browser endpoint remains active; refusing to stop an unverified owner" }
  if (Test-Path -LiteralPath $BrowserState) { Remove-Item -LiteralPath $BrowserState -Force }
  Write-Host "Browser bridge stopped; isolated profile data was preserved: $BrowserProfile"
}

function Show-BrowserMcpInfo {
  param([switch]$ScienceReady)
  $user = Resolve-LinuxUser
  $linuxHome = "/home/$user"
  $binary = "$linuxHome/.local/bin/chrome-devtools-mcp-finalkit"
  if (-not $ScienceReady) {
    Write-Host "Open current Science: .\FinalKit.ps1 -Action browser-science"
    Write-Host "Browser only:        .\FinalKit.ps1 -Action browser-start"
  }
  Write-Host "Claude Science > Customize > Connectors > Custom MCP (stdio):"
  Write-Host "  Command: $binary"
  Write-Host "  Arguments: --browser-url=$(Get-BrowserEndpoint) --slim"
  Write-Host "Optional Claude Code registration (run inside this WSL user):"
  Write-Host "  claude mcp add chrome-devtools -- $binary --browser-url=$(Get-BrowserEndpoint) --slim"
  Write-Warning "This MCP can inspect and control every tab opened in the isolated Chrome profile."
}

function Resolve-ProjectPath {
  if (-not $Project) { return (Get-Location).Path }
  if (-not (Test-Path -LiteralPath $Project -PathType Container)) { throw "Project does not exist: $Project" }
  return (Resolve-Path -LiteralPath $Project).Path
}

function Initialize-ProjectHandoff {
  $projectPath = Resolve-ProjectPath
  $targetDir = Join-Path $projectPath ".science-codex"
  $target = Join-Path $targetDir "HANDOFF.md"
  $template = Join-Path $KitRoot "project-template\HANDOFF.md"
  $reviewSkillSource = Join-Path $KitRoot "claude-science-skills\reviewing-codex-science\SKILL.md"
  $reviewSkill = Join-Path $KitRoot "claude-science-skills\reviewing-codex-science.zip"
  if (Test-Path -LiteralPath $target) {
    Write-Host "Handoff already exists; unchanged: $target"
  } else {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    Copy-Item -LiteralPath $template -Destination $target
    Write-Host "Created collaboration handoff: $target"
  }
  if ((Test-Path -LiteralPath $reviewSkillSource -PathType Leaf) -and (Test-Path -LiteralPath $reviewSkill -PathType Leaf)) {
    Write-Host "Claude Science review skill source: $reviewSkillSource"
    Write-Host "Portable skill ZIP: $reviewSkill"
    Write-Host "Publish from the user-controlled Claude Science browser with customize + host.skills; see operation.md section 9.2"
  } else {
    Write-Warning "Claude Science review skill source or package is missing: $reviewSkillSource ; $reviewSkill"
  }
}

function Invoke-WindowsReview {
  $projectPath = Resolve-ProjectPath
  $prompt = Get-Content -LiteralPath (Join-Path $KitRoot "project-template\windows-codex-review-prompt.zh-CN.md") -Raw -Encoding UTF8
  $handoff = Join-Path $projectPath ".science-codex\HANDOFF.md"
  if (Test-Path -LiteralPath $handoff) { $prompt += "`n`nProject handoff file: $handoff" }
  $codex = Get-Command codex -ErrorAction Stop
  & $codex.Source login status
  if ($LASTEXITCODE -ne 0) { throw "Windows Codex is not logged in. Run: codex login" }
  & $codex.Source exec --ignore-user-config --ephemeral --sandbox read-only --color never `
    --cd $projectPath --skip-git-repo-check $prompt
  if ($LASTEXITCODE -ne 0) { throw "Windows Codex review failed with exit code $LASTEXITCODE" }
}

function Invoke-WindowsClaudeController {
  param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("help", "menu", "init", "configure", "start", "status", "stop", "official")]
    [string]$ControllerAction,
    [ValidateSet("", "deepseek", "kimi", "glm", "codex")]
    [string]$ProfileMode = ""
  )
  $controller = Join-Path $PSScriptRoot "WindowsClaude.ps1"
  if (-not (Test-Path -LiteralPath $controller -PathType Leaf)) {
    throw "Independent Windows Claude controller is missing: $controller"
  }
  $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $controller, "-Action", $ControllerAction)
  if ($ProfileMode) { $arguments += @("-Mode", $ProfileMode) }
  if ($NoBrowser) { $arguments += "-NoLaunch" }
  if ($Force) { $arguments += "-Force" }
  if ($NoBackup) { $arguments += "-NoBackup" }
  & powershell.exe @arguments
  if ($LASTEXITCODE -ne 0) { throw "Windows Claude controller failed with exit code $LASTEXITCODE" }
}

function Get-WindowsClaudeModeArgument {
  $values = @()
  foreach ($argument in @($RemainingArgs)) {
    $values += @($argument -split ',' | Where-Object { $_ -ne "" })
  }
  if ($values.Count -ne 1 -or $values[0] -notin @("deepseek", "kimi", "glm", "codex")) {
    throw "Specify exactly one Windows Claude provider mode: deepseek|kimi|glm|codex"
  }
  return [string]$values[0]
}

function Show-Menu {
  while ($true) {
    Write-Host ""
    Write-Host "Science SwitchModel / FinalKit $PackageVersion"
    Write-Host "  Running from: $KitRoot" -ForegroundColor DarkGray
    Write-Host "  Default route: Opus=Sol max | Sonnet=Terra max | Haiku=Luna max; menu 17 persists future models" -ForegroundColor Cyan
    Write-Host "  1  Clear selected Ubuntu WSL (confirmed + backup)"
    Write-Host "  2  First install / full repair WSL + Science + Claude + Codex"
    Write-Host "  3  Configure DeepSeek"
    Write-Host "  4  Configure Kimi"
    Write-Host "  5  Configure GLM"
    Write-Host "  6  Configure ChatGPT Codex"
    Write-Host "  7  Start Science + DeepSeek   8 + Kimi   9 + GLM   10 + Codex"
    Write-Host "  11 Open current Science in automation Chrome"
    Write-Host "  12 Status   13 Doctor   14 Stop Science/gateway   15 Stop automation Chrome"
    Write-Host "  16 Update FinalKit runtime   17 Update provider models   18 Update official tools"
    Write-Host "  19 Claude Code + DeepSeek   20 + Kimi   21 + GLM   22 + Codex"
    Write-Host "  23 Independent Windows Claude provider stack (three API keys + Codex login)"
    Write-Host "  24 One-time Windows Codex login -> WSL Codex Science (optional)"
    Write-Host "  0  Exit"
    $selection = Read-Host "Select"
    try {
      switch ($selection) {
        "1" { Invoke-Clear }
        "2" { Invoke-Build }
        "3" { Invoke-Fkctl @("configure-deepseek") }
        "4" { Invoke-Fkctl @("configure-kimi") }
        "5" { Invoke-Fkctl @("configure-glm") }
        "6" {
          Assert-FkctlCapability -Capability "browser-codex-oauth" -ActionLabel "ChatGPT Codex browser login"
          Invoke-Fkctl @("configure-codex")
        }
        "7" { Open-Science deepseek }
        "8" { Open-Science kimi }
        "9" { Open-Science glm }
        "10" { Open-Science codex }
        "11" { Open-ScienceInBrowserBridge }
        "12" { Invoke-Fkctl @("status") }
        "13" { Invoke-Fkctl @("doctor") }
        "14" { Invoke-Fkctl @("stop") }
        "15" { Stop-BrowserBridge }
        "16" { Invoke-RuntimeUpdate }
        "17" { Invoke-ModelUpdateInteractive }
        "18" { Invoke-ToolsUpdate }
        "19" { Open-NativeClaude deepseek }
        "20" { Open-NativeClaude kimi }
        "21" { Open-NativeClaude glm }
        "22" { Open-NativeClaude codex }
        "23" { Invoke-WindowsClaudeController -ControllerAction menu }
        "24" { Invoke-WindowsCodexAuthMigrationToWsl }
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
Science SwitchModel / FinalKit $PackageVersion

1. Clear (optional, destructive, exact-name confirmation, backup by default):
  .\FinalKit.ps1 -Action clear
  .\FinalKit.ps1 -Action clear -AllUbuntu

2. First install or full repair of standard Ubuntu-24.04:
  .\FinalKit.ps1 -Action build
  .\FinalKit.ps1 -Action build -LinuxUser alice

Providers and start:
  .\FinalKit.ps1 -Action configure-deepseek | configure-kimi | configure-glm
  .\FinalKit.ps1 -Action configure-codex
  .\FinalKit.ps1 -Action configure-codex-device  # beta fallback when browser OAuth cannot return to WSL
  .\FinalKit.ps1 -Action migrate-windows-codex-auth-to-wsl  # optional one-time copy; WSL remains independent afterwards
  .\FinalKit.ps1 -Action test-codex-tiers        # explicit 3-request Sol/Terra/Luna account acceptance test
  .\FinalKit.ps1 -Action deepseek | kimi | glm | codex  # start Claude Science with the selected route
  .\FinalKit.ps1 -Action science                         # open the current local-only Science workbench
  .\FinalKit.ps1 -Action claude -RemainingArgs deepseek,--help  # explicit native Claude Code path

Independent updates (no WSL rebuild):
  .\FinalKit.ps1 -Action update-runtime
  .\FinalKit.ps1 -Action models
  .\FinalKit.ps1 -Action discover-models -RemainingArgs deepseek  # read-only official account catalog
  .\FinalKit.ps1 -Action update-models             # interactive preview + persistent update
  .\FinalKit.ps1 -Action update-models -RemainingArgs codex,--opus,gpt-6-sol,--reasoning-opus,max,--sonnet,gpt-6-terra,--reasoning-sonnet,max,--haiku,gpt-6-luna,--reasoning-haiku,max,--restart
  .\FinalKit.ps1 -Action update-tools              # explicit network update; asks for confirmation
  .\FinalKit.ps1 -Action update-tools -Force       # automation: same update without the second prompt

Isolated Windows browser bridge:
  .\FinalKit.ps1 -Action browser-start
  .\FinalKit.ps1 -Action browser-science
  .\FinalKit.ps1 -Action browser-mcp-info
  .\FinalKit.ps1 -Action browser-stop

Independent Windows Claude application (three API keys + Windows Codex login; never invokes WSL):
  .\FinalKit.ps1 -Action windows-claude-init
  .\FinalKit.ps1 -Action windows-claude-configure -RemainingArgs deepseek  # or kimi/glm/codex
  .\FinalKit.ps1 -Action windows-claude -RemainingArgs deepseek            # start one configured profile
  .\FinalKit.ps1 -Action windows-claude-status | windows-claude-stop
  .\FinalKit.ps1 -Action windows-claude-official                           # restore official 1P mode

Collaboration:
  .\FinalKit.ps1 -Action init-project -Project D:\path\to\project
  .\FinalKit.ps1 -Action windows-review -Project D:\path\to\project
  Publish claude-science-skills\reviewing-codex-science\SKILL.md from the Claude Science browser with customize + host.skills
  The sibling ZIP is only for Claude surfaces that expose standard custom-Skills upload

Defaults: distro=$Distro; normal per-Windows-user WSL storage; Linux user=auto from the current Windows username (or -LinuxUser)
"@
}

try {
  switch ($Action) {
    "menu" { Show-Menu }
    "clear" { Invoke-Clear }
    "build" { Invoke-Build }
    "bootstrap" { Invoke-Build }
    "update-runtime" { Invoke-RuntimeUpdate }
    "models" { Show-ModelRoutes }
    "discover-models" {
      Assert-FkctlCapability -Capability "provider-model-discovery" -ActionLabel "read-only official provider model discovery"
      $providerArguments = @()
      foreach ($argument in @($RemainingArgs)) {
        $providerArguments += @($argument -split ',' | Where-Object { $_ -ne "" })
      }
      if ($providerArguments.Count -eq 0) { throw "Specify deepseek, kimi or glm in -RemainingArgs" }
      Invoke-Fkctl (@("discover-models") + $providerArguments)
    }
    "update-models" {
      if ($RemainingArgs -and $RemainingArgs.Count -gt 0) {
        Assert-FkctlCapability -Capability "model-route-update" -ActionLabel "independent model-route updates"
        $modelArguments = @()
        foreach ($argument in $RemainingArgs) {
          # powershell.exe -File preserves comma-separated ValueFromRemainingArguments
          # as one string. Model IDs reject commas, so splitting here is
          # deterministic while ordinary space-separated callers still work.
          $modelArguments += @($argument -split ',' | Where-Object { $_ -ne "" })
        }
        Invoke-Fkctl (@("update-models") + $modelArguments)
      } else {
        Invoke-ModelUpdateInteractive
      }
    }
    "update-tools" { Invoke-ToolsUpdate }
    "configure-deepseek" { Invoke-Fkctl @("configure-deepseek") }
    "configure-kimi" { Invoke-Fkctl @("configure-kimi") }
    "configure-glm" { Invoke-Fkctl @("configure-glm") }
    "configure-codex" {
      Assert-FkctlCapability -Capability "browser-codex-oauth" -ActionLabel "ChatGPT Codex browser login"
      Invoke-Fkctl @("configure-codex")
    }
    "configure-codex-device" {
      Assert-FkctlCapability -Capability "codex-device-oauth" -ActionLabel "ChatGPT Codex device-code login"
      Invoke-Fkctl @("configure-codex-device")
    }
    "migrate-windows-codex-auth-to-wsl" { Invoke-WindowsCodexAuthMigrationToWsl }
    "login-linux-codex" { Invoke-Fkctl @("login-linux-codex") }
    "deepseek" { Open-Science deepseek }
    "kimi" { Open-Science kimi }
    "glm" { Open-Science glm }
    "codex" { Open-Science codex }
    "claude" {
      if (-not $RemainingArgs -or $RemainingArgs.Count -lt 1) { throw "claude needs provider first: deepseek|kimi|glm|codex" }
      $claudeArguments = @()
      foreach ($argument in @($RemainingArgs)) {
        $claudeArguments += @($argument -split ',' | Where-Object { $_ -ne "" })
      }
      Invoke-Fkctl (@("claude") + $claudeArguments)
    }
    "science" { Open-CurrentScience }
    "restart" {
      Invoke-Fkctl @("restart")
      $url = Get-FkctlOutput @("url")
      if (-not $NoBrowser) { Start-Process $url }
    }
    "smoke" { Invoke-Fkctl @("smoke") }
    "test-deepseek" { Invoke-Fkctl @("test", "deepseek") }
    "test-kimi" { Invoke-Fkctl @("test", "kimi") }
    "test-glm" { Invoke-Fkctl @("test", "glm") }
    "test-codex" { Invoke-Fkctl @("test", "codex") }
    "test-codex-tiers" {
      Assert-FkctlCapability -Capability "codex-tier-test" -ActionLabel "the explicit Sol/Terra/Luna acceptance test"
      Invoke-Fkctl @("test-codex-tiers")
    }
    "status" { Invoke-Fkctl @("status") }
    "doctor" { Invoke-Fkctl @("doctor"); if (Get-Command codex -ErrorAction SilentlyContinue) { codex --version; codex login status } }
    "stop" { Invoke-Fkctl @("stop") }
    "browser-start" { Start-BrowserBridge }
    "browser-science" { Open-ScienceInBrowserBridge }
    "browser-status" { Show-BrowserStatus }
    "browser-stop" { Stop-BrowserBridge }
    "browser-mcp-info" { Show-BrowserMcpInfo }
    "init-project" { Initialize-ProjectHandoff }
    "windows-review" { Invoke-WindowsReview }
    "windows-claude-menu" { Invoke-WindowsClaudeController -ControllerAction menu }
    "windows-claude-init" { Invoke-WindowsClaudeController -ControllerAction init }
    "windows-claude-configure" {
      Invoke-WindowsClaudeController -ControllerAction configure -ProfileMode (Get-WindowsClaudeModeArgument)
    }
    "windows-claude" {
      Invoke-WindowsClaudeController -ControllerAction start -ProfileMode (Get-WindowsClaudeModeArgument)
    }
    "windows-claude-status" { Invoke-WindowsClaudeController -ControllerAction status }
    "windows-claude-stop" { Invoke-WindowsClaudeController -ControllerAction stop }
    "windows-claude-official" { Invoke-WindowsClaudeController -ControllerAction official }
    "help" { Show-Help }
  }
} catch {
  [Console]::Error.WriteLine("ERROR: " + $_.Exception.Message)
  exit 1
}
