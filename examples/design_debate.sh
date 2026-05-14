#!/bin/bash
# 模式A: 设计方案辩论示例
# 使用场景: 头脑风暴，从零设计新方案

cd "$(dirname "$0")/.."

SCRIPT_DIR="$(pwd)/channel_debate.py"

echo "🤖 模式A: 设计方案辩论"
echo "📌 议题: 设计一个可扩展的用户认证系统"
echo ""

python3 "$SCRIPT_DIR" \
    "设计一个可扩展的用户认证系统，支持多种登录方式（账号密码、手机号、OAuth）" \
    --search

echo ""
echo "✅ 辩论完成，查看 /tmp/debate_state.json 可恢复辩论"
