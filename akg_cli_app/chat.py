import asyncio
import os
import sys
import shutil
from typing import List, Dict, Optional

import httpx
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML, FormattedText
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.application import run_in_terminal
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.text import Text

from .api import DeepSeekAPI
from .completer import CommandCompleter
from .panels import PanelProvider, TimePanel


class AKGChat:
    """主聊天类"""

    def __init__(self):
        self.console = Console(record=True, force_terminal=True, color_system="truecolor")
        self.api = None
        self.messages: List[Dict[str, str]] = []
        self.model = "deepseek-chat"
        self.history_file = os.path.expanduser("~/.akg_cli_history")
        self.busy = False
        self.current_request_id = 0
        self.exit_requested = False
        self.panel_visible = True
        self.panel_provider: PanelProvider = TimePanel()

        self._load_env_file()

        # 初始化 API
        try:
            self.api = DeepSeekAPI()
        except ValueError as e:
            self.console.print(f"[red]错误: {e}[/red]")
            sys.exit(1)

        # 配置键绑定：Enter 提交，Ctrl+J 换行
        self.kb = KeyBindings()

        @self.kb.add("c-j")
        def _(event):
            """Ctrl+J 换行"""
            line_count = event.current_buffer.text.count("\n") + 1
            if line_count >= 10:
                return
            event.current_buffer.insert_text("\n")

        @self.kb.add(Keys.Enter, eager=True)
        def _(event):
            """Enter 键提交"""
            if not event.current_buffer.text.strip():
                return
            event.current_buffer.validate_and_handle()

        @self.kb.add("c-c")
        def _(event):
            """Ctrl+C 中断当前请求"""
            if self.busy:
                self.current_request_id += 1
                self.busy = False
                self.emit("已中断当前请求。")
            else:
                self.exit_requested = True
                event.app.exit(result="")

        @self.kb.add("f2")
        def _(event):
            """F2 切换面板显示"""
            self.panel_visible = not self.panel_visible
            event.app.invalidate()

        # 初始化提示会话
        self.input_style = Style.from_dict({
            "default": "fg:#f0f0f0 bg:#303a46",
            "input": "fg:#f0f0f0 bg:#303a46",
            "prompt": "bold fg:#f0f0f0 bg:#303a46",
            "promptcontinuation": "fg:#f0f0f0 bg:#303a46",
            "placeholder": "fg:#b0b0b0 bg:#303a46",
            "panel": "fg:#e6e6e6 bg:#303a46",
        })
        self.session = PromptSession(
            history=FileHistory(self.history_file),
            auto_suggest=AutoSuggestFromHistory(),
            completer=CommandCompleter(),
            style=self.input_style,
            key_bindings=self.kb,
            multiline=True,
        )

    def _load_env_file(self) -> None:
        if os.getenv("DEEPSEEK_API_KEY"):
            return
        env_path = os.path.join(os.getcwd(), ".env")
        if not os.path.exists(env_path):
            return
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):].strip()
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if key != "DEEPSEEK_API_KEY":
                        continue
                    value = value.strip().strip('"').strip("'")
                    if value:
                        os.environ["DEEPSEEK_API_KEY"] = value
                    return
        except Exception:
            return

    def welcome_text(self) -> str:
        return (
            "AKG CLI - DeepSeek 聊天工具\n"
            "输入 /help 查看帮助，输入 /exit 退出\n"
        )

    def help_text(self) -> str:
        return (
            "可用命令:\n"
            "  /help      - 显示此帮助信息\n"
            "  /exit      - 退出程序\n"
            "  /quit      - 退出程序（同 /exit）\n"
            "  /clear     - 清空当前对话历史\n"
            "  /model     - 切换模型（deepseek-chat / deepseek-coder）\n"
            "  /history   - 显示对话历史统计\n"
            "\n"
            "使用说明:\n"
            "  直接输入消息即可与 DeepSeek 对话\n"
            "  支持多行输入：\n"
            "    • Enter 键提交消息\n"
            "    • Ctrl+J 换行\n"
            "  使用 Ctrl+C 中断当前请求\n"
        )

    def handle_command_ui(self, user_input: str) -> bool:
        cmd = user_input.strip().lower()

        if cmd == "/help":
            self.emit(self.help_text().rstrip())
            return True

        if cmd in ["/exit", "/quit"]:
            self.emit("再见！")
            return False

        if cmd == "/clear":
            self.messages.clear()
            self.emit("对话历史已清空")
            return True

        if cmd == "/model":
            current_model = self.model
            new_model = "deepseek-coder" if current_model == "deepseek-chat" else "deepseek-chat"
            self.model = new_model
            self.emit(f"模型已切换: {current_model} -> {new_model}")
            return True

        if cmd == "/history":
            count = len(self.messages)
            self.emit(f"当前对话历史: {count} 条消息")
            return True

        self.emit(f"未知命令: {cmd}")
        self.emit("输入 /help 查看帮助")
        return True

    async def handle_user_input(self, text: str) -> None:
        if text.startswith("/"):
            if not self.handle_command_ui(text):
                self.exit_requested = True
            return
        if self.session.app.is_running:
            await self.emit_in_terminal(self.render_user_message(text))
        else:
            self.emit(self.render_user_message(text))
        self.messages.append({"role": "user", "content": text})

        self.busy = True
        self.current_request_id += 1
        request_id = self.current_request_id
        if self.session.app.is_running:
            await self.emit_in_terminal("DeepSeek 正在思考...")
        else:
            self.emit("DeepSeek 正在思考...")

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self.api.chat, self.messages, self.model
            )
            if request_id != self.current_request_id:
                return
            if self.session.app.is_running:
                await self.emit_in_terminal("DeepSeek:")
                await self.emit_in_terminal(Markdown(response))
                await self.emit_in_terminal("")
            else:
                self.emit("DeepSeek:")
                self.emit(Markdown(response))
                self.emit("")
            self.messages.append({"role": "assistant", "content": response})
        except httpx.HTTPStatusError as e:
            if request_id != self.current_request_id:
                return
            if self.session.app.is_running:
                await self.emit_in_terminal(f"API 错误: {e.response.status_code}")
                await self.emit_in_terminal(e.response.text)
            else:
                self.emit(f"API 错误: {e.response.status_code}")
                self.emit(e.response.text)
            if self.messages:
                self.messages.pop()
        except Exception as e:
            if request_id != self.current_request_id:
                return
            if self.session.app.is_running:
                await self.emit_in_terminal(f"错误: {str(e)}")
            else:
                self.emit(f"错误: {str(e)}")
            if self.messages:
                self.messages.pop()
        finally:
            if request_id == self.current_request_id:
                self.busy = False

    async def chat_loop(self):
        """非全屏聊天：输出走终端滚动区，输入固定在底部"""
        self.emit(self.welcome_text().rstrip())

        queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        async def consumer():
            while True:
                text = await queue.get()
                if text is None:
                    return
                await self.handle_user_input(text)

        async def producer():
            while True:
                if self.exit_requested:
                    return
                while self.busy:
                    await asyncio.sleep(0.05)
                    if self.exit_requested:
                        return
                try:
                    text = await self.session.prompt_async(
                        self.render_panel_prompt,
                        prompt_continuation=lambda width, line_number, is_soft_wrap: HTML("<promptcontinuation> </promptcontinuation>"),
                        multiline=True,
                        wrap_lines=False,
                        key_bindings=self.kb,
                        refresh_interval=1.0,
                        placeholder=HTML("<placeholder>输入 (Enter 发送, Ctrl+J 换行, 最多10行)</placeholder>"),
                        style=self.input_style,
                        color_depth=ColorDepth.TRUE_COLOR,
                    )
                except (EOFError, KeyboardInterrupt):
                    return

                if not text.strip():
                    continue
                if text.count("\n") + 1 > 10:
                    text = "\n".join(text.splitlines()[:10])
                    self.emit("已截断为 10 行以内。")
                if text.startswith("/") and not self.handle_command_ui(text):
                    self.exit_requested = True
                    return
                await queue.put(text)

        with patch_stdout():
            consumer_task = asyncio.create_task(consumer())
            producer_task = asyncio.create_task(producer())
            await producer_task
            await queue.put(None)
            await consumer_task

    def emit(self, renderable) -> None:
        self.emit_sync(renderable)

    def emit_sync(self, renderable) -> None:
        with self.console.capture() as capture:
            self.console.print(renderable)
        output = capture.get()
        if not output:
            return
        print_formatted_text(ANSI(output), end="")

    async def emit_in_terminal(self, renderable) -> None:
        with self.console.capture() as capture:
            self.console.print(renderable)
        output = capture.get()
        if not output:
            return
        def _print():
            print_formatted_text(ANSI(output), end="")
        await run_in_terminal(_print, render_cli_done=False)

    def render_panel_prompt(self):
        if not self.panel_visible or self.panel_provider is None:
            return ""
        width = max(10, shutil.get_terminal_size((80, 20)).columns)
        height = 10
        lines = self.panel_provider.render(width, height)
        fragments = FormattedText()
        for i in range(height):
            line = lines[i] if i < len(lines) else ""
            padded = line[:width].ljust(width)
            fragments.append(("class:panel", padded + "\n"))
        return fragments

    def render_user_message(self, text: str):
        width = max(20, self.console.width)
        lines = text.splitlines() or [""]
        rendered = []
        for idx, line in enumerate(lines):
            prefix = "你: " if idx == 0 else "   "
            t = Text(prefix + line, style="bold fg:#eaf2ff on #303a46")
            if t.cell_len < width:
                t.pad_right(width - t.cell_len)
            rendered.append(t)
        return Group(*rendered)


def main():
    """主函数"""
    try:
        chat = AKGChat()
        asyncio.run(chat.chat_loop())
    except KeyboardInterrupt:
        print("\n\n再见！")
        sys.exit(0)
    except Exception as e:
        console = Console()
        console.print(f"[red]启动失败: {str(e)}[/red]")
        sys.exit(1)
