from datetime import datetime
from typing import List


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
