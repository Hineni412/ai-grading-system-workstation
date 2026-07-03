# WP0.3 依赖锁定 Implementation Plan

> 所属：总体方案 Phase 0。执行方式：按 Task 顺序勾选执行。

**Goal:** 让"重新安装依赖"可复现便携运行时的实际版本：导出 constraints 锁定文件、拆分运行/构建依赖、移除无使用证据的依赖声明。

**已核验事实（2026-07-03）：**

- 便携运行时 pip 26.1.2 可用（`runtime/python/python.exe -m pip`）。
- `google-generativeai`：全仓第一方代码 **零导入**（grep 复核，排除 runtime/site-packages）→ 可从声明中删除。**不卸载**运行时中已安装的包（不动便携运行时）。
- 仍在使用、不得移除：`pypinyin`（web_app.py）、`rapidocr_onnxruntime`（scanner.py）、`streamlit-drawable-canvas`（web_app.py 三处编辑器）、其余均为核心依赖。
- `pyinstaller` 是构建期依赖，混在运行清单中 → 拆分。

## Global Constraints

- 只改声明文件（requirements*.txt、新增 constraints.txt），不安装/卸载任何包。
- requirements.txt 保持"下限声明"风格不变；版本锁定统一由 constraints.txt 承担。

## Tasks

### Task 1: 导出 constraints.txt

- [ ] Step 1: `runtime/python/python.exe -m pip freeze > constraints.txt`（UTF-8，无本地路径行；如有 `@ file://` 行需人工检查处理）。
- [ ] Step 2: 抽查关键包版本与 ARCHITECTURE.md 第 9 节记录一致：streamlit 1.58.0、openai 2.43.0、PyMuPDF 1.27.x、Pillow 12.x、pandas 3.x、opencv 4.13。
- [ ] Step 3: 文件头加注释：来源（便携运行时 pip freeze）、日期、用法 `pip install -r requirements.txt -c constraints.txt`。

### Task 2: 拆分 requirements

- [ ] Step 1: 从 `requirements.txt` 删除 `google-generativeai>=0.7.2` 与 `pyinstaller>=6.12.0`。
- [ ] Step 2: 新建 `requirements-build.txt`：内容为 `pyinstaller>=6.12.0` + 注释（仅打包/构建用）。
- [ ] Step 3: `requirements.txt` 头部注释说明：运行依赖 + constraints 用法 + 构建依赖另见 requirements-build.txt。

### Task 3: 验证与提交

- [ ] Step 1: 复核 grep：第一方代码无 `google.generativeai` / `import genai` 导入。
- [ ] Step 2: 全量 `python -m pytest -q` 通过（声明文件不影响运行时，此步为主动复核）。
- [ ] Step 3: 提交 `chore: 锁定依赖版本并拆分运行/构建清单`。

## 验收

- constraints.txt 与运行时实际版本一致；requirements.txt 无未使用/构建期依赖。

## 回退

- revert 单个提交即可；未动运行时环境。
