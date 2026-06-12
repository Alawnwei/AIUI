import asyncio
import json
import logging
import re

from dotenv import load_dotenv

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.agent.views import AgentOutput
from browser_use.browser.views import BrowserStateSummary
from browser_use.llm import ChatOpenAI

import openlit

load_dotenv()

# ✨ 在代码最前面初始化 OpenLIT，它会自动监测 browser-use
openlit.init()

# ---------- 1. 优化补丁（代理 BrowserSession.get_browser_state_summary）----------
def patch_browser_session_with_optimizer(browser_session: BrowserSession):
    """
    对 BrowserSession 实例打补丁，拦截 get_browser_state_summary。
    核心优化：关掉截图避免自建页面 15s+ 超时。
    """
    original_get_browser_state_summary = browser_session.get_browser_state_summary

    async def optimized_get_browser_state_summary(*args, **kwargs):
        kwargs['include_screenshot'] = False
        return await original_get_browser_state_summary(*args, **kwargs)

    object.__setattr__(browser_session, 'get_browser_state_summary', optimized_get_browser_state_summary)
    return browser_session


# 定义一个异步回调函数，在每一步开始前被调用
async def step_callback(browser_state: BrowserStateSummary,
                        agent_output: AgentOutput,
                        step_number: int):
    """打印每一步的详细信息，用来观察Agent的内部思考"""
    print(f"\n{'=' * 20} STEP {step_number} START {'=' * 20}")

    # 打印当前页面的标题和URL
    print(f"📄 Page Title: {browser_state.title}")
    print(f"🔗 Current URL: {browser_state.url}")

    # 打印大模型给出的详细思考过程
    if agent_output.current_state:
        # 评估上一步的目标完成情况
        print(f"🧠 Evaluation of Previous Goal: {agent_output.current_state.evaluation_previous_goal}")
        print(f"💭 Memory: {agent_output.current_state.memory}")  # 对应 'current_state_summary'
        print(f"🎯 Next Goal: {agent_output.current_state.next_goal}")

        # ✅ 修复后的动作打印 - 适配新版 list 类型
    if agent_output.action:
        # 如果 action 是列表，逐个打印
        if isinstance(agent_output.action, list):
            print("⚡️ Actions to take:")
            for i, act in enumerate(agent_output.action):
                # 如果元素是 Pydantic 模型，尝试转 dict；否则直接打印
                if hasattr(act, 'model_dump'):
                    print(f"   {i + 1}. {json.dumps(act.model_dump(), indent=2)}")
                else:
                    print(f"   {i + 1}. {act}")
        else:
            # 兼容旧版单个模型的情况（通常不会走到这里）
            print(f"⚡️ Action to take: {agent_output.action}")

    print(f"{'=' * 50}\n")

# 定义一个回调函数，在整个任务完成时被调用
async def done_callback(history):
    print("\n🎉 TASK COMPLETED!")
    # 打印最终结果
    final_result = history.final_result()
    print(f"📊 Final Result: {final_result}")


# 测试过程 🔪
async def process_by_ai(case: dict | None = None):
    """
    执行浏览器自动化测试任务。

    Args:
        case: 测试用例记录（dict），来自 Excel 的用例行，包含字段如"用例描述"、"用例标题"等。
              为 None 时使用默认硬编码任务。
    """
    DEFAULT_URL = "http://page-web-aia-test1.test.eks.za-gj-aws.net/ci/claimsubmission?61PwVNbuphp3Ztxkpy/vO6g/OjW1WVP+ULLUQzOVybiQn6U0x8Q="
    task_description = case.get("用例描述") if case else None
    profile = BrowserProfile(headless=False, disable_security=True)
    browser_session = BrowserSession(browser_profile=profile)
    patch_browser_session_with_optimizer(browser_session)

    if task_description is None:
        task = f"""1. 打开网页 {DEFAULT_URL}
                2. 如果出现REMINDER弹窗，点击next按钮
                3. Benefit Name下拉菜单选择"Lifestyle Assistance Allowance Benefit"
                4. 查看页面是否出现next按钮。最终返回结果：如果出现next按钮 返回"测试通过"，否则返回"测试不通过"
            """
    else:
        task = re.sub(r'\{DEFAULT_URL}|{url}', DEFAULT_URL, task_description, flags=re.IGNORECASE)
        print(f"[DEBUG] Task sent to Agent:\n{task}\n")

    llm = ChatOpenAI(
        api_key="sk-4iYDw8D3Sg5FSMFSVqg5Xw",
        base_url="https://litellm.peak3.com",
        model="openai/deepseek-v4-flash"
    )
    agent = Agent(
        llm=llm,
        browser_session=browser_session,
        message_context="你正在进行web软件自动化测试，你最终返回的结果是测试通过或者不通过，不需要返回其他内容",
        task=task,
        register_new_step_callback=step_callback,
        register_done_callback=done_callback,
        max_steps=15,
        use_judge=False  # 关掉 judge，避免结束后额外等待
    )
    history = await agent.run()
    result = history.final_result()
    print("执行结果:", result)
    await browser_session.close()
    await asyncio.sleep(0.2)


# 设置 🏷️browser_use 相关的日志级别为 DEBUG
logging.getLogger('browser_use').setLevel(logging.DEBUG)
# 设置控制台输出格式，方便阅读
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# if __name__ == "__main__":
#     asyncio.run(process_by_ai())