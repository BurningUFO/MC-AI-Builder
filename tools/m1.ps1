[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('Build', 'Client', 'Smoke')]
    [string] $Action,
    [string] $JavaHome,
    [string] $SourceInstance = "$env:USERPROFILE\Desktop\PCL2-214-Test\.minecraft\versions\BMC4",
    [ValidateRange(0, 65535)]
    [int] $Port = 0,
    [switch] $Offline
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$cache = Join-Path $project '.cache\m1'
$python = Join-Path $project '.venv\Scripts\python.exe'
$runtime = Join-Path $env:APPDATA '.minecraft\runtime'
if (-not $JavaHome) {
    $candidates = @(
        $env:JAVA_HOME
        (Join-Path $runtime 'java-runtime-gamma-snapshot')
        (Join-Path $runtime 'java-runtime-gamma')
        (Join-Path $project 'tools\java-17')
    )
    $JavaHome = $candidates | Where-Object {
        $_ -and (Test-Path -LiteralPath (Join-Path $_ 'bin\javac.exe'))
    } | Where-Object {
        (& (Join-Path $_ 'bin\javac.exe') -version 2>&1 | Out-String) -match 'javac 17(\.|\s)'
    } | Select-Object -First 1
}
if (-not $JavaHome -or -not (Test-Path -LiteralPath (Join-Path $JavaHome 'bin\javac.exe'))) {
    throw 'Supply a Java 17 JDK directory with -JavaHome.'
}
$JavaHome = (Resolve-Path -LiteralPath $JavaHome).Path
if ((& (Join-Path $JavaHome 'bin\javac.exe') -version 2>&1 | Out-String) -notmatch 'javac 17(\.|\s)') {
    throw "The JDK at $JavaHome is not Java 17."
}
$env:JAVA_HOME = $JavaHome
$env:GRADLE_USER_HOME = Join-Path $cache 'gradle'
$env:TEMP = Join-Path $cache 'tmp'
$env:TMP = $env:TEMP
$javaUserHome = Join-Path $cache 'home'
New-Item -ItemType Directory -Force -Path $env:GRADLE_USER_HOME,$env:TEMP,$javaUserHome | Out-Null
$env:JAVA_TOOL_OPTIONS = '-Duser.home="' + $javaUserHome + '" -Djava.io.tmpdir="' + $env:TEMP + '"'

Push-Location -LiteralPath $project
try {
    if ($Action -eq 'Build') {
        $downloads = Join-Path $cache 'downloads'
        New-Item -ItemType Directory -Force -Path $downloads | Out-Null
        $players = Join-Path $downloads 'players-forge-1.20.1-3.0.0.jar'
        $digest = '2a7ccf0a3561ae8970dc654a2809f1f4c60902210126a4d962bc48513871fb5dc208348fc7fc1b4b1b11e28408a9b022dac4282a366d96f4e729215f4a4185c9'
        $modrinth = 'https://cdn.modrinth.com/data/1RSY6DQH/versions/IoNTsmcR/players-forge-1.20.1-3.0.0.jar'
        if (-not (Test-Path -LiteralPath $players) -or (Get-FileHash -LiteralPath $players -Algorithm SHA512).Hash -ne $digest) {
            if ($Offline) {
                throw "Verified Fake Players JAR required in $downloads. Run Build online once."
            }
            $staged = "$players.part"
            & curl.exe -fLsS --retry 2 --connect-timeout 15 --max-time 180 $modrinth -o $staged
            if ($LASTEXITCODE -ne 0 -or (Get-FileHash -LiteralPath $staged -Algorithm SHA512).Hash -ne $digest) {
                throw 'Fake Players download or checksum failed.'
            }
            Move-Item -LiteralPath $staged -Destination $players -Force
        }
        $flags = @('--no-daemon', '--console=plain')
        if ($Offline) { $flags += '--offline' }
        Push-Location -LiteralPath (Join-Path $project 'forge')
        try {
            & .\gradlew.bat @flags build
            if ($LASTEXITCODE -ne 0) { throw "Gradle failed with exit code $LASTEXITCODE." }
        } finally {
            Pop-Location
        }
    } else {
        if (-not (Test-Path -LiteralPath $python)) {
            throw 'The existing .venv Python is required. See docs\README.md.'
        }
        if ($Action -eq 'Client') {
            if ($Port -eq 0) { $Port = 8765 }
            $entry = 'tools.m1_client'
        } else {
            if ($Port -eq 0) { $Port = 18765 }
            $entry = 'tests.m1_smoke'
        }
        & $python -B -m $entry --java-home $JavaHome --source-instance $SourceInstance --port $Port
        if ($LASTEXITCODE -ne 0) { throw "M1 $Action exited with code $LASTEXITCODE." }
    }
} finally {
    Pop-Location
}
