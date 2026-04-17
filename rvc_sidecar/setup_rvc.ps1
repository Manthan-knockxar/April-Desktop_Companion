$SidecarDir = $PSScriptRoot
$VenvDir = Join-Path $SidecarDir "venv"
$ModelsDir = Join-Path $SidecarDir "models"
$IronmouseDir = Join-Path $ModelsDir "ironmouse"

Write-Host "=== April RVC Sidecar Setup ==="

# 1. Create venv
if (!(Test-Path $VenvDir)) {
    Write-Host "Creating Python 3.10 virtual environment..."
    py -3.10 -m venv $VenvDir
} else {
    Write-Host "Virtual environment already exists."
}

# 2. Install dependencies
Write-Host "Installing dependencies..."
& "$VenvDir\Scripts\python.exe" -m pip install -r "$SidecarDir\requirements.txt"
& "$VenvDir\Scripts\python.exe" -m pip install torch==2.1.1+cu118 torchaudio==2.1.1+cu118 --index-url https://download.pytorch.org/whl/cu118

# 3. Download ironmouse model
if (!(Test-Path $IronmouseDir)) {
    Write-Host "Downloading Ironmouse Vanguard V2 model..."
    New-Item -ItemType Directory -Path $IronmouseDir -Force | Out-Null
    
    $ZipPath = Join-Path $IronmouseDir "model.zip"
    $ModelUrl = "https://huggingface.co/Tempo-Hawk/IronmouseV2/resolve/main/IronmouseV2.zip"
    
    Invoke-WebRequest -Uri $ModelUrl -OutFile $ZipPath
    
    Write-Host "Extracting model..."
    Expand-Archive -Path $ZipPath -DestinationPath $IronmouseDir -Force
    Remove-Item $ZipPath
    Write-Host "Model downloaded and extracted."
} else {
    Write-Host "Ironmouse model already exists."
}

Write-Host "=== Setup Complete! ==="
