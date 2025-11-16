# -----------------------------
# Poppler Installer for Windows (User folder, no admin needed)
# -----------------------------

# -----------------------------
# Config
# -----------------------------
$DownloadUrl = "https://github.com/oschwartz10612/poppler-windows/releases/download/v25.07.0-0/Release-25.07.0-0.zip"
$ZipName = "Release-25.07.0-0.zip"
$DownloadPath = "$env:USERPROFILE\Downloads\$ZipName"
$InstallDir = "$env:USERPROFILE\Programs\poppler"

# -----------------------------
# Download ZIP if not exists
# -----------------------------
if (-not (Test-Path $DownloadPath)) {
  Write-Host "Downloading Poppler..."
  Invoke-WebRequest -Uri $DownloadUrl -OutFile $DownloadPath
}
else {
  Write-Host "ZIP already exists at $DownloadPath, skipping download."
}

# -----------------------------
# Create install directory
# -----------------------------
if (-not (Test-Path $InstallDir)) {
  Write-Host "Creating install directory..."
  New-Item -ItemType Directory -Force -Path $InstallDir
}
else {
  Write-Host "$InstallDir already exists."
}

# -----------------------------
# Extract ZIP
# -----------------------------
Write-Host "Extracting Poppler ZIP..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($DownloadPath, $InstallDir)

# -----------------------------
# Find bin folder dynamically
# -----------------------------
$extractedDir = Join-Path $InstallDir (Get-ChildItem $InstallDir | Where-Object { $_.PSIsContainer } | Select-Object -First 1).Name
$PopplerBin = Join-Path $extractedDir "bin"

# -----------------------------
# Add Poppler bin to user PATH
# -----------------------------
Write-Host "Adding Poppler bin to user PATH..."
$oldPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($oldPath -notlike "*$PopplerBin*") {
  [Environment]::SetEnvironmentVariable("Path", "$oldPath;$PopplerBin", [EnvironmentVariableTarget]::User)
  Write-Host "User PATH updated. Restart your terminal to apply changes."
}
else {
  Write-Host "Poppler bin is already in user PATH."
}

# -----------------------------
# Verify installation
# -----------------------------
Write-Host "Verifying Poppler installation..."
Start-Process cmd -ArgumentList "/c pdftoppm -v" -NoNewWindow -Wait

Write-Host "Poppler installation completed successfully!"
