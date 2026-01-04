# AKG CLI

基于 `prompt_toolkit` + `rich` 的 DeepSeek 聊天 CLI 工具。

## 功能特性

- 🎨 终端友好的彩色输出（Rich 渲染）
- 💬 与 DeepSeek 对话
- 📝 多行输入（Enter 发送，Ctrl+J 换行，最多 10 行）
- 🔄 命令历史记录
- ⚡ 命令自动补全
- 🎯 支持模型切换
- 📊 对话历史统计
- 🧩 可折叠面板（F2 切换，默认显示实时时间）

## 安装

```bash
pip install -e .
```

或手动安装依赖：

```bash
pip install prompt_toolkit rich httpx
```

## 配置

设置 DeepSeek API Key 环境变量：

```bash
export DEEPSEEK_API_KEY="your-api-key-here"
```

也可以在项目根目录放置 `.env`：

```
DEEPSEEK_API_KEY=your-api-key-here
```

## 使用方法

运行程序：

```bash
python akg_cli.py
```

或使用安装后的命令：

```bash
akg-cli
```

### 快捷键

- `Enter`：发送
- `Ctrl+J`：换行（最多 10 行）
- `Ctrl+C`：中断当前请求
- `F2`：切换面板显示/隐藏

### 命令

- `/help` - 显示帮助信息
- `/exit` 或 `/quit` - 退出程序
- `/clear` - 清空当前对话历史
- `/model` - 切换模型（deepseek-chat / deepseek-coder）
- `/history` - 显示对话历史统计

## 当前已知问题

1. 面板会进入消息区域（因为当前输入区域非全屏渲染，面板作为 prompt 输出会写入终端滚动区）。
2. 用户输入会打印两遍，其中有一条是 prompt 输出（无法完全控制）。
3. 尚未接入流式输出（当前为一次性返回）。

## 技术栈

- [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit)
- [rich](https://github.com/Textualize/rich)
- [httpx](https://github.com/encode/httpx)

## 许可证

MIT
