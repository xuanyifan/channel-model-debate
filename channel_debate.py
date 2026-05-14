#!/usr/bin/env python3
"""
多模型迭代辩论 - 交叉论证版
3渠道: DeepSeek, PackyCode, MiniMax

每轮各模型给出论点 -> 互相挑刺 -> 优化己方 -> 重复直到共识或最大轮次

配置: 从 config.json 读取，请先复制 config.example.json 为 config.json 并填入 token
"""

import asyncio
import aiohttp
import os
import json
import sys
import signal
from typing import List, Dict, Any

# 当前执行阶段（用于信号处理报告进度）
_CURRENT_PHASE = "初始化"
_CURRENT_ROUND = 0

# 不可用渠道黑名单（Round 1 确认失败后加入，后续轮次直接跳过）
DEAD_CHANNELS: set = set()

# 全局配置
MODELS: Dict[str, Dict] = {}
MAX_ROUNDS = 10
TAVILY_API_KEY = ""
STATE_FILE = "/tmp/debate_state.json"
SEARCH_ENABLED = False
TOPIC = ""


def load_config(config_path: str = None):
    """从 config.json 加载配置"""
    global MODELS, MAX_ROUNDS, TAVILY_API_KEY

    if config_path is None:
        # 优先查找当前目录，其次技能目录
        possible_paths = [
            "config.json",
            os.path.join(os.path.dirname(__file__), "config.json"),
            os.path.expanduser("~/.claude/skills/channel-model-debate/config.json"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                config_path = p
                break
        else:
            print("❌ 未找到 config.json，请先复制 config.example.json 为 config.json 并填入 token")
            sys.exit(1)

    with open(config_path, "r") as f:
        config = json.load(f)

    MODELS = config.get("channels", {})
    MAX_ROUNDS = config.get("max_rounds", 10)
    TAVILY_API_KEY = config.get("tavily_api_key", "")

    if not MODELS:
        print("❌ config.json 中未找到 channels 配置")
        sys.exit(1)


def save_state(context: str, all_arguments: dict, max_rounds: int):
    """保存辩论状态，回归验证不通过时可 --resume 追加轮次"""
    with open(STATE_FILE, "w") as f:
        json.dump({
            "topic": TOPIC, "context": context,
            "all_arguments": all_arguments, "max_rounds": max_rounds,
            "dead_channels": list(DEAD_CHANNELS),
        }, f, ensure_ascii=False, indent=2)
    print(f"\n💾 辩论状态已保存到 {STATE_FILE}")


def load_state():
    """加载辩论状态"""
    with open(STATE_FILE) as f:
        data = json.load(f)
        DEAD_CHANNELS.update(data.get("dead_channels", []))
        return data


# 工具使用说明，注入每轮 prompt
TOOL_INSTRUCTION = """
【可用工具】
你可以使用 [SEARCH: 查询内容] 来搜索互联网获取实时信息。
当你对某个事实、数据、API规范、协议细节不确定时，请使用搜索工具。
搜索结果将在下一轮辩论中作为参考上下文提供。

使用示例:
[SEARCH: 安悦充电桩 API 接口规范]
[SEARCH: OCPP 2.0.1 协议充电桩启停流程]
"""


async def call_model(
    session: aiohttp.ClientSession,
    model_name: str,
    messages: List[Dict],
    timeout: int = 600,
    retries: int = 1,
) -> str:
    """调用单个模型 API，带容错重试"""
    if model_name in DEAD_CHANNELS:
        return f"[⏭️跳过] {model_name} 已在之前轮次确认不可用"
    cfg = MODELS[model_name]
    headers = {
        "Authorization": f"Bearer {cfg['auth_token']}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.7,
    }

    last_error = ""
    for attempt in range(retries + 1):
        try:
            async with session.post(
                f"{cfg['base_url']}/v1/messages",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(connect=10, total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data.get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            return item["text"]
                    if isinstance(content, list) and len(content) > 0:
                        return content[0].get("text", str(content[0]))
                    return str(content)

                elif resp.status == 429:
                    wait = 5 * (attempt + 1)
                    if attempt < retries:
                        print(f"  ⏳ [{model_name}] 限流(429)，等待{wait}秒重试({attempt+1}/{retries})...")
                    else:
                        print(f"  🚫 [{model_name}] 限流(429)，重试{retries}次后仍失败，跳过该渠道")
                    await asyncio.sleep(wait)
                    last_error = f"[⏭️跳过] {model_name} 限流(429)，已重试{retries+1}次"
                    continue

                elif resp.status in (500, 502, 503, 504):
                    wait = 3 * (attempt + 1)
                    if attempt < retries:
                        print(f"  ⏳ [{model_name}] 服务器异常({resp.status})，等待{wait}秒重试({attempt+1}/{retries})...")
                    await asyncio.sleep(wait)
                    last_error = f"[⏭️跳过] {model_name} 服务器错误 ({resp.status})"
                    continue

                elif resp.status == 403:
                    print(f"  🚫 [{model_name}] 访问被拒(403)，可能触发限流或配额耗尽，跳过该渠道")
                    last_error = f"[⏭️跳过] {model_name} 访问被拒(403)，配额/限流"
                    break

                else:
                    err = await resp.text()
                    last_error = f"[Error {resp.status}] {err[:200]}"
                    print(f"  ⚠️ [{model_name}] {last_error}")
                    break

        except asyncio.TimeoutError:
            timeout_str = f"{timeout}s" if timeout < 120 else f"{timeout//60}min"
            last_error = f"[⏭️跳过] {model_name} 请求超时({timeout_str})"
            print(f"  🚫 [{model_name}] 超时({timeout_str})，跳过该渠道，不重试")
        except aiohttp.ClientError as e:
            last_error = f"[{model_name}] 网络错误: {str(e)[:100]}"
            print(f"  ⚠️ {last_error}")
            break
        except Exception as e:
            last_error = f"[{model_name}] 未知错误: {str(e)[:100]}"
            print(f"  ⚠️ {last_error}")
            break

    return last_error



async def round_1_generate(session: aiohttp.ClientSession, topic: str, context: str = "") -> Dict[str, str]:
    """第1轮: 各模型独立生成初始论点（并发调用，先完成先输出）"""
    print(f"\n{'='*60}")
    print("📝 第1轮: 初始方案生成")
    print(f"{'='*60}")

    template = """请给出你对这个议题的核心观点，需要2-3个有力论据。
要求: 论点清晰、论据充分、逻辑严谨。
约束: 紧扣议题范围，不过度设计，不做范围外的延伸探讨。
{tool}"""

    # 显示所有模型状态
    for name in MODELS:
        print(f"  ⏳ {name} 生成中...")

    # 包装函数，每个模型用各自的 context
    async def _call(name):
        base_text = f"议题: {topic}"
        tool = TOOL_INSTRUCTION if SEARCH_ENABLED else ""
        body = template.format(tool=tool)
        prompt = f"{base_text}{context}\n\n{body}"
        messages = [{"role": "user", "content": prompt}]
        return name, await call_model(session, name, messages)

    results = {}
    tasks = [_call(name) for name in MODELS]
    for coro in asyncio.as_completed(tasks):
        model, result = await coro
        results[model] = result
        ok = not is_failed(result)
        status = "✅" if ok else "⚠️"
        preview = result[:200] + "..." if len(result) > 200 else result
        print(f"  {status} {model} 已完成\n     {preview[:150]}...")
    print()
    return results


async def round_n(session: aiohttp.ClientSession, topic: str, context: str, all_arguments: str, round_num: int) -> Dict[str, str]:
    """第N轮: 各模型基于所有论点互相挑刺并优化（并发调用，先完成先输出对比）"""
    print(f"\n{'='*60}")
    print(f"🔄 第{round_num}轮: 交叉辩论")
    print(f"{'='*60}")

    template = """以下是其他AI模型对上述议题的论证:

{all_args}

请仔细阅读其他模型的论点，然后:
1. 指出其中2个最薄弱的论据及原因
2. 针对被指出的弱点，强化你自己的论点
3. 给出优化后的最终论点

约束:
- 紧扣议题范围，不过度设计，不做范围外的延伸
- 挑刺要务实，只指出有实际影响的薄弱点，不为了挑刺而挑刺
- 如果其他模型的论点已经合理，可以说"无重大分歧"而不必强行挑刺

格式:
【弱点指出】: ...
【己方强化】: ...
【优化后论点】: ...
{tool}"""

    # 显示所有模型状态
    for name in MODELS:
        print(f"  ⏳ {name} 挑刺中...")

    # 包装函数，每个模型用各自的 context
    async def _call(name):
        base_text = f"议题: {topic}"
        tool = TOOL_INSTRUCTION if SEARCH_ENABLED else ""
        body = template.format(all_args=all_arguments, tool=tool)
        prompt = f"{base_text}{context}\n\n{body}"
        messages = [{"role": "user", "content": prompt}]
        return name, await call_model(session, name, messages)

    results = {}
    completed = []
    tasks = [_call(name) for name in MODELS]
    for coro in asyncio.as_completed(tasks):
        model, result = await coro
        results[model] = result
        ok = not is_failed(result)
        status = "✅" if ok else "⚠️"
        preview = result[:200] + "..." if len(result) > 200 else result
        print(f"\n  {status} {model} 已完成\n     {preview[:150]}...")

        # 即时对比
        if completed and ok:
            others_str = ', '.join(completed)
            print(f"  📊 {model} vs {others_str} 对比中...")
            others_weakpoints = []
            for om in completed:
                om_res = results.get(om, "")
                if not is_failed(om_res):
                    others_weakpoints.extend(_extract_weakpoints(om_res))
            current_weakpoints = _extract_weakpoints(result)
            overlap = set(current_weakpoints) & set(others_weakpoints)
            if overlap:
                print(f"     共识点: {', '.join(list(overlap)[:3])}")
            else:
                print(f"     观点独立")
        completed.append(model)
    print()
    return results


def _extract_weakpoints(text: str) -> list[str]:
    """从论点中提取弱点关键词"""
    import re
    keywords = []
    # 匹配【弱点指出】后的内容
    m = re.search(r'【弱点指出】[:：]\s*(.+?)(?:【|$)', text, re.DOTALL)
    if m:
        content = m.group(1)[:200]
        # 提取关键短语（中文分词简化版）
        keywords = [w.strip() for w in re.findall(r'[\u4e00-\u9fff]{2,6}', content)]
    return keywords


async def synthesize(topic: str, context: str, all_arguments: Dict[int, Dict[str, str]]) -> str:
    """最终汇总: 综合分析所有论点，输出共识"""
    print(f"\n{'='*60}")
    print("🧠 共识结论草稿（需经回归验证后方可交付）")
    print(f"{'='*60}\n")

    # 构建所有轮次的论证文本
    argument_text = ""
    for round_num, round_args in sorted(all_arguments.items()):
        argument_text += f"\n--- 第{round_num}轮 ---\n"
        for model, arg in round_args.items():
            argument_text += f"\n【{model}】\n{arg}\n"

    alive = [m for m in MODELS if m not in DEAD_CHANNELS]
    judge = alive[0] if alive else "DeepSeek"
    # 回归论证优先级: DeepSeek > PackyCode > 其他
    SYNTHESIS_PRIORITY = ["DeepSeek", "PackyCode", "MiniMax"]
    for p in SYNTHESIS_PRIORITY:
        if p in alive:
            judge = p
            break

    prompt_template = """你是资深技术顾问。以下是多模型AI对议题「{topic}」的多轮交叉论证:

{argument_text}
{ctx}

请基于以上论证，输出工程可交付的最佳实践结论：

若是设计方案类议题:
- 给出最终推荐方案（架构、接口、关键流程）
- 方案中每个选择要有论据支撑（为什么不选其他方案）
- 标注需要用户确认的权衡点

若是验证/评估类议题:
- 逐项列出合规点（✅）和不合规点（❌）
- 每个不合规点给出具体修复方案（代码级）
- 按优先级排序

格式:
【推荐方案】/【验证结论】: ...
【关键论据】: ...
【风险/权衡】: ...
【行动项】: ..."""

    prompt = prompt_template.format(topic=topic, argument_text=argument_text, ctx=context)
    messages = [{"role": "user", "content": prompt}]
    async with aiohttp.ClientSession() as session:
        result = await call_model(session, judge, messages)
        return result


def is_failed(result: str) -> bool:
    """判断模型调用是否失败"""
    return result.startswith(("[Error", "[⚠️", "[⏳", "[⏭️跳过"))


async def search_web(session: aiohttp.ClientSession, query: str) -> str:
    """调用 Tavily API 搜索，返回格式化的搜索结果"""
    try:
        async with session.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": 3,
                "search_depth": "basic",
            },
            timeout=aiohttp.ClientTimeout(connect=5, total=30),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if not results:
                    return f"[搜索 '{query}'] 未找到相关结果"
                lines = [f"【搜索结果: {query}】"]
                for r in results[:3]:
                    lines.append(f"- {r.get('title', 'N/A')}: {r.get('content', '')[:300]}")
                    if r.get('url'):
                        lines.append(f"  来源: {r['url']}")
                return "\n".join(lines)
            else:
                return f"[搜索 '{query}'] 失败: HTTP {resp.status}"
    except Exception as e:
        return f"[搜索 '{query}'] 异常: {str(e)[:100]}"


def extract_search_requests(text: str) -> list[str]:
    """从模型输出中提取 [SEARCH: ...] 请求"""
    import re
    return re.findall(r'\[SEARCH:\s*(.+?)\]', text)


async def execute_searches(session: aiohttp.ClientSession, round_results: dict) -> str:
    """扫描所有模型的输出，执行搜索，返回聚合结果"""
    all_queries = set()
    for model, output in round_results.items():
        if is_failed(output):
            continue
        queries = extract_search_requests(output)
        all_queries.update(q.strip() for q in queries)

    if not all_queries:
        return ""

    print(f"\n🔍 检测到 {len(all_queries)} 个搜索请求，执行中...")
    tasks = [search_web(session, q) for q in all_queries]
    results = await asyncio.gather(*tasks)

    search_context = "\n\n【本轮联网搜索结果】\n"
    for r in results:
        if r:
            search_context += r + "\n"
            print(f"  ✅ {r[:80]}...")

    return search_context


async def check_consensus(session: aiohttp.ClientSession, topic: str, all_arguments: Dict[int, Dict[str, str]]) -> tuple[bool, str]:
    """检测是否达成共识，返回 (是否达成共识, 共识内容)"""
    # 构建最近两轮的论证
    rounds = sorted(all_arguments.keys())
    if len(rounds) < 2:
        return False, ""  # 需要至少两轮才能检测共识

    recent_rounds = rounds[-2:]  # 最近两轮
    recent_args = ""
    for rn in recent_rounds:
        for model, arg in all_arguments[rn].items():
            if not is_failed(arg):
                recent_args += f"【{model}】: {arg[:500]}\n"

    prompt = f"""议题: {topic}

以下是各AI模型最近两轮的论证:

{recent_args}

请仔细分析以上论证，判断各模型是否已达成共识。

判断标准：
1. 各模型的核心结论是否基本一致？
2. 关键论据是否收敛到相似的观点？
3. 分歧是否已经消除或大幅缩小？

注意: 不要因为细微的措辞差异而判定未达成共识，务实地看本质结论是否一致。

如果达成共识，请用2-3句话总结共识内容。
如果未达成共识，请回答"未达成共识"。

格式：
【共识状态】: 已达成共识 / 未达成共识
【共识内容】: (如果达成共识，用2-3句话总结)"""

    messages = [{"role": "user", "content": prompt}]

    # 选一个可用模型检测共识，回归论证优先级
    alive = [m for m in MODELS if m not in DEAD_CHANNELS]
    judge = alive[0] if alive else "DeepSeek"
    SYNTHESIS_PRIORITY = ["DeepSeek", "PackyCode", "MiniMax"]
    for p in SYNTHESIS_PRIORITY:
        if p in alive:
            judge = p
            break
    result = await call_model(session, judge, messages, timeout=120)

    if is_failed(result):
        return False, ""

    # 检查是否达成共识
    if "已达成共识" in result or "共识状态】: 已达成" in result:
        # 提取共识内容
        lines = result.split('\n')
        consensus_content = ""
        in_content = False
        for line in lines:
            if "【共识内容】" in line:
                in_content = True
                consensus_content = line.replace("【共识内容】:", "").strip()
            elif in_content and line.strip() and not line.startswith("【"):
                consensus_content += " " + line.strip()
        return True, consensus_content if consensus_content else result[:200]

    return False, ""


def filter_failed(results: Dict[str, str]) -> Dict[str, str]:
    """过滤掉失败的模型调用"""
    return {k: v for k, v in results.items() if not is_failed(v)}


def load_context(paths: List[str]) -> str:
    """加载文件或目录内容作为上下文"""
    context = []
    for path in paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    context.append(f"=== 文件: {path} ===\n{content}")
            except Exception as e:
                context.append(f"=== 文件: {path} ===\n[读取失败: {e}]")
        elif os.path.isdir(path):
            try:
                for root, dirs, files in os.walk(path):
                    # 跳过隐藏目录和常见忽略目录
                    dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'target', 'dist', 'build')]
                    for f in files:
                        if f.startswith('.') or f.endswith(('.pyc', '.class', '.o', '.bin')):
                            continue
                        fp = os.path.join(root, f)
                        try:
                            with open(fp, "r", encoding="utf-8") as file:
                                content = file.read()
                                rel = os.path.relpath(fp, path)
                                context.append(f"=== {rel} ===\n{content}")
                        except:
                            pass
            except Exception as e:
                context.append(f"=== 目录: {path} ===\n[读取失败: {e}]")
        else:
            context.append(f"[路径不存在: {path}]")
    return '\n\n'.join(context)


async def main():
    global TOPIC, _CURRENT_PHASE, _CURRENT_ROUND, SEARCH_ENABLED

    # 加载配置
    load_config()

    # 信号处理：被 kill/中断时报告进度
    def _on_terminate(sig, frame):
        print(f"\n\n{'='*60}")
        print(f"⚠️ 进程被中断 (信号 {sig})")
        print(f"   当前阶段: {_CURRENT_PHASE}")
        if _CURRENT_ROUND > 0:
            print(f"   当前轮次: 第{_CURRENT_ROUND}轮")
        print(f"{'='*60}")
        sys.exit(1)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_terminate)
        except Exception:
            pass  # 非主线程可能注册失败
    if len(sys.argv) < 2:
        print("用法: python3 channel_debate.py <议题> [--ctx-file 文件] [--resume 文件] [--search]")
        print("       --ctx-file  Skill压缩后的上下文JSON文件")
        print("       --resume    从保存的状态文件恢复辩论，追加2轮修复")
        print("       --search    启用联网搜索")
        sys.exit(1)

    # 解析参数
    topic = None
    max_rounds = MAX_ROUNDS
    ctx_file = None
    resume_file = None
    args = sys.argv[1:]
    while args:
        if args[0] == "--ctx-file":
            args.pop(0)
            if args and not args[0].startswith('-'):
                ctx_file = args.pop(0)
        elif args[0] == "--resume":
            args.pop(0)
            resume_file = args.pop(0)
        elif args[0] == "--search":
            SEARCH_ENABLED = True
            args.pop(0)
        elif args[0].startswith('-'):
            print(f"未知参数: {args[0]}")
            sys.exit(1)
        elif not topic:
            topic = args.pop(0)
        else:
            args.pop(0)  # 忽略多余参数

    TOPIC = topic

    # 滑动窗口默认参数（可由 --ctx-file JSON 覆盖）
    max_window_chars = 30000
    min_preserve_rounds = 2

    # 加载上下文（Skill 层通过 --ctx-file 传入已压缩的内容和窗口参数）
    context = ""
    if ctx_file:
        print(f"\n📂 加载上下文文件: {ctx_file}")
        try:
            with open(ctx_file, "r") as f:
                ctx_data = json.load(f)
            context = ctx_data.get("context", "")
            window = ctx_data.get("window", {})
            max_window_chars = window.get("max_chars", max_window_chars)
            min_preserve_rounds = window.get("min_preserve_rounds", min_preserve_rounds)
            print(f"✅ 已加载压缩上下文 {len(context)} 字符，窗口参数 max_chars={max_window_chars} min_rounds={min_preserve_rounds}\n")
        except Exception as e:
            print(f"❌ 读取 {ctx_file} 失败: {e}\n")
    else:
        print(f"\nℹ️ 未指定 --ctx-file，无额外上下文\n")

    print(f"\n🤖 多模型迭代辩论")
    print(f"📌 议题: {TOPIC}")
    print(f"🔧 模型: {', '.join(MODELS.keys())}")
    print(f"📊 轮次上限: {MAX_ROUNDS}")

    # --resume: 从保存状态恢复，追加2轮修复
    resume_start_round = 1
    if resume_file:
        state = load_state()
        context = state.get("context", "")
        all_arguments_pre = state["all_arguments"]
        all_arguments_pre = {int(k): v for k, v in all_arguments_pre.items()}
        resume_start_round = max(all_arguments_pre.keys()) + 1
        max_rounds = resume_start_round + 1
        print(f"🔄 从 {resume_file} 恢复，第{resume_start_round}轮开始追加2轮修复")
        print(f"  已有 {len(all_arguments_pre)} 轮辩论记录")

    async with aiohttp.ClientSession() as session:
        if resume_file:
            all_arguments = all_arguments_pre
        else:
            all_arguments = {}

        # 第1轮: 独立生成
        if not resume_file:
            _CURRENT_PHASE = "第1轮: 初始方案生成"
            _CURRENT_ROUND = 1
            print(f"\n{'='*60}")
            print("📝 第1轮: 各模型独立思考，生成初始论点")
            print(f"{'='*60}\n")

            round1_results = await round_1_generate(session, TOPIC, context)
            all_arguments[1] = round1_results
            successful = filter_failed(round1_results)

            print(f"\n✅ 成功: {list(successful.keys())}")
            failed_models = [m for m in round1_results if is_failed(round1_results[m])]
            if failed_models:
                DEAD_CHANNELS.update(failed_models)
                print(f"🚫 以下渠道已确认不可用，后续轮次将跳过: {', '.join(failed_models)}")
                alive = [m for m in MODELS if m not in DEAD_CHANNELS]
                print(f"✅ 可用渠道 ({len(alive)}): {', '.join(alive)}")

            for model, arg in round1_results.items():
                if is_failed(arg):
                    print(f"\n【{model}】 ⚠️ {arg[:100]}")
                else:
                    preview = arg[:300] + "..." if len(arg) > 300 else arg
                    print(f"\n【{model}】\n{preview}")

            if SEARCH_ENABLED:
                search_ctx = await execute_searches(session, round1_results)
                if search_ctx:
                    context = (context or "") + search_ctx

            if len(successful) < 2:
                print("\n⚠️ 成功模型少于2个，无法进行交叉辩论，跳到最终汇总")
                max_rounds = 1

            # 构建首轮锚定：保留各模型初始立论根基，永不截断
            anchor_text = ""
            for m, a in round1_results.items():
                if not is_failed(a):
                    anchor_text += f"⚓【{m}首轮立论】: {a[:300]}\n"
            anchor_len = len(anchor_text)
            if anchor_text:
                print(f"📌 首轮锚定已锁定 ({anchor_len} 字符，{len([m for m,a in round1_results.items() if not is_failed(a)])} 模型)")

        # 第2-N轮: 交叉辩论 + 滑动窗口
        for round_num in range(resume_start_round, max_rounds + 1):
            _CURRENT_ROUND = round_num
            _CURRENT_PHASE = f"第{round_num}轮: 交叉辩论"
            alive_str = ', '.join(m for m in MODELS if m not in DEAD_CHANNELS)
            print(f"\n{'='*60}")
            print(f"🔄 第{round_num}轮: 交叉辩论 (参与: {alive_str})")
            print(f"{'='*60}\n")

            # 构建辩论历史文本（每个模型输出截断至300字）
            all_args_text = ""
            for rn, r_args in all_arguments.items():
                all_args_text += f"\n--- 第{rn}轮 ---\n"
                for m, a in r_args.items():
                    if not is_failed(a):
                        all_args_text += f"【{m}】: {a[:300]}...\n"

            # 锚定区 + 滑动区：锚定永不被截，滑动区在剩余配额内FIFO
            if anchor_text:
                all_args_text = anchor_text + "\n" + all_args_text
                effective_window = max_window_chars - anchor_len
            else:
                effective_window = max_window_chars

            if len(all_args_text) > max_window_chars:
                rounds_split = all_args_text.split("\n--- 第")
                if anchor_text:
                    rounds_split[0] = anchor_text + "\n" + rounds_split[0]
                if len(rounds_split) > min_preserve_rounds + 1:
                    head = rounds_split[0]
                    keep = rounds_split[-(min_preserve_rounds):]
                    all_args_text = head + "\n--- 第" + "\n--- 第".join(keep)
                    print(f"  📏 辩论历史超限，滑动窗口：锚定+保留最近 {min_preserve_rounds} 轮 ({len(all_args_text)} 字符)")

            round_n_results = await round_n(session, TOPIC, context, all_args_text, round_num)
            all_arguments[round_num] = round_n_results
            round_successful = filter_failed(round_n_results)

            print(f"✅ 本轮成功: {list(round_successful.keys())}")
            for model, arg in round_n_results.items():
                if is_failed(arg):
                    print(f"【{model}】 ⚠️ {arg[:100]}")
                else:
                    preview = arg[:200] + "..." if len(arg) > 200 else arg
                    print(f"【{model}】: {preview}")

            if len(round_successful) < 2:
                print("\n⚠️ 成功模型少于2个，停止后续交叉辩论")
                break

            if SEARCH_ENABLED:
                search_ctx = await execute_searches(session, round_n_results)
                if search_ctx:
                    context = (context or "") + search_ctx

            print(f"\n🔍 检查是否达成共识...")
            consensus_reached, consensus_content = await check_consensus(session, TOPIC, all_arguments)
            if consensus_reached:
                print(f"\n✅ 各模型已达成共识!")
                print(f"📌 共识内容: {consensus_content[:300]}")
                print(f"\n{'='*60}")
                print("🧠 共识结论草稿（需经回归验证后方可交付）")
                print(f"{'='*60}\n")
                try:
                    final = await synthesize(TOPIC, context, all_arguments)
                    print(final)
                    save_state(context, all_arguments, max_rounds)
                except Exception as e:
                    print(f"汇总失败: {e}")
                    print(f"\n共识内容: {consensus_content}")
                return

            await asyncio.sleep(1)

    # 最终汇总
    print(f"\n{'='*60}")
    print("🧠 共识结论草稿（达到轮次上限，需经回归验证后方可交付）")
    print(f"{'='*60}\n")

    try:
        final = await synthesize(TOPIC, context, all_arguments)
        print(final)
        save_state(context, all_arguments, max_rounds)
    except Exception as e:
        print(f"汇总失败: {e}")
        print("\n\n--- 各模型论点汇总 ---")
        for round_num, r_args in all_arguments.items():
            print(f"\n第{round_num}轮:")
            for m, a in filter_failed(r_args).items():
                print(f"【{m}】: {a[:500]}")


if __name__ == "__main__":
    asyncio.run(main())
