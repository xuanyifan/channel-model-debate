#!/bin/bash
# 模式B: 代码交叉验证示例
# 使用场景: 对照文档验证现有代码实现

cd "$(dirname "$0")/.."

SCRIPT_DIR="$(pwd)/channel_debate.py"

echo "🤖 模式B: 代码交叉验证"
echo "📌 议题: 验证代码实现是否符合 RESTful API 规范"
echo ""

# 示例：验证当前目录下的代码
python3 "$SCRIPT_DIR" \
    "验证当前代码是否符合 RESTful API 最佳实践" \
    --ctx ./

echo ""
echo "✅ 验证完成，查看 /tmp/debate_state.json 可恢复验证"
