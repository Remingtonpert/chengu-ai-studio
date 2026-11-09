"""AI 智能体（第六板块）：自然语言命令 → 五大引擎路由 + 聊天/文案/语音。

设计原则与整个项目一致：
* 只依赖标准库，不引入任何新的第三方包或需要下载的权重；
* 大模型走用户配置里的本机 OpenAI 兼容端点（默认 Ollama），拿不到就诚实降级为规则应答；
* 语音播报走 Windows 自带 SAPI（免权重、可商用），不做任何“已支持语音输入”的虚假承诺；
* 智能体只负责“理解 + 路由 + 排队”，真正的生成仍由既有 TaskService 串行队列与各引擎 worker 完成，
  因此网页端与桌面端提交的任务在队列里完全等价。
"""

from app.agent.router import AgentCommand, parse_command
from app.agent.llm import LlmUnavailable, LocalLlmClient

__all__ = [
    "AgentCommand",
    "LlmUnavailable",
    "LocalLlmClient",
    "parse_command",
]
