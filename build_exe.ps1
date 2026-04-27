$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name ZMDStoreDesktop `
  --contents-directory . `
  --distpath dist_release `
  --workpath build_release `
  --specpath . `

  --hidden-import PySide6.QtCore `
  --hidden-import PySide6.QtGui `
  --hidden-import PySide6.QtWidgets `

  --hidden-import cv2 `
  --hidden-import numpy `
  --hidden-import auto_click `


  --exclude-module PySide6.QtWebEngine `
  --exclude-module PySide6.QtQml `
  --exclude-module PySide6.QtNetwork `
  --exclude-module PySide6.QtMultimedia `
  --exclude-module PySide6.QtSql `
  --exclude-module PySide6.QtTest `
  --exclude-module PySide6.QtCharts `
  --exclude-module matplotlib `
  --exclude-module torch `
  --exclude-module scipy `
  --exclude-module pandas `

  --add-data "item;item" `
  --add-data "region;region" `
  --add-data "public;public" `

  ui_main.py