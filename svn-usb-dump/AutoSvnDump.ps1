<# 
AutoSvnDump.ps1
----------------
Dump (and optionally hotcopy) all SVN repositories to a plugged-in USB drive on Windows.

USAGE (manual):
  PowerShell (Run as Administrator):
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
    .\AutoSvnDump.ps1 -ReposRoot "D:\svn_repos" -UsbLabel "SVN_BACKUP"

USAGE (with Scheduled Task on USB insert):
  Create the scheduled task using the provided XML, or follow the README.
  The task will run this script and it will scan all currently attached removable drives.

NOTES:
- "svnadmin.exe" must be installed (VisualSVN Server or CollabNet/Subversion).
- Script supports incremental dumps per-repo using a state file on the USB.
- Safer backups use "svnadmin hotcopy" to a temp location, then dump from the hotcopy.
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)]
  [string]$ReposRoot,                    # Root folder that contains all SVN repositories (each repo is a folder with "format" + "db")
  [string]$UsbLabel = "",                # Optional: Only backup when a removable drive with this VolumeLabel is present
  [int]$UsbMinFreeGB = 2,                # Require at least this many GB of free space on the USB before dumping
  [string]$DumpSubdir = "SVN_DUMPS",     # Where on the USB to store dumps/logs/state
  [string]$SvnAdminPath = "",            # If empty, we'll try PATH then common install locations
  [switch]$NoHotcopy,                    # If set, we will dump directly from the live repo (generally OK, but hotcopy is safer)
  [switch]$Full,                         # Force full dump (ignore state)
  [switch]$ZipDumps,                     # Compress .svndump files into .zip
  [switch]$Verify                         # Run "svnadmin verify" on the hotcopy before dumping
)

# --- Utilities ---
function Write-Log {
  param([string]$Message, [string]$Level = "INFO")
  $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  $line = "[$stamp][$Level] $Message"
  Write-Host $line
  if ($Global:LogFile) { Add-Content -Path $Global:LogFile -Value $line -Encoding UTF8 }
}

function Find-SvnAdmin {
  param([string]$Hint)
  if ($Hint -and (Test-Path $Hint)) { return (Resolve-Path $Hint).Path }

  # Try PATH
  $exe = "svnadmin.exe"
  $found = (Get-Command $exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
  if ($found) { return $found }

  # Common locations
  $candidates = @(
    "C:\Program Files\Subversion\bin\svnadmin.exe",
    "C:\Program Files (x86)\Subversion\bin\svnadmin.exe",
    "C:\Program Files\VisualSVN Server\bin\svnadmin.exe"
  )
  foreach ($c in $candidates) {
    if (Test-Path $c) { return $c }
  }
  throw "svnadmin.exe not found. Install Subversion (or VisualSVN Server) or specify -SvnAdminPath."
}

function Get-RemovableVolumes {
  # Return objects with DriveLetter, Label, FreeSpaceGB, Root
  Get-Volume -ErrorAction SilentlyContinue | Where-Object {
    $_.DriveType -eq 'Removable' -and $_.DriveLetter
  } | ForEach-Object {
    [PSCustomObject]@{
      DriveLetter = $_.DriveLetter
      Label       = $_.FileSystemLabel
      FreeSpaceGB = [math]::Round($_.SizeRemaining / 1GB, 2)
      Root        = ($_.DriveLetter + ":\")
    }
  }
}

function Detect-Repos {
  param([string]$Root)
  if (!(Test-Path $Root)) { throw "Repos root '$Root' does not exist." }
  Get-ChildItem -LiteralPath $Root -Directory -ErrorAction Stop | Where-Object {
    Test-Path (Join-Path $_.FullName "format") -and Test-Path (Join-Path $_.FullName "db")
  }
}

function Get-RepoHead {
  param([string]$SvnAdmin, [string]$RepoPath)
  $p = Start-Process -FilePath $SvnAdmin -ArgumentList @("youngest", "`"$RepoPath`"") -NoNewWindow -PassThru -RedirectStandardOutput temp_youngest.txt
  $p.WaitForExit()
  $head = Get-Content temp_youngest.txt | Select-Object -First 1
  Remove-Item temp_youngest.txt -Force -ErrorAction SilentlyContinue
  if (-not ($head -as [int])) { throw "Cannot read youngest revision for $RepoPath" }
  return [int]$head
}

function Do-Hotcopy {
  param([string]$SvnAdmin, [string]$RepoPath, [string]$HotcopyPath)
  if (Test-Path $HotcopyPath) { Remove-Item $HotcopyPath -Recurse -Force -ErrorAction SilentlyContinue }
  $args = @("hotcopy", "`"$RepoPath`"", "`"$HotcopyPath`"", "--clean-logs")
  $p = Start-Process -FilePath $SvnAdmin -ArgumentList $args -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) { throw "svnadmin hotcopy failed for $RepoPath" }
}

function Do-Verify {
  param([string]$SvnAdmin, [string]$RepoPath)
  $p = Start-Process -FilePath $SvnAdmin -ArgumentList @("verify", "`"$RepoPath`"") -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) { throw "svnadmin verify failed for $RepoPath" }
}

function Dump-Repo {
  param(
    [string]$SvnAdmin, [string]$SourceRepo, [string]$OutFile, 
    [int]$StartRev, [int]$EndRev, [switch]$Incremental
  )
  $range = "$StartRev:$EndRev"
  $args = @("dump", "`"$SourceRepo`"", "-r", $range)
  if ($Incremental) { $args += "--incremental" }
  Write-Log "Dumping range $range from $SourceRepo to $OutFile"
  $p = Start-Process -FilePath $SvnAdmin -ArgumentList $args -NoNewWindow -PassThru -RedirectStandardOutput $OutFile
  $p.WaitForExit()
  if ($p.ExitCode -ne 0) { throw "svnadmin dump failed for $SourceRepo r$range" }
}

# --- Main ---
try {
  $Global:LogDir = Join-Path $env:ProgramData "SvnUsbDump\logs"
  New-Item -ItemType Directory -Force -Path $Global:LogDir | Out-Null
  $Global:LogFile = Join-Path $Global:LogDir ("dump-" + (Get-Date -Format "yyyyMMdd") + ".log")

  Write-Log "Starting AutoSvnDump.ps1"

  $svnadmin = Find-SvnAdmin -Hint $SvnAdminPath
  Write-Log "Using svnadmin at: $svnadmin"

  $removable = Get-RemovableVolumes
  if (-not $removable) {
    Write-Log "No removable volumes present. Exiting."
    exit 0
  }

  if ($UsbLabel) {
    $removable = $removable | Where-Object { $_.Label -eq $UsbLabel }
    if (-not $removable) {
      Write-Log "No removable volume with label '$UsbLabel' found. Exiting."
      exit 0
    }
  }

  foreach ($usb in $removable) {
    if ($usb.FreeSpaceGB -lt $UsbMinFreeGB) {
      Write-Log "Skipping $($usb.DriveLetter): Insufficient free space ($($usb.FreeSpaceGB)GB < $UsbMinFreeGB GB)"
      continue
    }

    $usbRoot = $usb.Root
    $dumpRoot = Join-Path $usbRoot $DumpSubdir
    $repoDumpsDir = Join-Path $dumpRoot "repos"
    $stateFile = Join-Path $dumpRoot "backup_state.json"
    $usbLogDir = Join-Path $dumpRoot "logs"
    foreach ($p in @($dumpRoot, $repoDumpsDir, $usbLogDir)) { New-Item -ItemType Directory -Force -Path $p | Out-Null }

    Write-Log "Target USB: $($usb.DriveLetter): Label='$($usb.Label)' Free=$($usb.FreeSpaceGB)GB"
    Write-Log "Dump output: $repoDumpsDir"
    $state = @{}
    if (Test-Path $stateFile) {
      try { $state = Get-Content $stateFile -Raw | ConvertFrom-Json } catch { $state = @{} }
    }

    $repos = Detect-Repos -Root $ReposRoot
    if (-not $repos) {
      Write-Log "No SVN repositories detected in $ReposRoot" "WARN"
      continue
    }

    foreach ($repo in $repos) {
      $name = $repo.Name
      $repoPath = $repo.FullName
      Write-Log "Processing repo: $name"

      $head = Get-RepoHead -SvnAdmin $svnadmin -RepoPath $repoPath

      $lastDumped = if ($Full) {  -1 } else { ($state.$name) }
      if (-not ($lastDumped -is [int])) { $lastDumped = -1 }  # no prior dump

      $start = if ($lastDumped -lt 0) { 0 } else { [int]($lastDumped + 1) }
      if ($start -gt $head) {
        Write-Log "Nothing new to dump for $name (last=$lastDumped, head=$head)"
        continue
      }

      $dateTag = (Get-Date -Format "yyyyMMdd_HHmmss")
      $baseOut = Join-Path $repoDumpsDir ("{0}_r{1}-{2}_{3}.svndump" -f $name, $start, $head, $dateTag)

      $source = $repoPath
      $tempHotcopy = Join-Path $env:TEMP ("svn_hotcopy_{0}_{1}" -f $name, $dateTag)

      if (-not $NoHotcopy) {
        Write-Log "Creating hotcopy to $tempHotcopy"
        Do-Hotcopy -SvnAdmin $svnadmin -RepoPath $repoPath -HotcopyPath $tempHotcopy
        if ($Verify) {
          Write-Log "Verifying hotcopy..."
          Do-Verify -SvnAdmin $svnadmin -RepoPath $tempHotcopy
        }
        $source = $tempHotcopy
      }

      try {
        $isIncremental = ($start -gt 0)
        Dump-Repo -SvnAdmin $svnadmin -SourceRepo $source -OutFile $baseOut -StartRev $start -EndRev $head -Incremental:$isIncremental

        if ($ZipDumps) {
          $zipPath = [System.IO.Path]::ChangeExtension($baseOut, ".zip")
          Write-Log "Compressing dump to $zipPath"
          Compress-Archive -Path $baseOut -DestinationPath $zipPath -Force
          Remove-Item $baseOut -Force -ErrorAction SilentlyContinue
          $finalOut = $zipPath
        } else {
          $finalOut = $baseOut
        }

        # Update state
        $state.$name = $head
        ($state | ConvertTo-Json -Depth 5) | Out-File -FilePath $stateFile -Encoding UTF8

        Write-Log "SUCCESS: $name -> $finalOut"
      }
      catch {
        Write-Log "ERROR dumping $name: $($_.Exception.Message)" "ERROR"
      }
      finally {
        if (-not $NoHotcopy -and (Test-Path $tempHotcopy)) {
          Remove-Item $tempHotcopy -Recurse -Force -ErrorAction SilentlyContinue
        }
      }
    }

    # Copy host log into USB log dir for convenience
    try {
      Copy-Item -LiteralPath $Global:LogFile -Destination (Join-Path $usbLogDir (Split-Path $Global:LogFile -Leaf)) -Force
    } catch {}

    Write-Log "Completed backup run for USB $($usb.DriveLetter):\."
  }

  Write-Log "All done."
  exit 0
}
catch {
  Write-Log "FATAL: $($_.Exception.Message)" "ERROR"
  exit 1
}
