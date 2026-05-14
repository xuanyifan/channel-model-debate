#!/bin/bash
set -e

SKILLS_DIR="$HOME/.claude/skills"
PROJECT_NAME="channel-model-debate"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$SKILLS_DIR/$PROJECT_NAME"

echo "📦 安装 $PROJECT_NAME"

# 创建技能目录
mkdir -p "$TARGET_DIR"

# 复制技能书并替换路径为当前项目目录
sed "s|PROJECT_DIR_PLACEHOLDER|$PROJECT_DIR|g" "$PROJECT_DIR/SKILL.md" > "$TARGET_DIR/SKILL.md"

# 创建 config.json（如果项目目录下不存在）
if [ ! -f "$PROJECT_DIR/config.json" ]; then
    cp "$PROJECT_DIR/config.example.json" "$PROJECT_DIR/config.json"
    echo "⚠️ 已创建 config.json，请在项目目录编辑填入 API keys"
fi

# 安装 Python 依赖
echo "📦 安装 Python 依赖..."
pip install aiohttp --quiet

echo ""
echo "✅ 安装完成!"
echo "  技能目录: $TARGET_DIR （仅 SKILL.md）"
echo "  项目目录: $PROJECT_DIR （脚本 + 配置）"
echo ""
echo "下一步:"
echo "  1. 编辑 $PROJECT_DIR/config.json 填入 API keys"
echo "  2. 在 Claude Code 中使用: /channel-model-debate"
