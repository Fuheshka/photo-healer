<#
.SYNOPSIS
    Photo Healer — Triage PowerShell Script v1.0
    Classifies image files as TRIM-erased zeros or files with live data.
    No Python required. Streams files in 64 KB chunks.

.DESCRIPTION
    Scans a directory tree for image files and classifies each as:
      TRIM_ZERO        — 100% zero bytes (TRIM-erased, unrecoverable)
      HEALED_CANDIDATE — leading zeros + live data (repairable)
      VALID            — correct magic bytes (intact JPEG/PNG/etc.)
      OTHER            — unknown format

.PARAMETER Root
    Root directory to scan (required).

.PARAMETER Quarantine
    If specified, TRIM_ZERO files are moved here (folder structure preserved).

.PARAMETER Report
    Path for the JSON report. Default: triage_report.json in current folder.

.PARAMETER DryRun
    Show what would be done without actually moving files.

.EXAMPLE
    .\triage.ps1 -Root "E:\Photos" -Quarantine "E:\TRIM_Quarantine"
    .\triage.ps1 -Root "E:\Photos" -DryRun
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Root,

    [string]$Quarantine,

    [string]$Report = "triage_report.json",

    [switch]$DryRun,

    [string[]]$Ext = @(".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Constants ──────────────────────────────────────────────────────────────────

$CHUNK_SIZE = 65536   # 64 KB

$MAGIC = @{
    ".jpg"  = [byte[]]@(0xFF, 0xD8, 0xFF)
    ".jpeg" = [byte[]]@(0xFF, 0xD8, 0xFF)
    ".png"  = [byte[]]@(0x89, 0x50, 0x4E, 0x47)
    ".gif"  = [byte[]]@(0x47, 0x49, 0x46, 0x38)
    ".bmp"  = [byte[]]@(0x42, 0x4D)
    ".tif"  = [byte[]]@(0x49, 0x49, 0x2A, 0x00)
    ".tiff" = [byte[]]@(0x4D, 0x4D, 0x00, 0x2A)
    ".webp" = [byte[]]@(0x52, 0x49, 0x46, 0x46)
}

$ExtSet = [System.Collections.Generic.HashSet[string]]::new(
    [string[]]($Ext | ForEach-Object { $_.ToLower() }),
    [System.StringComparer]::OrdinalIgnoreCase
)

# ── Classifier ─────────────────────────────────────────────────────────────────

function Invoke-ClassifyFile {
    param([System.IO.FileInfo]$File)

    $result = [PSCustomObject]@{
        path          = $File.FullName
        size          = $File.Length
        status        = "error"
        first_nonzero = -1
        note          = ""
    }

    if ($File.Length -eq 0) {
        $result.status = "empty"
        $result.note   = "zero-length file"
        return $result
    }

    $ext = $File.Extension.ToLower()

    try {
        $stream = [System.IO.File]::OpenRead($File.FullName)
        $buffer = New-Object byte[] $CHUNK_SIZE
        $offset = 0
        $firstNonZero = -1
        $found = $false

        while (-not $found) {
            $read = $stream.Read($buffer, 0, $CHUNK_SIZE)
            if ($read -eq 0) { break }

            for ($i = 0; $i -lt $read; $i++) {
                if ($buffer[$i] -ne 0) {
                    $firstNonZero = $offset + $i
                    $found = $true
                    break
                }
            }
            $offset += $read
        }
        $stream.Dispose()
    }
    catch {
        $result.note = $_.Exception.Message
        return $result
    }

    $result.first_nonzero = $firstNonZero

    if ($firstNonZero -eq -1) {
        $result.status = "trim_zero"
        $result.note   = "100% zeros — TRIM erased ($([Math]::Round($File.Length / 1MB, 2)) MB)"
        return $result
    }

    if ($firstNonZero -eq 0) {
        # Check magic bytes
        $expectedMagic = $MAGIC[$ext]
        if ($expectedMagic) {
            $fs = [System.IO.File]::OpenRead($File.FullName)
            $head = New-Object byte[] $expectedMagic.Length
            $null = $fs.Read($head, 0, $expectedMagic.Length)
            $fs.Dispose()

            $match = $true
            for ($i = 0; $i -lt $expectedMagic.Length; $i++) {
                if ($head[$i] -ne $expectedMagic[$i]) { $match = $false; break }
            }

            if ($match) {
                $result.status = "valid"
                $result.note   = "intact"
            } else {
                $result.status = "other"
                $hexHead = ($head | ForEach-Object { '{0:X2}' -f $_ }) -join ' '
                $result.note   = "unexpected magic: $hexHead"
            }
        } else {
            $result.status = "other"
            $result.note   = "no magic table for $ext"
        }
        return $result
    }

    # first_nonzero > 0 → leading zeros + live data
    $sectors = [Math]::Floor($firstNonZero / 512)
    $result.status        = "healed_candidate"
    $result.first_nonzero = $firstNonZero
    $result.note          = "header zeroed ($firstNonZero bytes = $sectors sectors), live data at $firstNonZero"
    return $result
}

# ── Scanner ────────────────────────────────────────────────────────────────────

function Invoke-Scan {
    param([string]$RootDir, [System.Collections.Generic.HashSet[string]]$Extensions)

    $results = [System.Collections.Generic.List[PSCustomObject]]::new()
    $total = 0

    Get-ChildItem -LiteralPath $RootDir -Recurse -File | ForEach-Object {
        if (-not $Extensions.Contains($_.Extension.ToLower())) { return }

        $total++
        $r = Invoke-ClassifyFile -File $_
        $results.Add($r)

        if ($total % 200 -eq 0) {
            $c = $results | Group-Object status | ForEach-Object { @{$_.Name = $_.Count} }
            $v = ($results | Where-Object status -eq "valid").Count
            $t = ($results | Where-Object status -eq "trim_zero").Count
            $h = ($results | Where-Object status -eq "healed_candidate").Count
            $o = ($results | Where-Object status -eq "other").Count
            Write-Host ("  [{0,5}]  valid={1,4}  trim={2,4}  candidate={3,3}  other={4,3}" -f $total, $v, $t, $h, $o)
        }
    }

    return $results
}

# ── Summary ────────────────────────────────────────────────────────────────────

function Write-Summary {
    param([System.Collections.Generic.List[PSCustomObject]]$Results)

    $statuses = @("valid", "healed_candidate", "trim_zero", "other", "empty", "error")
    $icons    = @{
        "valid"            = "[OK]  "
        "trim_zero"        = "[TRIM]"
        "healed_candidate" = "[HEAL]"
        "other"            = "[???] "
        "empty"            = "[NULL]"
        "error"            = "[ERR] "
    }

    $line = "=" * 62
    Write-Host "`n$line"
    Write-Host "  PHOTO HEALER — TRIAGE REPORT"
    Write-Host $line

    foreach ($st in $statuses) {
        $group = $Results | Where-Object { $_.status -eq $st }
        $n = @($group).Count
        if ($n -eq 0) { continue }
        $mb = [Math]::Round(($group | Measure-Object size -Sum).Sum / 1MB, 1)
        $icon = $icons[$st]
        Write-Host ("  {0}  {1,-22} {2,5} files   {3,9:N1} MB" -f $icon, $st, $n, $mb)
    }

    Write-Host $line
    $totalN  = $Results.Count
    $totalMB = [Math]::Round(($Results | Measure-Object size -Sum).Sum / 1MB, 1)
    Write-Host ("  TOTAL                          {0,5} files   {1,9:N1} MB" -f $totalN, $totalMB)
    Write-Host $line
}

# ── Quarantine ─────────────────────────────────────────────────────────────────

function Move-ToQuarantine {
    param(
        [System.Collections.Generic.List[PSCustomObject]]$Results,
        [string]$Dest,
        [string]$RootPath,
        [bool]$Dry
    )

    if (-not $Dry) {
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    }

    $moved = 0
    $freed = 0.0

    foreach ($r in ($Results | Where-Object { $_.status -eq "trim_zero" })) {
        $src = $r.path
        if (-not (Test-Path -LiteralPath $src)) { continue }

        # Relative path
        $rel = $src.Substring($RootPath.TrimEnd('\').Length).TrimStart('\')
        $dst = Join-Path $Dest $rel

        if ($Dry) {
            Write-Host "  [DRY] $(Split-Path $src -Leaf) -> $rel"
        } else {
            $dstDir = Split-Path $dst -Parent
            if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
            try {
                Move-Item -LiteralPath $src -Destination $dst -Force
                $moved++
                $freed += $r.size
            } catch {
                Write-Warning "  [ERR] $(Split-Path $src -Leaf): $_"
            }
        }
    }

    return @{ Moved = $moved; FreedMB = [Math]::Round($freed / 1MB, 1) }
}

# ── Main ───────────────────────────────────────────────────────────────────────

if (-not (Test-Path -LiteralPath $Root)) {
    Write-Error "Directory not found: $Root"
    exit 1
}

$RootResolved = (Resolve-Path $Root).Path

Write-Host "[SCAN] $RootResolved"
Write-Host "       extensions : $($Ext -join ', ')"
Write-Host "       chunk size : $([int]($CHUNK_SIZE/1KB)) KB`n"

$results = Invoke-Scan -RootDir $RootResolved -Extensions $ExtSet
Write-Summary -Results $results

# JSON report
$reportPath = $Report
$jsonData = $results | ForEach-Object {
    [ordered]@{
        path          = $_.path
        size          = $_.size
        status        = $_.status
        first_nonzero = $_.first_nonzero
        note          = $_.note
    }
}
$jsonData | ConvertTo-Json -Depth 3 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host "`n[RPT] Report saved: $reportPath"

# Heal candidates shortlist
$candidates = $results | Where-Object { $_.status -eq "healed_candidate" }
if (@($candidates).Count -gt 0) {
    $candPath = Join-Path (Split-Path $reportPath -Parent) "heal_candidates.json"
    $candidates | ForEach-Object {
        [ordered]@{ path = $_.path; size = $_.size; status = $_.status
                    first_nonzero = $_.first_nonzero; note = $_.note }
    } | ConvertTo-Json -Depth 3 | Set-Content -Path $candPath -Encoding UTF8
    Write-Host "[RPT] Heal candidates ($(@($candidates).Count)): $candPath"
}

# Quarantine
if ($Quarantine) {
    $mode = if ($DryRun) { "DRY-RUN" } else { "MOVING" }
    Write-Host "`n[Q] $mode trim_zero -> $Quarantine"
    $qResult = Move-ToQuarantine -Results $results -Dest $Quarantine -RootPath $RootResolved -Dry $DryRun.IsPresent
    if (-not $DryRun) {
        Write-Host "[Q] Moved: $($qResult.Moved) files  ($($qResult.FreedMB) MB freed)"
    }
}
