# AI阅卷系统 工作机版 v1.5.0

这是私人便携开发版，包含运行所需的 Python 环境、源码和当前 `user_data` 数据。

## 启动

双击 `运行.bat`。

新电脑不需要预装 Python，也不需要重新安装 `requirements.txt`。启动脚本会直接使用：

```text
runtime\python\python.exe
```

## 数据

本包保留当前 `user_data/`，包括数据库、模板、历史考试、输出文件和 `user_data/config/api_profiles.json`。

这个包包含 API 密钥，只适合你自己使用，不要外发。

## 后续继续用 Codex 修改

源码保留在发布目录中，可以直接用 Codex 打开这个文件夹继续修改。

临时排查脚本、补丁脚本、旧测试脚本没有进入发布包；保留了少量核心回归测试，可双击 `运行核心测试.bat` 检查关键逻辑。
