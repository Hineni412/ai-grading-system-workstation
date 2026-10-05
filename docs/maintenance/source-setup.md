# GitHub 源码环境准备

本页面向在 Windows 上开发和运行源码的人。教师使用完整便携包时，按 [README](../../README.md) 启动即可。仓库不附带 Python 运行时、本地模型、PDF 排版引擎、前端成品或业务数据。

## 准备条件

- Windows 64 位和 PowerShell 7。
- 安装完整的 Python 3.12，包含 Tcl/Tk；用 `py -3.12` 调用。现有便携构建函数需要从这个 Python 安装中复制本地文件选择窗口的组件。
- 安装符合 [前端依赖声明](../../frontend/package.json) 的 Node.js，并使用 npm 11.8.0。
- 首次准备需要网络连接，以下载 Python、Python 依赖和前端依赖。

下面的运行时步骤只用于新的源码副本。不要在已投入使用的目录中替换运行时。

## 1. 获取源码

```powershell
git clone https://github.com/Hineni412/ai-grading-system-workstation.git
cd ai-grading-system-workstation
```

## 2. 准备启动脚本使用的 Python

启动脚本寻找的是项目内的 `runtime/python/python.exe`。只安装系统 Python 还不能满足这个路径要求。

在新的源码目录执行以下命令。命令复用现有打包脚本中的运行时构建与复制函数，只准备运行时，不调用完整私有打包流程，也不复制业务数据。`PIP_CONSTRAINT` 让子进程安装依赖时使用仓库的版本约束。

```powershell
$previousConstraint = $env:PIP_CONSTRAINT
$env:PIP_CONSTRAINT = (Resolve-Path .\constraints.txt).Path
try {
@'
from pathlib import Path
import importlib.util
root = Path.cwd()
if (root / 'runtime' / 'python').exists():
    raise SystemExit('runtime/python already exists; use a new source checkout')
spec = importlib.util.spec_from_file_location('portable_package', root / 'package_v1.5.0.py')
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)
runtime = packaging.build_runtime(root, root / '.portable_runtime_cache')
packaging.copy_runtime(runtime, root)
'@ | py -3.12 -
    if ($LASTEXITCODE -ne 0) { throw 'Python runtime preparation failed.' }
} finally {
    $env:PIP_CONSTRAINT = $previousConstraint
}
```

构建失败时，源码保持原样；下载缓存和未完成的运行时可能留在 `.portable_runtime_cache/`。排查错误后可在这份新的源码副本中重试。不要把缓存、运行时或本机配置提交到 Git。

## 3. 准备前端

```powershell
npm install --global npm@11.8.0
cd frontend
npm ci
npm run build
cd ..
```

## 4. 准备按功能需要的本地资源

Python 与前端准备完成后，OCR 模型和训练卷 PDF 排版资源仍需准备。当前打包脚本的资源目录为 `runtime/models` 和 `runtime/tectonic`，细节见 [打包与更新](packaging.md)。这些二进制资源不随 GitHub 源码提供。

缺少这些资源时，不能把 Python 导入成功或测试通过当作 OCR 与 PDF 导出已验收。不要从他人的私有包复制 `user_data/` 或模型账号配置。

## 5. 启动与开发检查

```powershell
.\运行.bat
```

启动后在本机打开 `http://127.0.0.1:8035`。日常启动使用这份源码目录的 `user_data/`，首次使用时通过应用设置页建立自己的名单和模型配置。人工操作无需调用模型；真实模型请求按所选服务收费。

准备测试环境：

```powershell
& .\runtime\python\python.exe -m pip install -r requirements-test.txt -c constraints.txt
& .\runtime\python\python.exe tools\run_test_suite.py quick
& .\runtime\python\python.exe tools\check_documentation.py
```

测试使用合成数据和模型替身。完整测试、浏览器安装与并行构建规则见 [测试说明](../testing/README.md)。

## 验证范围

GitHub CI 在新的 Windows 检查目录中安装普通 Python 和前端依赖，运行后端快速测试、前端检查及模拟浏览器测试。CI 不构建完整便携运行时，不下载本地 OCR 模型或 PDF 排版资源，也不验证教师机器的完整安装流程。整套源码准备步骤在空白 Windows 机器上的人工验收仍待完成。
