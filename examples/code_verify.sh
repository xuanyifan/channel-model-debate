#!/bin/bash
# 模式B: 代码交叉验证示例
# 使用场景: 对照文档验证现有代码实现

cd "$(dirname "$0")/.."

SCRIPT_DIR="$(pwd)/channel_debate.py"

echo "🤖 代码交叉验证"
echo "📌 议题: 验证代码实现是否符合规范"
echo ""

python3 "$SCRIPT_DIR" \
    "验证当前代码是否符合 RESTful API 最佳实践" \
    --ctx-file /tmp/debate_context.txt

echo ""
echo "✅ 验证完成"
echo "📄 输出: /tmp/debate_output.txt"
echo "📄 状态: /tmp/debate_state.json"
