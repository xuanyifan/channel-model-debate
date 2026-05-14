# channel-model-debate

多模型迭代辩论 - 交叉论证版

[![Version](https://img.shields.io/badge/version-v1.0.0-blue)](https://github.com/xuanyifan/channel-model-debate/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)](#)
[![Python](https://img.shields.io/badge/python-3.8%2B-green)](#)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-CLI-orange)](https://docs.anthropic.com/en/docs/claude-code)

支持多渠道动态配置的 AI 交叉辩论引擎。配置多个兼容 Anthropic API 格式的渠道模型，让它们交叉辩论、互相挑刺，对设计方案进行迭代优化，或对代码实现进行交叉验证，自动收敛至工程可交付的最佳实践结论。

## 安装

双击 `install.command` 运行，或终端执行：

```bash
cp config.example.json config.json   # 编辑填入 API keys
./install.command
```
> 首次运行 `install.command` 时会自动从 `config.example.json` 创建 `config.json`（如不存在）。

## 使用方法

### Claude Code 技能方式

```
/channel-model-debate 设计一个用户登录模块
/channel-model-debate 验证代码是否符合接口规范
```

### 命令行直接调用

```bash
# 设计方案辩论
python3 channel_debate.py "设计一个可扩展的用户认证系统" --search --ctx-file /tmp/debate_context.txt

# 代码交叉验证
python3 channel_debate.py "验证代码是否符合 RESTful 规范" --ctx-file /tmp/debate_context.txt

# 恢复中断的辩论
python3 channel_debate.py "议题" --resume /tmp/debate_state.json
```

> `--search` 是可选的小技能，模型在辩论中对不确定的内容通过联网论证，不区分模式。

## 工作原理

1. **第1轮**：各模型独立生成初始论点（并发调用）
2. **第2-N轮**：模型互相挑刺并优化论点
3. **检测共识**：每轮后自动检测
   - 若未达成共识 → 继续迭代优化
   - 若达成共识 → 进入回归验证
4. **回归验证**：验证共识结论的可靠性
5. **输出结论**：工程可交付的最佳实践结论

**最大轮次**：默认 10 轮，可通过 `config.json` 的 `max_rounds` 配置

### 上下文压缩与迭代窗口

- **初始压缩**：由 Skill 层在启动脚本前完成，根据渠道模型的上下文限制做语义摘要，写入 `/tmp/debate_context.txt`（JSON 格式，含 `context` 和 `window` 参数），脚本通过 `--ctx-file` 读取
- **迭代窗口**：辩论进行中多轮论点累积超限时，脚本自动按 FIFO 滑动窗口丢弃最旧轮次，保留最近 N 轮（默认 2 轮），纯规则执行不调 LLM

## 示例脚本

```bash
# 设计方案辩论
chmod +x examples/design_debate.sh
./examples/design_debate.sh

# 代码交叉验证
chmod +x examples/code_verify.sh
./examples/code_verify.sh
```

## 配置

复制 `config.example.json` 为 `config.json`，填入你的配置：

```json
{
  "channels": {
    "渠道名称": {
      "base_url": "https://api.example.com/anthropic",
      "auth_token": "YOUR_TOKEN",
      "model": "model-name"
    }
  },
  "tavily_api_key": "YOUR_TAVILY_API_KEY",
  "max_rounds": 10
}
```

| 配置项 | 说明 |
|--------|------|
| `channels` | 任意兼容 Anthropic API 格式的渠道，可自由增删 |
| `tavily_api_key` | 联网搜索 API key（可选，不填则模型辩论时无法联网） |
| `max_rounds` | 辩论最大轮次，默认 10 |

> `config.example.json` 中预设了 3 个渠道配置模板（DeepSeek、PackyCode、MiniMax），仅供格式参考，可按需修改或增减。

## 依赖

- Python 3.8+
- aiohttp

```bash
pip install -r requirements.txt
```

## 下载

| 版本 | 系统 | zip | tar.gz |
|------|------|-----|-------|
| [v1.0.0](https://github.com/xuanyifan/channel-model-debate/releases/tag/v1.0.0) | macOS | [下载](https://github.com/xuanyifan/channel-model-debate/archive/refs/tags/v1.0.0.zip) | [下载](https://github.com/xuanyifan/channel-model-debate/archive/refs/tags/v1.0.0.tar.gz) |
