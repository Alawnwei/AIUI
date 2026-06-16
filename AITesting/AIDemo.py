import asyncio
from dotenv import load_dotenv
#from langchain_openai import ChatOpenAI
from browser_use import Browser, BrowserProfile, BrowserSession, Agent
from browser_use.llm import ChatOpenAI
from browser_use.agent.views import AgentOutput
from browser_use.browser.views import BrowserStateSummary
import openlit # 导入 openlit 库
import logging
import json
from bs4 import BeautifulSoup
from playwright.async_api import Page

load_dotenv()

# ✨ 在代码最前面初始化 OpenLIT，它会自动监测 browser-use
openlit.init()

# ---------- 1. DOM 蒸馏器（基于 BeautifulSoup）----------
class DOMDistiller:
    """将 HTML 压缩为仅包含交互元素的精简版本"""

    BLOCKLIST_TAGS = {"script", "style", "svg", "path", "head", "meta", "link", "noscript", "iframe"}
    ALLOWED_ATTRS = {"id", "name", "type", "placeholder", "aria-label", "href", "role", "value", "checked", "disabled"}
    INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea", "form", "label"}

    @classmethod
    def distill(cls, html_content: str, max_nodes: int = 300) -> str:
        """返回精简后的 HTML 字符串"""
        soup = BeautifulSoup(html_content, 'html.parser')

        # 移除无用标签
        for tag in cls.BLOCKLIST_TAGS:
            for el in soup.find_all(tag):
                el.decompose()

        # 清理属性
        for tag in soup.find_all(True):
            attrs = tag.attrs.copy()
            for key in attrs:
                if key not in cls.ALLOWED_ATTRS:
                    del tag[key]

        # 删除空洞的 div/span
        for tag in soup.find_all(['div', 'span']):
            text = tag.get_text(strip=True)
            if not text and not any(tag.find_all(cls.INTERACTIVE_TAGS)):
                tag.decompose()

        # 限制节点数量（简单截取前 N 个节点）
        nodes = soup.find_all(True)
        if len(nodes) > max_nodes:
            # 保留交互元素 + 前 max_nodes//2 个节点
            keep = set()
            for node in nodes[:max_nodes // 2]:
                keep.add(node)
            for node in soup.find_all(cls.INTERACTIVE_TAGS):
                keep.add(node)
            for node in nodes:
                if node not in keep and hasattr(node, 'decompose'):
                    node.decompose()

        return soup.prettify()

# ---------- 2. 优化补丁（代理 BrowserSession.get_browser_state_summary）----------
async def _get_accessibility_text(page: Page) -> str | None:
    """使用 Playwright 内置 API 获取无障碍树并扁平化为文本"""
    try:
        await page.wait_for_load_state('networkidle')
        snapshot = await page.accessibility.snapshot(interesting_only=True)
        if not snapshot:
            return None

        def flatten(node, indent="", ref_counter=0):
            lines = []
            role = node.get('role', 'unknown')
            name = node.get('name', '')
            value = node.get('value', '')
            ref = f"ref_{ref_counter}"
            line = f"{indent}[{ref}] {role}"
            if name:
                line += f" '{name}'"
            if value:
                line += f" = {value}"
            lines.append(line)
            child_ref = ref_counter + 1
            for child in node.get('children', []):
                child_lines, child_ref = flatten(child, indent + "  ", child_ref)
                lines.extend(child_lines)
            return lines, child_ref

        flat_lines, _ = flatten(snapshot)
        return "\n".join(flat_lines)
    except Exception as e:
        logging.debug(f"获取 Accessibility Tree 失败: {e}")
        return None


def _get_page_from_session(session: BrowserSession) -> Page | None:
    """从 BrowserSession 中安全获取 Page 对象"""
    page = getattr(session, '_page', None)
    if page is None and hasattr(session, 'browser_context'):
        pages = session.browser_context.pages
        if pages:
            page = pages[0]
    return page


class _WrappedDOMState:
    """包裹 SerializedDOMState，替换 llm_representation 输出，其余属性透传回原始对象。"""

    def __init__(self, original, text):
        object.__setattr__(self, '_original', original)
        object.__setattr__(self, '_text', text)

    @property
    def selector_map(self):
        return self._original.selector_map

    def llm_representation(self, *args, **kwargs):
        return self._text

    def __getattr__(self, name):
        """未显式定义的属性/方法透传回原始 SerializedDOMState"""
        return getattr(self._original, name)


def patch_browser_session_with_optimizer(browser_session: BrowserSession,
                                         use_accessibility_tree: bool = True,
                                         use_dom_distillation: bool = True):
    """
    对 BrowserSession 实例打补丁，拦截 get_browser_state_summary。
    将 dom_state.llm_representation() 的输出替换为优化后的文本，
    但保留 selector_map 确保 Agent 能正常识别可交互元素。
    """
    original_get_browser_state_summary = browser_session.get_browser_state_summary

    async def optimized_get_browser_state_summary(*args, **kwargs):
        # 强制关掉截图，避免 15s 超时
        kwargs['include_screenshot'] = False
        state = await original_get_browser_state_summary(*args, **kwargs)

        if not state.dom_state:
            return state

        # 先尝试 AX Tree（快且小）
        if use_accessibility_tree:
            page = _get_page_from_session(browser_session)
            if page:
                acc_text = await _get_accessibility_text(page)
                if acc_text:
                    logging.debug("✅ 已使用 Accessibility Tree")
                    state.dom_state = _WrappedDOMState(state.dom_state, acc_text)
                    return state

        # 回退 DOM 蒸馏
        if use_dom_distillation:
            try:
                original_text = state.dom_state.llm_representation()
                if original_text:
                    distilled = DOMDistiller.distill(original_text)
                    logging.debug(
                        f"✅ DOM 蒸馏完成: {len(original_text)} -> {len(distilled)} 字符")
                    state.dom_state = _WrappedDOMState(state.dom_state, distilled)
            except Exception as e:
                logging.debug(f"DOM 蒸馏失败: {e}")

        return state

    # 使用 object.__setattr__ 绕过 Pydantic 验证（BrowserSession 是 BaseModel）
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
async def process_by_ai(task_description: str | None = None):
    """
    执行浏览器自动化测试任务。

    Args:
        task_description: 测试用例描述，来自 Excel 用例。为 None 时使用默认硬编码任务。
    """
    llm = ChatOpenAI(
        api_key="sk-d88a8e8f14894be28707adbae1b024c9",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus"
    )

    # 新版 browser_use 直接传参到 Browser（即 BrowserSession），无需 BrowserConfig
    # 注意：enable_default_extensions=False 是因为国内网络无法访问 Google 扩展商店，
    # 若启用会导致扩展下载超时（60s+）从而触发启动超时。
    browser = Browser(
        headless=False, # 展示界面
        disable_security=True, # 禁用部分安全限制，便于自动化
        enable_default_extensions=False, # 禁用在 Google 商店下载扩展，避免超时
        # chrome_instance_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", # edge路径
    # 不指定 chrome_instance_path，让 Playwright 自动管理浏览器生命周期
    )

    # 对 BrowserSession 打补丁优化 DOM
    # browser 本身就是 BrowserSession 的别名
    session = browser
    patch_browser_session_with_optimizer(
        session,
        use_accessibility_tree=True,
        use_dom_distillation=True
    )

    if task_description is None:
        url = "http://page-web-aia-test1.test.eks.za-gj-aws.net/ci/claimsubmission?61PwVNbuphp3Ztxkpy/vO6g/OjW1WVP+ULLUQzOVybiQn6U0x8Q="
        task = f"""1. 打开网页 {url}
                2. 如果出现REMINDER弹窗，点击next按钮
                3. Benefit Name下拉菜单选择"Lifestyle Assistance Allowance Benefit"
                4. 查看页面是否出现next按钮。最终返回结果：如果出现next按钮 返回"测试通过"，否则返回"测试不通过"
            """
    else:
        task = task_description

    agent = Agent(
        llm=llm,
        browser=browser,
        message_context="你正在进行web软件自动化测试，你最终返回的结果是测试通过或者不通过，不需要返回其他内容",
        task=task,
    )
    history = await agent.run()
    result = history.final_result()
    print("执行结果:", result)
    # 显式关闭浏览器，释放资源
    await browser.close()
    # 短暂等待，让子进程有时间彻底退出
    await asyncio.sleep(0.2)


if __name__ == "__main__":
    # 设置 🏷️browser_use 相关的日志级别为 DEBUG
    logging.getLogger('browser_use').setLevel(logging.DEBUG)
    # 设置控制台输出格式，方便阅读
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    asyncio.run(process_by_ai())