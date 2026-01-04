#!/usr/bin/env python3
"""
akg_cli - 基于 prompt_toolkit + rich 的 DeepSeek 聊天 CLI 工具
"""

import os
import sys
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
import httpx
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML, FormattedText
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.output import ColorDepth
import shutil
from prompt_toolkit.application import run_in_terminal
from rich.console import Console
from rich.markdown import Markdown


class DeepSeekAPI:
    """DeepSeek API 客户端"""
    
    BASE_URL = "https://api.deepseek.com/v1/chat/completions"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("请设置 DEEPSEEK_API_KEY 环境变量或通过参数传入 API Key")
        
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
    
    def chat(self, messages: List[Dict[str, str]], model: str = "deepseek-chat") -> str:
        """发送聊天请求"""
        response = self.client.post(
            self.BASE_URL,
            json={
                "model": model,
                "messages": messages,
                "stream": False,
            }
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    
    def chat_stream(self, messages: List[Dict[str, str]], model: str = "deepseek-chat"):
        """流式聊天请求"""
        with self.client.stream(
            "POST",
            self.BASE_URL,
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            }
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        continue


class CommandCompleter(Completer):
    """命令补全器"""
    
    COMMANDS = {
        "/help": "显示帮助信息",
        "/exit": "退出程序",
        "/quit": "退出程序",
        "/clear": "清空对话历史",
        "/model": "切换模型",
        "/history": "显示对话历史",
    }
    
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            for cmd, desc in self.COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)


class PanelProvider:
    """可插拔的面板基类"""
    
    def render(self, width: int, height: int) -> List[str]:
        return [""] * height


class TimePanel(PanelProvider):
    """实时时间面板"""
    
    def render(self, width: int, height: int) -> List[str]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [""] * height
        lines[0] = f"  时间：{now}"
        return lines


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
        self.last_prompt_height = 0
        self.debug_enabled = os.getenv("AKG_DEBUG") == "1"
        self.debug_log_path = os.getenv("AKG_DEBUG_LOG", "/tmp/akg_cli_debug.log")
        
        self._load_env_file()
        
        # 初始化 API
        try:
            self.api = DeepSeekAPI()
        except ValueError as e:
            self.console.print(f"[red]错误: {e}[/red]")
            sys.exit(1)
        
        # 配置键绑定：Enter 提交，Ctrl+J 换行
        self.kb = KeyBindings()
        
        @self.kb.add('c-j')
        def _(event):
            """Ctrl+J 换行"""
            line_count = event.current_buffer.text.count("\n") + 1
            if line_count >= 10:
                return
            event.current_buffer.insert_text('\n')
        
        @self.kb.add(Keys.Enter, eager=True)
        def _(event):
            """Enter 键提交"""
            # 如果输入为空，不提交
            if not event.current_buffer.text.strip():
                return
            event.current_buffer.validate_and_handle()
        
        @self.kb.add('c-c')
        def _(event):
            """Ctrl+C 中断当前请求"""
            if self.busy:
                self.current_request_id += 1
                self.busy = False
                self.emit("已中断当前请求。")
            else:
                self.exit_requested = True
                event.app.exit(result="")
        
        @self.kb.add('f2')
        def _(event):
            """F2 切换面板显示"""
            self.panel_visible = not self.panel_visible
            event.app.invalidate()
            self.debug_log(f"F2 toggle panel_visible={self.panel_visible}")
        
        # 初始化提示会话
        self.input_style = Style.from_dict({
            'default': 'fg:#f0f0f0 bg:#444444',
            'input': 'fg:#f0f0f0 bg:#444444',
            'prompt': 'bold fg:#f0f0f0 bg:#444444',
            'promptcontinuation': 'fg:#f0f0f0 bg:#444444',
            'placeholder': 'fg:#b0b0b0 bg:#444444',
            'panel': 'fg:#e6e6e6 bg:#303a46',
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
    
    def handle_command(self, user_input: str) -> bool:
        """处理命令，返回是否继续（旧控制台模式保留）"""
        cmd = user_input.strip().lower()
        
        if cmd == "/help":
            self.console.print(self.help_text())
            return True
        
        elif cmd in ["/exit", "/quit"]:
            self.console.print("\n[dim]再见！[/dim]")
            return False
        
        elif cmd == "/clear":
            self.messages.clear()
            self.console.print("[green]对话历史已清空[/green]")
            return True
        
        elif cmd == "/model":
            current_model = self.model
            new_model = "deepseek-coder" if current_model == "deepseek-chat" else "deepseek-chat"
            self.model = new_model
            self.console.print(f"[green]模型已切换: {current_model} -> {new_model}[/green]")
            return True
        
        elif cmd == "/history":
            count = len(self.messages)
            self.console.print(f"[cyan]当前对话历史: {count} 条消息[/cyan]")
            return True
        
        else:
            self.console.print(f"[yellow]未知命令: {cmd}[/yellow]")
            self.console.print("输入 /help 查看帮助")
            return True
    
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
        
        self.emit(f"你: {text}")
        self.messages.append({"role": "user", "content": text})
        
        self.busy = True
        self.current_request_id += 1
        request_id = self.current_request_id
        self.emit("DeepSeek 正在思考...")
        
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, self.api.chat, self.messages, self.model
            )
            if request_id != self.current_request_id:
                return
            self.emit("DeepSeek:")
            self.emit(Markdown(response))
            self.emit("")
            self.messages.append({"role": "assistant", "content": response})
        except httpx.HTTPStatusError as e:
            if request_id != self.current_request_id:
                return
            self.emit(f"API 错误: {e.response.status_code}")
            self.emit(e.response.text)
            if self.messages:
                self.messages.pop()
        except Exception as e:
            if request_id != self.current_request_id:
                return
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
                    self.debug_log("producer: begin prompt")
                    self.debug_log(f"app.full_screen(before)={self.session.app.full_screen}")
                    self.debug_log(f"app.full_screen(after)={self.session.app.full_screen}")
                    self.last_prompt_height = 0
                    text = await self.session.prompt_async(
                        self.render_panel_prompt,
                        prompt_continuation=lambda width, line_number, is_soft_wrap: HTML("<prompt> </prompt>"),
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
                finally:
                    self.debug_log("producer: end prompt")
                    self.debug_log(f"renderer.in_alt={getattr(self.session.app.renderer,'_in_alternate_screen', None)}")
                    self.debug_log(f"renderer.in_alt(after reset)={getattr(self.session.app.renderer,'_in_alternate_screen', None)}")
                
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
    
    def chat_loop_legacy(self):
        """旧的控制台模式（保留但不使用）"""
        self.emit(self.welcome_text())

    def emit(self, renderable) -> None:
        with self.console.capture() as capture:
            self.console.print(renderable)
        output = capture.get()
        if not output:
            return
        if self.session.app.is_running:
            self.debug_log("emit: run_in_terminal")
            async def _run():
                def _print():
                    print_formatted_text(ANSI(output), end="")
                await run_in_terminal(_print, render_cli_done=False)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_run())
            except RuntimeError:
                print_formatted_text(ANSI(output), end="")
        else:
            self.debug_log("emit: direct print")
            print_formatted_text(ANSI(output), end="")
    
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
            text = padded + "\n"
            fragments.append(("class:panel", text))
        return fragments
    
    
    def debug_log(self, message: str) -> None:
        if not self.debug_enabled:
            return
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.debug_log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts} {message}\n")
        except Exception:
            return


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


if __name__ == "__main__":
    main()
