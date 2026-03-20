$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

Write-Host "========================================"
Write-Host "聚水潭停产下架商品编码批量修改"
Write-Host "========================================"
Write-Host ""

if (-not (Test-Path ".env")) {
    Write-Host "未找到 .env 文件。"
    Write-Host "请先参考 .env.example 创建并填写 .env。"
    Write-Host ""
    Read-Host "按回车退出"
    exit 1
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "未找到 npm。"
    Write-Host "请先安装 Node.js，并确保 npm 可用。"
    Write-Host ""
    Read-Host "按回车退出"
    exit 1
}

if (-not (Test-Path "node_modules")) {
    Write-Host "未找到 node_modules，开始安装依赖..."
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "依赖安装失败。"
        Write-Host ""
        Read-Host "按回车退出"
        exit $LASTEXITCODE
    }
}

Write-Host "开始执行..."
Write-Host "结果会输出到 results 目录。"
Write-Host ""

npm run start
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "执行完成。"
} else {
    Write-Host "执行失败，错误码: $exitCode"
    Write-Host "请查看 results 和 artifacts 目录。"
}
Write-Host ""
Read-Host "按回车关闭窗口"
exit $exitCode
