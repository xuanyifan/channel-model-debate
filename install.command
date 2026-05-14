#!/bin/bash
set -e

SKILLS_DIR="$HOME/.claude/skills"
PROJECT_NAME="channel-model-debate"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$SKILLS_DIR/$PROJECT_NAME"

echo "📦 安装 $PROJECT_NAME"

# 创建技能目录
mkdir -p "$TARGET_DIR"

# 技能书始终更新
sed "s|PROJECT_DIR_PLACEHOLDER|$PROJECT_DIR|g" "$PROJECT_DIR/SKILL.md" > "$TARGET_DIR/SKILL.md"
echo "✅ 技能书已更新"

# 配置文件智能合并：保留用户 token，补充版本新增字段
if [ ! -f "$PROJECT_DIR/config.json" ]; then
    cp "$PROJECT_DIR/config.example.json" "$PROJECT_DIR/config.json"
    echo "⚠️ 已创建 config.json，请编辑填入 API keys"
else
    python3 -c "
import json
example = json.load(open('$PROJECT_DIR/config.example.json'))
current = json.load(open('$PROJECT_DIR/config.json'))
# 深度合并：example 中的新字段补入 current，已有字段保留用户值
def merge(base, defaults):
    for k, v in defaults.items():
        if k not in base:
            base[k] = v
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
merge(current, example)
json.dump(current, open('$PROJECT_DIR/config.json','w'), ensure_ascii=False, indent=2)
print('✅ config.json 已更新（保留已有配置，补充新增字段）')
" 2>/dev/null || echo "ℹ️ config.json 已存在，跳过合并（python3 不可用）"
fi

# 安装 Python 依赖（已安装则跳过）
if python3 -c "import aiohttp" 2>/dev/null; then
    echo "✅ aiohttp 已安装，跳过"
else
    echo "📦 安装 aiohttp..."
    pip install aiohttp --quiet || pip install aiohttp --break-system-packages --quiet
fi

echo ""
echo "✅ 安装完成!"
echo "  技能目录: $TARGET_DIR （仅 SKILL.md）"
echo "  项目目录: $PROJECT_DIR （脚本 + 配置）"
echo ""
echo "下一步:"
echo "  1. 编辑 $PROJECT_DIR/config.json 填入 API keys"
echo "  2. 在 Claude Code 中使用: /channel-model-debate"
