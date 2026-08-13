@echo off
setlocal enabledelayedexpansion

echo ====================================
echo    欢迎使用 SuperBizAgent 系统
echo ====================================
echo.

REM 检查 uv 是否安装，如果没有则使用 pip
echo [1/6] 检查运行环境...
where uv >nul 2>&1
if errorlevel 1 (
    echo [信息] uv 未安装，将使用传统 pip 方式
    echo [提示] 安装 uv 可加速依赖：pip install uv
    set USE_UV=0
) else (
    echo [成功] 检测到 uv，将优先使用
    set USE_UV=1
)
echo.

REM 确认 Python 版本正确
echo [2/6] 检查 Python 版本...
if exist .python-version (
    set /p PYTHON_VERSION=<.python-version
    echo [信息] 当前配置版本: !PYTHON_VERSION!

    REM 检查是否为 3.10（不兼容）
    echo !PYTHON_VERSION! | findstr /C:"3.10" >nul
    if not errorlevel 1 (
        echo [警告] Python 3.10 不兼容，自动升级到 3.13...
        echo 3.13> .python-version
        echo [成功] 已升级到 Python 3.13
    )
) else (
    echo [信息] 缺少 .python-version 文件...
    echo 3.13> .python-version
)
echo.

REM 创建或同步虚拟环境
echo [3/6] 创建/同步虚拟环境...
if exist .venv\Scripts\python.exe (
    echo [信息] 虚拟环境已存在，开始同步...

    REM 如果安装了 uv，则使用 uv sync
    if "%USE_UV%"=="1" (
        uv sync 2>nul
        if errorlevel 1 (
            echo [错误] uv sync 失败，改用 pip 安装...
            .venv\Scripts\python.exe -m pip install -e . -q
        ) else (
            echo [成功] 使用 uv 同步完成
        )
    ) else (
        echo [信息] 使用 pip 同步依赖...
        .venv\Scripts\python.exe -m pip install -e . -q
    )
) else (
    echo [信息] 创建新的虚拟环境...

    REM 如果安装了 uv，则优先使用 uv sync
    if "%USE_UV%"=="1" (
        echo [信息] 尝试使用 uv sync 创建...
        uv sync 2>nul
        if not errorlevel 1 (
            echo [成功] 使用 uv 创建完成
            goto :venv_created
        )
        echo [错误] uv sync 失败，回退到传统方式...
    )

    REM 使用传统 Python venv 创建
    echo [信息] 使用 python -m venv 创建...
    REM 优先使用 py launcher，否则使用 PATH 中有效的 python stub
    set PY_BOOT=python
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -c "import sys" >nul 2>&1
        if not errorlevel 1 set PY_BOOT=py -3
    )
    !PY_BOOT! -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        echo [提示] 请确认已安装 Python 3.11+
        pause
        exit /b 1
    )

    REM 安装依赖
    echo [信息] 安装项目依赖（可能需要几分钟）...
    .venv\Scripts\python.exe -m pip install --upgrade pip -q
    .venv\Scripts\python.exe -m pip install -e . -q
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo [成功] 虚拟环境创建完成
)

:venv_created
echo [成功] 虚拟环境就绪
echo.


REM 启动 Python 服务
set PYTHON_CMD=.venv\Scripts\python.exe

REM 启动 Docker Compose
echo [4/6] 启动 Milvus 向量数据库...
docker ps --format "{{.Names}}" | findstr "milvus-standalone" >nul 2>&1
if not errorlevel 1 (
    echo [信息] Milvus 已在运行，跳过启动
) else (
    docker compose -f vector-database.yml up -d
    if errorlevel 1 (
        echo [错误] Docker 启动失败，请确认 Docker Desktop 已启动
        pause
        exit /b 1
    )
    echo [信息] 等待 Milvus 启动（10秒）...
    timeout /t 10 /nobreak >nul
)
echo [成功] Milvus 数据库已就绪
echo.

REM 启动 CLS MCP 服务
echo [5/6] 启动 CLS MCP 服务...
start "CLS MCP Server" /min %PYTHON_CMD% mcp_servers/cls_server.py
timeout /t 2 /nobreak >nul
echo [成功] CLS MCP 服务已启动
echo.

REM 启动 Monitor MCP 服务
echo [6/6] 启动 Monitor MCP 服务...
start "Monitor MCP Server" /min %PYTHON_CMD% mcp_servers/monitor_server.py
timeout /t 2 /nobreak >nul
echo [成功] Monitor MCP 服务已启动
echo.

REM 启动 FastAPI 服务
echo [7/8] 启动 FastAPI 服务...
start "SuperBizAgent API" %PYTHON_CMD% -m uvicorn app.main:app --host 0.0.0.0 --port 9900
echo [信息] 等待服务启动（15秒）...
timeout /t 15 /nobreak >nul
echo.

REM 检查状态并上传文档
echo.
echo [信息] 检查服务状态...
curl -s http://localhost:9900/health >nul 2>&1
if errorlevel 1 (
    echo [错误] 服务可能未完全启动，请稍后手动检查
) else (
    echo [成功] FastAPI 服务已就绪
    echo.

    REM 通过 API 上传 aiops-docs 文档到知识库
    echo [8/8] 上传文档到知识库...
    for %%f in (aiops-docs\*.md) do (
        echo   上传: %%~nxf
        curl -s -X POST http://localhost:9900/api/upload -F "file=@%%f" >nul 2>&1
    )
    echo [成功] 文档上传完成
)

echo.
echo ====================================
echo    所有服务已启动完成！
echo ====================================
echo Web 界面: http://localhost:9900
echo API 文档: http://localhost:9900/docs
echo.
echo 查看日志:
echo   - FastAPI: logs\app_*.log（Loguru 日志自动滚动）
echo   - CLS MCP: type mcp_cls.log
echo   - Monitor: type mcp_monitor.log
echo 停止服务: stop-windows.bat
echo ====================================
pause
