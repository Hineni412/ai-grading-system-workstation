# 班主任工作台本地语音输入路线调研

> 日期：2026-08-06  
> 状态：技术路线建议，不构成实现、真实学生数据试点、模型下载或外部模型调用授权

## 结论

首版采用“教师主动录音 → 本地语音识别 → 教师检查和修改文字 → 通过现有文字入口提交给大模型”的路线。不把原始录音直接发送给通用大模型。

建议先对比两组成熟候选：

1. 默认候选：`sherpa-onnx + SenseVoiceSmall INT8`。它支持 Windows、本地离线识别、普通话/粤语/英语等语言、麦克风输入、VAD（自动切掉静音）和 ITN（数字、标点等文字规范化）；官方 INT8 模型约 228 MB，适合工作机内嵌。
2. 准确率对照：`FunASR Paraformer-large + FSMN-VAD + CT-Punc`。官方把它定位为中文生产识别链，支持流式/离线识别、热词、时间戳，并可组合静音检测和标点恢复；模型及运行依赖更重，但值得用同一批普通话、深圳口音、姓名和班务词汇录音对测。
3. 多语言备用：`faster-whisper`。它支持 CPU INT8、NVIDIA GPU、VAD 和 Windows 生态，通用多语言能力成熟，但不作为本项目中文短口述的默认首选。

不建议把浏览器 Web Speech API 当作生产离线引擎：部分浏览器默认使用在线识别服务；强制本机处理的 `processLocally` 能力仍属实验性且兼容性有限。

## 两条路线比较

| 维度 | 原始录音直接交给大模型 | 本地转文字后交给大模型 |
| --- | --- | --- |
| 学生隐私 | 录音及声音特征离开工作机，风险面更大 | 原始录音留在本机，只提交教师确认后的最小文本 |
| 模型兼容 | 绑定少数支持音频的模型 | 可继续使用任何文本模型和现有 AI Task 流程 |
| 可核对性 | 模型可能把“听错”和“理解错”混在一次结果里 | 教师先看到并修正转写，再让模型整理 |
| 成本与故障 | 依赖上传、网络、音频计费和供应商文件处理 | 本地有一次计算成本，后续仍是普通文本调用 |
| 声调/情绪 | 能保留语气信息，但班主任系统不应据此诊断或定性学生 | 主动舍弃容易误导的语气推断，更贴合事实记录 |
| 供应商切换 | 更困难 | 较容易 |

即使支持原生音频的模型可以直接理解录音，专用语音转文字仍是独立、成熟的任务。Google 的官方音频文档也把实时/专用转写指向 Speech-to-Text 服务；OpenAI 的实时音频文档说明输入转写是由独立 ASR 模型异步完成，不能把转写文本视为模型“精确听到”的全部内容。因此，“音频大模型”不等于可以省略校对环节。

## 建议的产品流程

```text
单击麦克风
→ 明确显示录音状态与时长
→ 单击停止（或短暂停顿后停止）
→ 本机临时音频交给离线识别引擎
→ 转写文字回填原输入框
→ 教师修改、确认
→ 走现有文字提交/AI Task
→ 成功、取消或失败后删除临时音频，并如实显示清理结果
```

首版不做常开麦克风、不做自动发送、不做声纹、不做情绪判断、不保存原始录音，也不在离线失败时静默切到云端识别。

## 验证方法

不能只引用公开榜单决定最终模型。应在不含真实学生资料的合成录音上先做 A/B 测试，覆盖：

- 普通话、深圳常见口音和少量粤语；
- 学生代号、日期、数字、班级、科目、校内简称；
- 安静办公室、风扇声、远距离和正常语速；
- 10—60 秒短口述，以及停顿、改口和自我纠正。

至少同时记录：关键姓名/代号、日期和数字是否正确，整句错字率，停止录音到出现文字的等待时间，内存占用，断网可用性，以及异常退出后临时音频是否按约定清理。真实教师语音、真实姓名或真实学生事件进入试点前，仍需另行授权。

## 一手来源

- [sherpa-onnx 官方仓库：离线能力、Windows 与多语言 API](https://github.com/k2-fsa/sherpa-onnx)
- [sherpa-onnx 官方 SenseVoice 模型文档：INT8 体积、语言、ITN、麦克风与 VAD](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html)
- [SenseVoice 官方仓库：发布模型能力与基准说明](https://github.com/QwenAudio/SenseVoice)
- [FunASR 官方仓库：Paraformer、VAD、标点、流式与热词用法](https://github.com/modelscope/FunASR)
- [ModelScope 官方 Paraformer 模型卡：生产部署与完整中文转写链](https://modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-torchscript)
- [faster-whisper 官方仓库：CPU/GPU、VAD 与 Windows 生态](https://github.com/SYSTRAN/faster-whisper)
- [MDN Web Speech API：默认在线识别与实验性本机识别](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API)
- [Google Gemini 官方音频理解文档](https://ai.google.dev/gemini-api/docs/audio)
- [OpenAI Realtime API 官方输入音频转写说明](https://platform.openai.com/docs/api-reference/realtime-server-events/conversation/item/input_audio_transcription/completed)

