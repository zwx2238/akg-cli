from prompt_toolkit.completion import Completer, Completion


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
