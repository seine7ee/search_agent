"""Run ten trajectory-synthesis queries concurrently without CLI arguments.

Edit the constants in the configuration block below, then execute:

    python3 run_batch_trajectories.py
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import config.config as cfg
from deep_search.observability import configure_logging
# from deep_search.orchestrator import DeepSearchOrchestrator, OrchestratorConfig
from deep_search_claude.orchestrator import DeepSearchOrchestrator, OrchestratorConfig
from deep_search.orchestrator import DeepSearchOrchestrator as DeepSearchOrchestratorFullWebs
from deep_search.orchestrator import OrchestratorConfig as DeepSearchOrchestratorFullWebsConfig

from deep_search.paths import build_trajectory_path
from deep_search_baseline.orchestrator import BaselineConfig, BaselineOrchestrator
from deep_search_single_agent.orchestrator import (
    OrchestratorConfig as SingleAgentConfig,
    SingleAgentOrchestrator,
)
from deep_search_multi_agent.orchestrator import (
    MultiAgentOrchestrator,
    OrchestratorConfig as MultiAgentConfig,
)
from deep_search_multi_agent_v1.orchestrator import (
    MultiAgentOrchestrator as MultiAgentOrchestratorV1,
    OrchestratorConfig as MultiAgentConfigV1,
)
from get_model_infer import request_model
from tools.web_search import web_search


SCHEMES = {
    "scheme_a", "scheme_b", "single_agent", "multi_agent", "scheme_a_full_webs",
    "multi_agent_v1",
}
Orchestrator = (
    DeepSearchOrchestrator | BaselineOrchestrator | SingleAgentOrchestrator | MultiAgentOrchestrator
    | DeepSearchOrchestratorFullWebs | MultiAgentOrchestratorV1
)


# ======================== Batch configuration ========================
# This list intentionally contains exactly ten queries. Replace the text in place
# when preparing a new batch; no command-line argument parsing is used.
QUERIES = [
    "小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？",
    "对比近三年全球主要车企的纯电动车销量及市场份额变化。",
    "梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。",
    "目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？",
    "分析近五年全球半导体产业链的关键并购及其影响。",
    "比较中国主要云计算厂商最新公开的收入、增速和市场定位。",
    "全球人形机器人量产进展如何，主要厂商的时间表是什么？",
    "近三年动力电池技术有哪些重要突破，分别由哪些企业推动？",
    "比较当前主流 AI 编程助手的功能、模型和企业部署能力。",
]
#
# QUERIES = [
#     "比较中国主要云计算厂商最新公开的收入、增速和市场定位。",
#     "全球人形机器人量产进展如何，主要厂商的时间表是什么？",
#     "近三年动力电池技术有哪些重要突破，分别由哪些企业推动？",
#     "比较当前主流 AI 编程助手的功能、模型和企业部署能力。",
# ]
#
# QUERIES = [
#     "摩拜单车最近一个完整财年的营收是多少?"
# ]

# QUERIES = [
#     "DeepSeek 创始人本科就读的大学，现任校长是谁？",
#     "《流浪地球2》导演的上一部长片，豆瓣评分多少？",
#     "ChatGPT 首次发布当天，英伟达股价收盘价是多少？",
#     "中国首次火星着陆当天，NASA 在任局长是谁？",
#     "特斯拉与比亚迪 2024 年全年销量相差多少辆？",
#     "最近两届冬奥会中国金牌总数比日本多几枚？",
#     "网传每天必须喝八杯水有科学依据吗？",
#     "各机构对 2025 年中国 GDP 增速预测差异有多大？",
#     "学术界对 Mamba 能否替代 Transformer 有哪些主要争议？",
#     "预算两万元，OLED 和 Mini LED 电视怎么选？"
# ]

# QUERIES = ["任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？"]

QUERIES = [
    "三角洲行动主播杨齐家和三角洲行动主播包子是什么关系？",
    "魔兽世界燃烧的远征，阿拉基的护符，61精英怪怎么打",
    "雅思考试大范围停考，为什么会出现停考的情况，此次停考有什么影响？",
    "在南京开网约车是不是需要什么证件？",
    "目前合规的超大容量可以上飞机高铁的充电宝有哪些品牌？",
    "如何看待游客向熊猫吐口水被终身禁止入园",
    "促甲状腺激素释放激素是血液循环吗",
    "上海期货交易所的行情为什么是延时半小时，在交易所网站可以下载交易品种从上市交易第一天到最近一天的所有日交易数据吗",
    "为什么美债利率那么高？嗯但是我们在国内的宽带的基金为什么是呈下跌趋势的？这是为什么",
    "中国女孩因1封信获全球唯一金奖，这封信是什么内容？为什么能获奖？",
    "常德市市里除西园幼儿园是公立的还有哪里也是公立的",
    "中国《山海经》VS外国《山海经》谁会赢",
    "睡眠空气净化器有哪些，推荐最优质的品牌",
    "假设，一个儿童水上乐园，没有收费出现事故，这个运营是否有责任",
    "唐国强几位妻子目前的现状如何",
    "凤凰县这边可以跨乡镇补办户口本吗？",
    "王者荣耀姜子牙如何封神",
    "杭州的jazz和hipo舞蹈教学不错的机构推荐",
    "帮我寻找若干加工制造双螺杆挤出机筒体的机械加工制造厂家",
    "肉苁蓉和锁阳药材泡酒还可以搭配哪些中药材",
    "指定居所监视居住机制，在我国与国外有什么相同和不同？以及在我国该项制度它的发展过程和，这些变化与巩固证据强制措施有何联系？（查找要求：必须是权威人士的文献或者权威网站里的内容整合）",
    "泉州泉运闽发出租车大众有吗",
    "你知不知道，微信视频号里面的一个主播叫我的小冰糖，专门写小说的。",
    "俄罗斯鼠疫有可能传到中国吗",
    "PET塑料瓶装一般调料安全吗？",
    "中国电信提前把1000兆宽带速度降成300兆是属于违约吗",
    "墨西哥贩毒组织集团详细解析，他们与外国企业和个人（含着中国）之间的关系，以及相关生态及运行模式。",
    "在《斗罗大陆：武魂觉醒》和《斗罗大陆：魂师对决》中，哪位角色在活动中同时登场并引起玩家关注？",
    "上海申通地铁公司现阶段盈利模式分析，从盈利点、盈利源、盈利杠杆、盈利对象、盈利屏障五个点进行分析，主要侧重于已有问题进行分析，并且说明目前存在的问题",
    "哪位中国人民大学信息学院的校友在2020年12月6日至8日于福建莆田举行的第五届中国电商讲师大赛全国总决赛中获胜并获得特等奖？",
    '在汽车之家App的论坛里，搜一下"特斯拉Model Y"的真实车主提车作业，看看大家普遍反馈的优缺点是什么。',
    "鸿蒙系统和其他操作系统共享文件的方法有什么不同？",
    "在JavaScript中如何通过闭包（dclosure）实现私有变量？",
    "商用电磁灶油炸，糯米粉和玉米淀粉按3比1的比例，水粉比例为1比1.2挂糊油炸食品，油温在160°的情况下，淀粉糊化会在几分钟完成，不能超过多少时间，不然会干巴巴影响食品口感不酥脆",
    "系统应用、三方应用、预置应用有什么差别",
    "s800是麦弗逊悬挂吗",
    "今年华为今年荣耀magic7的价格是多少",
    "帮我整理2023年全国两会关于教育领域的核心内容和代表提案，要包含政府工作报告的表述和各教育阶段的具体建议",
    "帮我找一下教育部最新批准的中外合作办学名单，要包含全国各地正规院校的信息",
    "在2025年前四个月中，EHA将在哪些城市举办不同主题的科研会议，并分别于几月几号开始？",
    "澳洲矿业股深度分析 包括LRV FML GLN BC8 HRZ EQR",
    "乳糖酸有微生物发酵来源吗？流程是怎样的？市面上乳糖酸的生产一般有哪些方式和生产流程？各个提取方法做个详细的对比。",
    "深圳市在新能源汽车领域，应该如何更好应对欧美贸易壁垒？",
    "从资源优势，全球局势，经济周期分析洛阳钼业是否值得入手",
    "智界S7和享界S9哪个更好",
    "糖尿病还能喝有糖饮料么？保险值是多少？摄入了少量含糖饮料如果血糖值控制良好也对身体有伤害么？",
    "糖皮质激素和醛固酮都属于肾上腺皮质激素吗",
    "浙江农林大学2023年数学与计算机科学学院的教职工团建活动和2021年教职工集体生日会的学院领导分别是谁主持的？",
    "北汽集团在文教结合的活动中，哪位教师首次参与了援藏支教，并同时有哪些文化活动也在这个时段得到北汽的支持？",
    "安徽省十五五规划关于矿山这边有哪些信息",
    "华为手表的手链断了怎么办",
    "北京工业大学计算机学院的高明霞教师，哪一年开始参与在研的北京市自然科学基金委员会项目？",
    "共基极放大电路和基极分压式射极偏置电路的区别？他们是一个电路吗",
    "阿昔洛韦和泛昔洛韦有什么差别",
    "浙江省国际大学生创新大赛银奖",
    "胡塞武装的导弹到底是谁提供的",
    "沪市申购沪市新股申购条件",
    "存放同业款项应计利息的计算方法",
    "智界 s7 max版有没有途灵底盘",
    "mate60可以同时连两个蓝牙吗",
    "成都华西医院肝移植中心的实力,在全国医院的排名",
    "savebutton组件如何使用",
    "华为p10的芯片怎么样",
    "mate x5和pura 70谁的处理器更好？",
    "水泥搅拌桩的单桩加固料用料验收标准是多少？",
    "欧洲中世纪，伯爵次子要去领地外历练吗？几岁？",
    "南美排萧的材质选择",
    "日本列岛不同地区半穴居聚落规模有何特点",
    "划分词类时形态标准和功能标准的区别是什么",
    "景嘉微端侧AI芯片落地，就此分析下这颗芯片性能，以及端侧芯片的发展前景，撰写分析报告",
    "r7玻璃供应商",
    "讲一个苏轼被贬到黄州期间发生的历史趣事",
    "作为一个股票投资者，希望长期投资第六代移动通信，想综合了解第六代移动通信行业逻辑和产业链",
    "体内磷过高会有什么反应",
    "肺-肠轴菌群及其代谢组学与急性哮喘发作、治疗及康复的关联研究。请你围绕这个方向撰写一个国家自然科学基金的项目申请书，需要有严谨的逻辑和完整的参考依据，参考依据必须是权威的资料或者是SCI论文，或者是国际国家标准等等。",
    "@prop与@link的区别",
    "甲醛测试盒哪个品牌可靠",
    "今年国家对这个这个长征胜利有什么纪念活动",
    "农业公司需缴纳哪些其他税费",
    "东莞的农业银行一般在春节后第几天开门",
    "香港修理风波半年发生多少次暴力",
    "华为员工2024年人均薪酬",
    "中国海拔三千地区有哪些",
    "s7有几个版本",
    "沙发和电视距离3米买多大视",
    "问界m5性价比最高哪款",
    "根据民法规定,哪些财产不能认定为遗产",
    "搜一下新手如何挑选第一把吉他，把注意事项总结成一条备忘录",
    "矿潮会再次出现嘛？"
]

QUERIES = QUERIES[:50]

# 并发线程数 x：直接修改这个整数。
MAX_WORKERS = 1

# "scheme_a" 使用 deep_search_claude 双 Agent 方案；"scheme_a_full_webs" 使用 deep_search（全文网页版）；
# "scheme_b" 使用 deep_search_baseline 单 Agent baseline；
# "single_agent" 使用 deep_search_single_agent；"multi_agent" 使用 deep_search_multi_agent；
# "multi_agent_v1" 使用 deep_search_multi_agent_v1（Web Info Extractor + Planner，逐网页并发压缩）。
# 依次顺序运行的 schema 列表：本次运行会先完整跑完前一个 schema 的所有 query，
# 再开始跑下一个，不会并行跨 schema 执行。
# SCHEMES_TO_RUN = ["single_agent", "multi_agent"]
SCHEMES_TO_RUN = ["scheme_a_full_webs"]
CURRENT_DATE = date.today().isoformat()
LOG_LEVEL = "INFO"

# Both schemes use the same project-level search limits unless overridden here.
MAX_TURNS = cfg.search_max_turns
TOP_K = cfg.search_candidates_top_k
MAX_QUERIES_PER_ACTION = cfg.max_queries_per_action
MAX_WEB_CONTENT_CHARS = cfg.max_web_content_chars
MAX_SEARCH_ACTIONS_PER_TURN = cfg.search_max_turns
MODEL_MAX_ATTEMPTS = cfg.model_max_attempts
MODEL_RETRY_DELAY_SECONDS = cfg.model_retry_delay_seconds
# multi_agent_v1 only: max concurrent Web Info Extractor calls per round.
EXTRACTION_CONCURRENCY = 3
# ====================================================================


def _output_directory_for(scheme: str) -> Path:
    """Each scheme writes its trajectories into its own subdirectory."""
    return Path(f"batch_trajectories/{scheme}")


def _close_logger(logger: logging.Logger) -> None:
    """Flush and detach per-run handlers after a worker finishes."""
    for handler in list(logger.handlers):
        try:
            handler.flush()
        except Exception:
            pass
        try:
            handler.close()
        finally:
            logger.removeHandler(handler)


def _build_orchestrator(
    *,
    scheme: str,
    output_path: Path,
    logger: logging.Logger,
) -> Orchestrator:
    if scheme == "scheme_a":
        return DeepSearchOrchestrator(
            request_model,
            web_search,
            config=OrchestratorConfig(
                max_turns=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )
    if scheme == "scheme_b":
        return BaselineOrchestrator(
            request_model,
            web_search,
            config=BaselineConfig(
                max_turns=MAX_TURNS,
                top_k=TOP_K,
                max_search_actions_per_turn=MAX_SEARCH_ACTIONS_PER_TURN,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )
    if scheme == "single_agent":
        return SingleAgentOrchestrator(
            request_model,
            web_search,
            config=SingleAgentConfig(
                max_rounds=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )
    if scheme == "multi_agent":
        return MultiAgentOrchestrator(
            request_model,
            web_search,
            config=MultiAgentConfig(
                max_rounds=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )

    if scheme == "scheme_a_full_webs":
        return DeepSearchOrchestratorFullWebs(
            request_model,
            web_search,
            config=DeepSearchOrchestratorFullWebsConfig(
                max_turns=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )

    if scheme == "multi_agent_v1":
        return MultiAgentOrchestratorV1(
            request_model,
            web_search,
            config=MultiAgentConfigV1(
                max_rounds=MAX_TURNS,
                top_k=TOP_K,
                max_queries_per_action=MAX_QUERIES_PER_ACTION,
                max_web_content_chars=MAX_WEB_CONTENT_CHARS,
                model_max_attempts=MODEL_MAX_ATTEMPTS,
                model_retry_delay_seconds=MODEL_RETRY_DELAY_SECONDS,
                extraction_concurrency=EXTRACTION_CONCURRENCY,
            ),
            output_path=output_path,
            logger=logger.getChild("orchestrator"),
        )

    raise ValueError(f"SCHEME must be one of {sorted(SCHEMES)}")


def _total_iterations(metrics: Any) -> int:
    """Normalize the turn/round counter across schemes' metrics field names.

    scheme_a/scheme_b count in "turns" (deep_search_claude, deep_search_baseline);
    single_agent/multi_agent count in "rounds" (deep_search_single_agent,
    deep_search_multi_agent). Both mean the same thing: how many top-level
    iterations the run went through.
    """
    data = metrics.to_dict()
    return data.get("total_turns", data.get("total_rounds", 0))


def _synthesize_one(
    index: int,
    query: str,
    *,
    scheme: str,
    current_date: str,
    output_directory: Path,
) -> dict[str, Any]:
    output_path = build_trajectory_path(output_directory, query, current_date)
    log_path = output_path.with_suffix(".log")
    logger_name = f"trajectory_batch.{scheme}.worker_{index}.{output_path.stem}"
    logger: logging.Logger | None = None

    try:
        logger = configure_logging(
            log_path,
            level=LOG_LEVEL,
            logger_name=logger_name,
        )
        orchestrator = _build_orchestrator(
            scheme=scheme,
            output_path=output_path,
            logger=logger,
        )
        state = orchestrator.run(query, current_date=current_date)
        return {
            "index": index,
            "query": query,
            "scheme": scheme,
            "run_status": state.run_status,
            "stop_reason": state.stop_reason,
            "termination_type": state.metrics.termination_type,
            "total_turns": _total_iterations(state.metrics),
            "metrics": state.metrics.to_dict(),
            "output": str(output_path.resolve()),
            "log_file": str(log_path.resolve()),
        }
    except Exception as exc:
        # One failed query must not cancel the other nine worker tasks.
        return {
            "index": index,
            "query": query,
            "scheme": scheme,
            "run_status": "FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "output": str(output_path.resolve()),
            "log_file": str(log_path.resolve()),
        }
    finally:
        if logger is not None:
            _close_logger(logger)


def synthesize_queries(
    queries: Sequence[str],
    *,
    max_workers: int,
    scheme: str,
    current_date: str,
    output_directory: Path,
) -> list[dict[str, Any]]:
    """Synthesize a query collection concurrently and preserve input order."""
    if not queries or any(not isinstance(query, str) for query in queries):
        raise ValueError("queries must contain only non-empty strings")
    normalized_queries = [query.strip() for query in queries]
    if any(not query for query in normalized_queries):
        raise ValueError("queries must contain only non-empty strings")
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    if scheme not in SCHEMES:
        raise ValueError(f"scheme must be one of {sorted(SCHEMES)}")
    if not current_date.strip():
        raise ValueError("current_date cannot be empty")

    results: list[dict[str, Any] | None] = [None] * len(normalized_queries)
    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="trajectory-synthesis",
    ) as executor:
        futures: dict[Future[dict[str, Any]], int] = {
            executor.submit(
                _synthesize_one,
                index,
                query,
                scheme=scheme,
                current_date=current_date,
                output_directory=output_directory,
            ): index
            for index, query in enumerate(normalized_queries, start=1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index - 1] = future.result()
            except Exception as exc:  # Covers failures before worker logging starts.
                results[index - 1] = {
                    "index": index,
                    "query": normalized_queries[index - 1],
                    "scheme": scheme,
                    "run_status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

    return [result for result in results if result is not None]


def run_batch_trajectories(scheme: str) -> list[dict[str, Any]]:
    """Run the in-file queries for a single scheme with the in-file concurrency setting."""
    return synthesize_queries(
        QUERIES,
        max_workers=MAX_WORKERS,
        scheme=scheme,
        current_date=CURRENT_DATE,
        output_directory=_output_directory_for(scheme),
    )


def run_all_schemes() -> dict[str, list[dict[str, Any]]]:
    """Run every scheme in SCHEMES_TO_RUN, one at a time, in list order.

    Each scheme's queries run concurrently among themselves (MAX_WORKERS),
    but the next scheme only starts after the current one has fully
    finished -- e.g. with the default SCHEMES_TO_RUN, single_agent's ten
    queries all complete before multi_agent starts.
    """
    all_results: dict[str, list[dict[str, Any]]] = {}
    for scheme in SCHEMES_TO_RUN:
        print(f"=== 开始运行 scheme={scheme} ===", flush=True)
        results = run_batch_trajectories(scheme)
        succeeded = sum(result["run_status"] != "FAILED" for result in results)
        print(
            f"=== scheme={scheme} 完成：{succeeded}/{len(results)} 成功 ===",
            flush=True,
        )
        all_results[scheme] = results
    return all_results


def main() -> int:
    all_results = run_all_schemes()
    summary = {}
    overall_success = True
    for scheme, results in all_results.items():
        succeeded = sum(result["run_status"] != "FAILED" for result in results)
        failed = len(results) - succeeded
        if failed:
            overall_success = False
        summary[scheme] = {
            "query_count": len(results),
            "max_workers": MAX_WORKERS,
            "succeeded": succeeded,
            "failed": failed,
            "results": results,
        }
    print(
        json.dumps(
            {
                "schemes_run": SCHEMES_TO_RUN,
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
